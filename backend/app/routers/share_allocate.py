"""共享源采集分摊接口。

在采集监控的来源登记之上，按来源样本与归属时段计算可共享指标，输出：
  * 共享源采集分摊表（团队 × 来源 × 十分钟窗口，含交集记录标识）
  * 分部门来源明细（采集报告使用）
  * 整体对账行（共享覆盖时间、全局去重前后差值、按唯一样本重算）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.share_allocate import CALIBERS, ShareAllocateService

router = APIRouter(prefix="/api/share_allocate", tags=["共享源采集分摊"])

service = ShareAllocateService()

LIST_FIELDS = ["任务编号", "统计周期开始", "统计周期结束", "当前口径", "最近生成时间"]
STATUSES = ["待重算", "已生成"]


def _check_caliber(caliber: str) -> None:
    if caliber not in CALIBERS:
        raise HTTPException(status_code=400, detail=f"口径仅支持：{', '.join(CALIBERS)}")


@router.get("/sources")
def list_sources() -> dict[str, Any]:
    """来源登记清单：同一物理来源只出现一次。"""
    items = service.list_sources()
    return {"module": "share_source", "total": len(items), "items": items}


@router.get("/teams")
def list_teams() -> dict[str, Any]:
    """团队与考核报表清单。"""
    items = service.list_teams()
    return {"module": "share_team", "total": len(items), "items": items}


@router.get("/attributions")
def list_attributions() -> dict[str, Any]:
    """归属时段清单：允许同一来源在同一时段归属多个团队。"""
    items = service.list_attributions()
    return {"module": "share_attribution", "total": len(items), "items": items}


@router.get("/samples")
def list_samples() -> dict[str, Any]:
    """唯一原始样本清单：整体采集量重算的唯一事实来源。"""
    items = service.list_samples()
    return {"module": "share_source_sample", "total": len(items), "items": items}


@router.get("/allocation")
def get_allocation(
    caliber: str = Query(default="full", description="full=考核全额各计 / split=按分钟拆分 / primary=主责承担"),
    team_id: str | None = Query(default=None, description="按团队编号过滤"),
    start: str | None = Query(default=None, description="统计周期开始 ISO 时间"),
    end: str | None = Query(default=None, description="统计周期结束 ISO 时间"),
) -> dict[str, Any]:
    """生成共享源采集分摊表：列明各团队权限内的有效分钟数与采集量。"""
    _check_caliber(caliber)
    items = service.build_allocation(caliber=caliber, team_id=team_id, start=start, end=end)
    return {"caliber": caliber, "total": len(items), "items": items}


@router.get("/department_details")
def get_department_details(
    caliber: str = Query(default="full"),
    team_id: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> dict[str, Any]:
    """分部门来源明细：采集报告按团队 × 来源汇总的口径。"""
    _check_caliber(caliber)
    items = service.build_department_details(caliber=caliber, team_id=team_id, start=start, end=end)
    return {"caliber": caliber, "total": len(items), "items": items}


@router.get("/reconciliation")
def get_reconciliation(
    caliber: str = Query(default="full"),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> dict[str, Any]:
    """整体对账行：共享覆盖时间、全局去重前后差值与按唯一样本重算结果。"""
    _check_caliber(caliber)
    return service.build_reconciliation(caliber=caliber, start=start, end=end)


@router.get("/report")
def get_shared_report(
    caliber: str = Query(default="full"),
    team_id: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> dict[str, Any]:
    """采集报告数据：基于同一张分摊表的分部门明细 + 整体对账行。"""
    _check_caliber(caliber)
    return service.build_report(caliber=caliber, team_id=team_id, start=start, end=end)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出分摊任务清单：返回全量数据。"""
    items, total = service.list_tasks(page=1, size=10000)
    return {"module": "share_allocate_task", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description="待重算、已生成"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """分摊任务分页列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_tasks(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{task_id}", response_model=dict)
def get_entry(task_id: int) -> dict:
    """读取单条分摊任务（含最近一次对账快照）；不存在时给出可读说明。"""
    entry = service.get_task(task_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"分摊任务 {task_id} 不存在或已归档")
    return entry


@router.post("/{task_id}/actions", response_model=ActionResult)
def run_action(task_id: int, payload: EntryPayload) -> ActionResult:
    """对分摊任务执行重新分摊；口径在 values.caliber 中指定，缺省沿用任务口径。"""
    action = str(payload.values.get("action") or "").strip()
    caliber = payload.values.get("caliber")
    entry, message = service.run_action(task_id, action, caliber=caliber)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
