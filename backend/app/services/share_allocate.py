"""共享源采集分摊业务规则。

领域事实（不可再分）：
  * ``share_source``        来源登记：一个物理来源只登记一次（物理来源标识唯一）。
  * ``share_attribution``   归属时段：同一来源可同时归属多个团队，允许时段重合，
                            重合不是前后交接，而是权限边界同时覆盖。
  * ``share_source_sample`` 原始样本：样本编号全局唯一，是整体采集量的唯一事实来源。

派生结果（随时可按口径重算）：
  * 共享源采集分摊表        团队 × 来源 × 十分钟窗口一行，保留交集记录标识与
                            原始样本编号清单，口径只决定采集量怎么分摊，
                            不产生、也不销毁任何原始样本。

口径（caliber）：
  * full     考核口径：权限内的分钟数与采集量各自全额计，重叠十分钟在两份考核
             报表中都出现，靠交集标识而不是删除来识别重复归属；
  * split    分摊口径：重叠窗口的采集量按各团队有效分钟占比拆分，分部门之和
             恰好等于全局去重采集量；
  * primary  主责口径：重叠窗口只计给主责团队，非主责团队该窗口计 0。

无论哪种口径，整体采集量都必须能用唯一原始样本编号重算并与去重口径对账。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE_TASK = "share_allocate_task"
MODULE_SOURCE = "share_source"
MODULE_TEAM = "share_team"
MODULE_ATTRIBUTION = "share_attribution"
MODULE_SAMPLE = "share_source_sample"

WINDOW_MINUTES = 10
CALIBERS = ("full", "split", "primary")
CALIBER_LABELS = {
    "full": "考核口径（全额各计）",
    "split": "分摊口径（按有效分钟占比拆分）",
    "primary": "主责口径（重叠归主责团队）",
}
STATUS_ORDER = ["待重算", "已生成"]
ACTION_RULES = {"重新分摊": "已生成"}


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def overlap_minutes(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> int:
    """两个半开区间 [start, end) 的重叠分钟数，不重叠为 0。"""
    left = max(start_a, start_b)
    right = min(end_a, end_b)
    return max(0, int((right - left).total_seconds() // 60))


def _sample_window(sample: dict[str, Any]) -> tuple[datetime, datetime]:
    return parse_ts(sample["观测开始"]), parse_ts(sample["观测结束"])


class ShareAllocateService:
    """共享源采集分摊表的计算、对账与报告组装。"""

    # ------------------------------------------------------------------ 基础数据
    def list_sources(self) -> list[dict[str, Any]]:
        return store.rows(MODULE_SOURCE)

    def list_teams(self) -> list[dict[str, Any]]:
        return store.rows(MODULE_TEAM)

    def list_attributions(self) -> list[dict[str, Any]]:
        return store.rows(MODULE_ATTRIBUTION)

    def list_samples(self) -> list[dict[str, Any]]:
        return store.rows(MODULE_SAMPLE)

    def _team_name(self, team_id: str) -> str:
        for team in self.list_teams():
            if team["团队编号"] == team_id:
                return str(team["团队名称"])
        return team_id

    def _source_name(self, source_id: str) -> str:
        for source in self.list_sources():
            if source["来源编号"] == source_id:
                return str(source["来源名称"])
        return source_id

    def _physical_id(self, source_id: str) -> str:
        for source in self.list_sources():
            if source["来源编号"] == source_id:
                return str(source["物理来源标识"])
        return source_id

    # ------------------------------------------------------------ 分摊表（核心）
    def build_allocation(
        self,
        *,
        caliber: str = "full",
        team_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        """生成共享源采集分摊表：一行 = 团队 × 来源 × 十分钟观测窗口。

        有效分钟数 = 团队归属时段与该观测窗口的交集裁剪结果；
        采集量按口径投影，原始样本编号清单与交集标识在任何口径下都原样保留。
        """
        if caliber not in CALIBERS:
            raise ValueError(f"未知分摊口径：{caliber}，可选 {', '.join(CALIBERS)}")
        range_start = parse_ts(start)
        range_end = parse_ts(end)

        # 1. 把原始样本按 (来源, 窗口起点) 归并，一个物理窗口只对应一份样本事实。
        grouped: dict[tuple[str, datetime], dict[str, Any]] = {}
        for sample in self.list_samples():
            w_start, w_end = _sample_window(sample)
            if range_start and w_end <= range_start:
                continue
            if range_end and w_start >= range_end:
                continue
            key = (sample["来源编号"], w_start)
            bucket = grouped.setdefault(key, {
                "window_start": w_start,
                "window_end": w_end,
                "volume": 0,
                "sample_ids": [],
            })
            bucket["volume"] += int(sample["采集量"])
            bucket["sample_ids"].append(sample["样本编号"])

        # 2. 归属时段与样本窗口做交集，得到团队 × 来源 × 窗口的分摊行。
        raw_rows: list[dict[str, Any]] = []
        for attr in self.list_attributions():
            if team_id and attr["团队编号"] != team_id:
                continue
            a_start = parse_ts(attr["开始时间"])
            a_end = parse_ts(attr["结束时间"])
            for (source_id, w_start), bucket in sorted(grouped.items()):
                if source_id != attr["来源编号"]:
                    continue
                valid_minutes = overlap_minutes(a_start, a_end, bucket["window_start"], bucket["window_end"])
                if valid_minutes <= 0:
                    continue
                raw_rows.append({
                    "source_id": source_id,
                    "team_id": attr["团队编号"],
                    "window_start": bucket["window_start"],
                    "window_end": bucket["window_end"],
                    "valid_minutes": valid_minutes,
                    "window_minutes": int((bucket["window_end"] - bucket["window_start"]).total_seconds() // 60),
                    "physical_volume": bucket["volume"],
                    "sample_ids": list(bucket["sample_ids"]),
                    "is_primary": bool(attr["主责团队"]),
                })

        # 3. 交集识别：同一 (来源, 窗口) 被多个团队持有的，每个团队都打上交集标识，
        #    保留交集记录标识 —— 不删除任何一份归属。
        holders_by_window: dict[tuple[str, datetime], list[str]] = {}
        for row in raw_rows:
            holders_by_window.setdefault((row["source_id"], row["window_start"]), []).append(row["team_id"])
        for row in raw_rows:
            key = (row["source_id"], row["window_start"])
            holders = sorted(holders_by_window[key])
            row["is_intersection"] = len(holders) > 1
            row["intersection_key"] = f"{row['source_id']}@{row['window_start'].strftime('%H:%M')}"
            row["shared_teams"] = holders
            row["holder_count"] = len(holders)

        # 4. 口径投影：只决定分摊采集量，不改动分钟数、样本清单与交集标识。
        for row in raw_rows:
            row["caliber"] = caliber
            row["caliber_label"] = CALIBER_LABELS[caliber]
            if not row["is_intersection"]:
                row["allocated_volume"] = row["physical_volume"]
            elif caliber == "full":
                # 考核口径：权限内全额各计，重叠十分钟在各自考核报表中都体现。
                row["allocated_volume"] = row["physical_volume"]
            elif caliber == "primary":
                row["allocated_volume"] = row["physical_volume"] if row["is_primary"] else 0
            else:
                row["allocated_volume"] = None  # split 需要整窗信息，下一步统一算

        if caliber == "split":
            window_groups: dict[tuple[str, datetime], list[dict[str, Any]]] = {}
            for row in raw_rows:
                window_groups.setdefault((row["source_id"], row["window_start"]), []).append(row)
            for group_rows in window_groups.values():
                shared = sorted(
                    (r for r in group_rows if r["is_intersection"]),
                    key=lambda r: r["team_id"],
                )
                if not shared:
                    continue
                total_minutes = sum(r["valid_minutes"] for r in shared)
                physical_volume = shared[0]["physical_volume"]
                # 按有效分钟占比拆分；余数按团队编号顺序逐份补 1，保证合计不丢不溢。
                allocated = [
                    r["valid_minutes"] * physical_volume // total_minutes for r in shared
                ]
                for idx in range(physical_volume - sum(allocated)):
                    allocated[idx % len(allocated)] += 1
                for r, value in zip(shared, allocated):
                    r["allocated_volume"] = value

        # 5. 输出稳定排序与展示字段。
        raw_rows.sort(key=lambda r: (r["window_start"], r["source_id"], r["team_id"]))
        result: list[dict[str, Any]] = []
        for seq, row in enumerate(raw_rows, start=1):
            result.append({
                "分摊行号": f"ALC-{seq:04d}",
                "来源编号": row["source_id"],
                "来源名称": self._source_name(row["source_id"]),
                "物理来源标识": self._physical_id(row["source_id"]),
                "团队编号": row["team_id"],
                "团队名称": self._team_name(row["team_id"]),
                "观测窗口开始": row["window_start"].isoformat(),
                "观测窗口结束": row["window_end"].isoformat(),
                "有效分钟数": row["valid_minutes"],
                "窗口分钟数": row["window_minutes"],
                "物理采集量": row["physical_volume"],
                "分摊采集量": row["allocated_volume"],
                "是否交集": row["is_intersection"],
                "交集记录标识": row["intersection_key"] if row["is_intersection"] else None,
                "共享团队": "、".join(row["shared_teams"]),
                "持有团队数": row["holder_count"],
                "是否主责": row["is_primary"],
                "原始样本编号": "、".join(row["sample_ids"]),
                "口径": row["caliber"],
                "口径说明": row["caliber_label"],
            })
        return result

    # ------------------------------------------------------------ 分部门来源明细
    def build_department_details(
        self,
        *,
        caliber: str = "full",
        team_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        """采集报告用：把分摊表按团队 × 来源汇总成部门来源明细。"""
        allocations = self.build_allocation(caliber=caliber, team_id=team_id, start=start, end=end)
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for row in allocations:
            key = (row["团队编号"], row["来源编号"])
            item = merged.setdefault(key, {
                "团队编号": row["团队编号"],
                "团队名称": row["团队名称"],
                "来源编号": row["来源编号"],
                "来源名称": row["来源名称"],
                "物理来源标识": row["物理来源标识"],
                "有效分钟数": 0,
                "分摊采集量": 0,
                "物理采集量": 0,
                "交集窗口数": 0,
                "交集记录标识": [],
                "原始样本编号": set(),
                "口径": row["口径"],
                "口径说明": row["口径说明"],
            })
            item["有效分钟数"] += row["有效分钟数"]
            item["分摊采集量"] += row["分摊采集量"]
            item["物理采集量"] += row["物理采集量"]
            if row["是否交集"]:
                item["交集窗口数"] += 1
                item["交集记录标识"].append(row["交集记录标识"])
            item["原始样本编号"].update(s for s in row["原始样本编号"].split("、") if s)

        details: list[dict[str, Any]] = []
        for item in sorted(merged.values(), key=lambda x: (x["团队编号"], x["来源编号"])):
            sample_ids = sorted(item.pop("原始样本编号"))
            item["原始样本编号"] = "、".join(sample_ids)
            item["原始样本数"] = len(sample_ids)
            item["交集记录标识"] = "、".join(item["交集记录标识"]) or None
            details.append(item)
        return details

    # ------------------------------------------------------------- 整体对账行
    def build_reconciliation(
        self,
        *,
        caliber: str = "full",
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        """整体对账：共享覆盖时间 + 全局去重前后差值，且按唯一样本重算。"""
        allocations = self.build_allocation(caliber=caliber, start=start, end=end)
        samples = self._filter_samples(start, end)

        # 物理窗口视角：同一来源同一窗口只有一份物理事实。
        physical_windows: dict[tuple[str, str], dict[str, Any]] = {}
        for row in allocations:
            key = (row["来源编号"], row["观测窗口开始"])
            window = physical_windows.setdefault(key, {
                "volume": row["物理采集量"],
                "holders": row["持有团队数"],
                "window_minutes": row["窗口分钟数"],
                "shared_minutes": 0,
            })
            if row["是否交集"]:
                window["shared_minutes"] += row["有效分钟数"]

        physical_source_ids = {row["物理来源标识"] for row in allocations}
        # 归属记录数按归属时段表统计：与统计周期有交集的归属时段条数，
        # 不按窗口展开 —— 归属可以重合，但它是权限登记，不是实际采集。
        attribution_count = self._count_attributions_in_range(start, end)
        shared_window_count = sum(1 for w in physical_windows.values() if w["holders"] > 1)
        # 共享覆盖时间：交集窗口的物理窗口时长，不按团队倍数累加。
        shared_cover_minutes = sum(
            w["window_minutes"] for w in physical_windows.values() if w["holders"] > 1
        )
        # 重叠归属分钟：各团队在交集窗口里的有效分钟之和（考核口径下会重复计的分钟）。
        overlap_attribution_minutes = sum(w["shared_minutes"] for w in physical_windows.values())

        physical_volume = sum(w["volume"] for w in physical_windows.values())
        allocated_total = sum(row["分摊采集量"] for row in allocations)
        # 去重前（考核口径分部门合计）= 各团队权限内物理量直接相加；去重后 = 物理窗口采集量相加。
        before_dedup = sum(row["物理采集量"] for row in allocations)
        dedup_diff = before_dedup - physical_volume

        unique_sample_ids = {s["样本编号"] for s in samples}
        recalc_volume = sum(int(s["采集量"]) for s in samples)

        return {
            "口径": caliber,
            "口径说明": CALIBER_LABELS[caliber],
            "统计周期开始": start,
            "统计周期结束": end,
            "物理来源数": len(physical_source_ids),
            "归属记录数": attribution_count,
            "归属与物理来源差值": attribution_count - len(physical_source_ids),
            "物理观测窗口数": len(physical_windows),
            "共享窗口数": shared_window_count,
            "共享覆盖分钟数": shared_cover_minutes,
            "重叠归属分钟数": overlap_attribution_minutes,
            "考核口径采集量": before_dedup,
            "去重前采集量": before_dedup,
            "全局去重采集量": physical_volume,
            "去重前后差值": dedup_diff,
            "分部门分摊合计": allocated_total,
            "唯一原始样本数": len(unique_sample_ids),
            "按样本重算采集量": recalc_volume,
            "重算口径样本编号": "、".join(sorted(unique_sample_ids)),
            "重算一致": recalc_volume == physical_volume,
            "分部门合计与去重一致": allocated_total == physical_volume if caliber != "full" else None,
        }

    def _count_attributions_in_range(self, start: str | None, end: str | None) -> int:
        range_start = parse_ts(start)
        range_end = parse_ts(end)
        count = 0
        for attr in self.list_attributions():
            a_start = parse_ts(attr["开始时间"])
            a_end = parse_ts(attr["结束时间"])
            if range_start and a_end <= range_start:
                continue
            if range_end and a_start >= range_end:
                continue
            count += 1
        return count

    def _filter_samples(self, start: str | None, end: str | None) -> list[dict[str, Any]]:
        range_start = parse_ts(start)
        range_end = parse_ts(end)
        result = []
        for sample in self.list_samples():
            w_start, w_end = _sample_window(sample)
            if range_start and w_end <= range_start:
                continue
            if range_end and w_start >= range_end:
                continue
            result.append(sample)
        return result

    # ------------------------------------------------------------- 报告组装
    def build_report(
        self,
        *,
        caliber: str = "full",
        team_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        """采集报告：分部门来源明细 + 整体对账行，全部基于同一张分摊表。"""
        return {
            "caliber": caliber,
            "caliber_label": CALIBER_LABELS[caliber],
            "team_id": team_id,
            "start": start,
            "end": end,
            "allocation": self.build_allocation(caliber=caliber, team_id=team_id, start=start, end=end),
            "department_details": self.build_department_details(caliber=caliber, team_id=team_id, start=start, end=end),
            "reconciliation": self.build_reconciliation(caliber=caliber, start=start, end=end),
        }

    # ------------------------------------------------------------- 任务状态流转
    def list_tasks(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE_TASK)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        begin = max(page - 1, 0) * size
        return rows[begin:begin + size], total

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        return store.find(MODULE_TASK, task_id)

    def run_action(self, task_id: int, action: str, *, caliber: str | None = None) -> tuple[dict[str, Any] | None, str]:
        task = store.find(MODULE_TASK, task_id)
        if task is None:
            return None, f"分摊任务 {task_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于共享源分摊可执行范围"
        chosen = caliber or task.get("当前口径") or "full"
        if chosen not in CALIBERS:
            return None, f"未知分摊口径：{chosen}，可选 {', '.join(CALIBERS)}"
        report = self.build_report(
            caliber=chosen,
            start=task.get("统计周期开始"),
            end=task.get("统计周期结束"),
        )
        recon = report["reconciliation"]
        task["status"] = ACTION_RULES[action]
        task["pending"] = False
        task["abnormal"] = not recon["重算一致"]
        task["当前口径"] = chosen
        task["最近生成时间"] = datetime.now().replace(microsecond=0).isoformat()
        task["最近对账"] = recon
        return task, (
            f"共享源采集分摊表已按{CALIBER_LABELS[chosen]}重新分摊："
            f"物理来源 {recon['物理来源数']} 个，共享窗口 {recon['共享窗口数']} 个，"
            f"去重前 {recon['去重前采集量']} / 去重后 {recon['全局去重采集量']}，"
            f"按唯一样本重算 {recon['按样本重算采集量']}（{'一致' if recon['重算一致'] else '不一致'}）"
        )
