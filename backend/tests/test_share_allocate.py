"""共享源采集分摊领域逻辑测试（仅依赖标准库：python -I -m unittest）。

覆盖：
  1. 物理来源只计一次、归属记录允许重复；
  2. 三种口径（full/split/primary）的采集量与有效分钟；
  3. 交集记录标识保留、原始样本编号可追溯；
  4. 整体采集量按唯一原始样本重算，分部门合计与去重口径的关系；
  5. 共享覆盖时间与去重前后差值；
  6. 部分重叠时段的分钟裁剪。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.share_allocate import CALIBER_LABELS, ShareAllocateService  # noqa: E402

START = "2026-11-20T20:00:00"
END = "2026-11-20T21:30:00"


class ShareAllocateTest(unittest.TestCase):
    def setUp(self) -> None:
        # 每个用例拿到全新的内存仓库，避免状态互相污染。
        # 服务里以 `from app.store import store` 持有引用，因此直接重置该对象内部表。
        from app.seed import SEED_ROWS
        from app.store import store

        store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
        self.service = ShareAllocateService()

    # ------------------------------------------------------------ 事实基线
    def test_seed_baseline_unique_samples(self) -> None:
        samples = self.service.list_samples()
        sample_ids = [row["样本编号"] for row in samples]
        self.assertEqual(len(sample_ids), len(set(sample_ids)), "样本编号必须全局唯一")
        self.assertEqual(sum(int(row["采集量"]) for row in samples), 10)
        physical_ids = {row["物理来源标识"] for row in self.service.list_sources()}
        self.assertEqual(len(physical_ids), 4, "两队都可查看的来源只计为一个物理来源")

    # ------------------------------------------------------------ full 考核口径
    def test_full_caliber_double_counts_intersection_but_keeps_marker(self) -> None:
        rows = self.service.build_allocation(caliber="full", start=START, end=END)
        shared = [r for r in rows if r["是否交集"]]
        self.assertTrue(shared, "应识别出权限边界重合的交集窗口")
        for row in shared:
            self.assertIsNotNone(row["交集记录标识"])
            self.assertGreaterEqual(row["持有团队数"], 2)
            self.assertTrue(row["原始样本编号"], "交集行必须保留原始样本编号用于追溯")
            self.assertEqual(
                row["分摊采集量"], row["物理采集量"],
                "考核口径下重叠十分钟在各自考核报表中全额体现",
            )
        recon = self.service.build_reconciliation(caliber="full", start=START, end=END)
        # 去重前 = 分部门加总；去重后 = 物理事实；差值来自交集窗口的重复归属
        # （SRC-01 共享窗 2+1=3，SRC-02 共享窗 1+1=2，合计重复 5）。
        self.assertEqual(recon["去重前采集量"], 15)
        self.assertEqual(recon["全局去重采集量"], 10)
        self.assertEqual(recon["去重前后差值"], 5)
        self.assertEqual(recon["分部门分摊合计"], 15)

    def test_intersection_rows_are_kept_not_dropped(self) -> None:
        rows = self.service.build_allocation(caliber="full", start=START, end=END)
        # SRC-02 的 20:30、21:00 两个窗口两个团队同时持有 -> 每个窗口保留两行。
        src02 = [r for r in rows if r["来源编号"] == "SRC-02"]
        self.assertEqual(len(src02), 4)
        self.assertTrue(all(r["是否交集"] for r in src02))
        markers = {r["交集记录标识"] for r in src02}
        self.assertEqual(markers, {"SRC-02@20:30", "SRC-02@21:00"})

    # ------------------------------------------------------------ split 分摊口径
    def test_split_caliber_sums_to_dedup_total(self) -> None:
        rows = self.service.build_allocation(caliber="split", start=START, end=END)
        recon = self.service.build_reconciliation(caliber="split", start=START, end=END)
        self.assertEqual(
            sum(r["分摊采集量"] for r in rows),
            recon["全局去重采集量"],
            "拆分口径分部门之和必须恰好等于全局去重采集量",
        )
        self.assertEqual(recon["分部门分摊合计"], 10)
        self.assertTrue(recon["分部门合计与去重一致"])
        # 20:20 窗口 T1 有效 10 分钟、T2 有效 10 分钟，采集量 2 -> 各 1。
        window_2020 = [r for r in rows if r["来源编号"] == "SRC-01" and r["观测窗口开始"].endswith("20:20:00")]
        self.assertEqual(sorted(r["团队编号"] for r in window_2020), ["T1", "T2"])
        self.assertEqual([r["分摊采集量"] for r in window_2020], [1, 1])
        # 20:30 窗口 T1 有效 10、T2 有效 10，采集量 1 -> 余数按团队顺序补，T1 得 1、T2 得 0。
        window_2030 = [r for r in rows if r["来源编号"] == "SRC-01" and r["观测窗口开始"].endswith("20:30:00")]
        by_team = {r["团队编号"]: r["分摊采集量"] for r in window_2030}
        self.assertEqual(by_team, {"T1": 1, "T2": 0})

    # ------------------------------------------------------------ primary 主责口径
    def test_primary_caliber_assigns_intersection_to_owner(self) -> None:
        rows = self.service.build_allocation(caliber="primary", start=START, end=END)
        for row in rows:
            if row["是否交集"]:
                expected = row["物理采集量"] if row["是否主责"] else 0
                self.assertEqual(row["分摊采集量"], expected)
        recon = self.service.build_reconciliation(caliber="primary", start=START, end=END)
        self.assertEqual(recon["分部门分摊合计"], 10)
        self.assertTrue(recon["分部门合计与去重一致"])

    # ------------------------------------------------------------ 分钟裁剪
    def test_partial_overlap_minute_clipping(self) -> None:
        rows = self.service.build_allocation(caliber="full", start=START, end=END)
        # SRC-01 的 20:20 窗口：T2 归属从 20:20 开始，与窗口 [20:20,20:30) 完整相交 10 分钟。
        t2_2020 = next(r for r in rows if r["来源编号"] == "SRC-01" and r["团队编号"] == "T2"
                       and r["观测窗口开始"].endswith("20:20:00"))
        self.assertEqual(t2_2020["有效分钟数"], 10)
        # T1 在 20:00 独占窗口有效 10 分钟，且不是交集。
        t1_2000 = next(r for r in rows if r["来源编号"] == "SRC-01" and r["团队编号"] == "T1"
                       and r["观测窗口开始"].endswith("20:00:00"))
        self.assertEqual(t1_2000["有效分钟数"], 10)
        self.assertFalse(t1_2000["是否交集"])

    # ------------------------------------------------------------ 对账与重算
    def test_reconciliation_shared_coverage_and_recalc(self) -> None:
        recon = self.service.build_reconciliation(caliber="full", start=START, end=END)
        # 共享窗口：SRC-01@20:20、SRC-01@20:30、SRC-02@20:30、SRC-02@21:00 共 4 个。
        self.assertEqual(recon["共享窗口数"], 4)
        self.assertEqual(recon["共享覆盖分钟数"], 40)
        # 重叠归属分钟 = 交集窗口内各团队有效分钟之和（4 窗 × 2 团队 × 10 分钟）。
        self.assertEqual(recon["重叠归属分钟数"], 80)
        self.assertEqual(recon["物理来源数"], 4)
        self.assertTrue(recon["重算一致"])
        self.assertEqual(recon["按样本重算采集量"], recon["全局去重采集量"])
        self.assertEqual(recon["唯一原始样本数"], 9)
        # 归属记录数按归属时段表计（6 条），不按窗口展开；4 个物理来源 -> 差值 2。
        self.assertEqual(recon["归属记录数"], 6)
        self.assertEqual(recon["归属与物理来源差值"], 2)
        self.assertNotEqual(recon["归属记录数"], len(self.service.build_allocation(caliber="full", start=START, end=END)))

    def test_recalc_invariant_holds_for_every_caliber(self) -> None:
        for caliber in ("full", "split", "primary"):
            with self.subTest(caliber=caliber):
                recon = self.service.build_reconciliation(caliber=caliber, start=START, end=END)
                self.assertTrue(recon["重算一致"], f"{caliber} 口径下按唯一样本重算必须与去重采集量一致")
                self.assertEqual(recon["按样本重算采集量"], 10)

    # ------------------------------------------------------------ 分部门明细
    def test_department_details_and_team_filter(self) -> None:
        details = self.service.build_department_details(caliber="full", start=START, end=END)
        teams = {(d["团队编号"], d["来源编号"]) for d in details}
        # 6 条归属记录（含共享来源的重复归属），每条在明细里保留交集标识。
        self.assertEqual(len(details), 6)
        self.assertIn(("T1", "SRC-01"), teams)
        self.assertIn(("T2", "SRC-01"), teams)
        t1_only = self.service.build_department_details(caliber="full", team_id="T1", start=START, end=END)
        self.assertTrue(all(d["团队编号"] == "T1" for d in t1_only))
        # 明细里的样本并集必须可回溯，且共享来源不会把样本数翻倍。
        src01_t1 = next(d for d in details if d["团队编号"] == "T1" and d["来源编号"] == "SRC-01")
        self.assertEqual(src01_t1["原始样本数"], 3)

    # ------------------------------------------------------------ 报告与任务流转
    def test_report_bundle_uses_single_allocation_table(self) -> None:
        report = self.service.build_report(caliber="split", start=START, end=END)
        # 分部门明细与对账行必须来自同一张分摊表：分摊行数 = 归属 × 窗口裁剪结果。
        self.assertEqual(len(report["allocation"]), 13)
        self.assertEqual(len(report["department_details"]), 6)
        self.assertEqual(
            report["reconciliation"]["全局去重采集量"],
            report["reconciliation"]["按样本重算采集量"],
        )
        self.assertIn("口径说明", report["reconciliation"])
        self.assertEqual(report["reconciliation"]["口径说明"], CALIBER_LABELS["split"])

    def test_task_action_recalcs_and_snapshots_recon(self) -> None:
        task, message = self.service.run_action(1, "重新分摊", caliber="primary")
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "已生成")
        self.assertEqual(task["当前口径"], "primary")
        self.assertFalse(task["abnormal"])
        self.assertEqual(task["最近对账"]["全局去重采集量"], 10)
        self.assertIn("按唯一样本重算", message)

    def test_split_with_partial_minute_weight(self) -> None:
        # 临时把 T2 的全段归属换成只覆盖窗口后 5 分钟的归属，
        # 验证拆分按有效分钟占比而不是团队数均分。
        from app.store import store

        attrs = store.rows("share_attribution")
        original = next(row for row in attrs if row["归属编号"] == "ATTR-02")
        attrs.remove(original)
        attrs.append({
            'id': 99, 'status': '生效中', 'pending': False, 'abnormal': False,
            '归属编号': 'ATTR-99', '来源编号': 'SRC-01', '团队编号': 'T2',
            '开始时间': '2026-11-20T20:25:00', '结束时间': '2026-11-20T20:30:00',
            '主责团队': False,
        })
        rows = self.service.build_allocation(caliber="split", start=START, end=END)
        window = [r for r in rows
                  if r["来源编号"] == "SRC-01" and r["观测窗口开始"].endswith("20:20:00")]
        by_team = {r["团队编号"]: r for r in window}
        self.assertEqual(by_team["T1"]["有效分钟数"], 10)
        self.assertEqual(by_team["T2"]["有效分钟数"], 5)
        # 物理采集量 2，按 10:5 的分钟权重：T1 = 2*10//15 = 1，T2 = 2*5//15 = 0，
        # 余数 1 按团队编号顺序补给 T1 -> T1=2、T2=0，合计仍等于物理量。
        self.assertEqual(by_team["T1"]["分摊采集量"], 2)
        self.assertEqual(by_team["T2"]["分摊采集量"], 0)
        self.assertEqual(sum(r["分摊采集量"] for r in window), 2)
        self.assertTrue(all(r["是否交集"] for r in window))

    def test_invalid_caliber_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.build_allocation(caliber="unknown")

    def test_invalid_action_rejected(self) -> None:
        task, message = self.service.run_action(1, "删除任务")
        self.assertIsNone(task)
        self.assertIn("不属于共享源分摊可执行范围", message)


if __name__ == "__main__":
    unittest.main()
