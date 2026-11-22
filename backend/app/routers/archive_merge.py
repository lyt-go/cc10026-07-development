"""归档下架清册与归并任务接口。

通用资源路由只覆盖模块条目的查询/登记/动作；过期导出的归档下架清册和
数据归并的取数任务是跨模块执行单，单独在此暴露：
- GET  /api/archive/roster            生成并查看可执行的归档下架清册
- POST /api/archive/execute           执行整份下架清册
- POST /api/archive/exports/{id}      下架单份到期导出文件
- GET  /api/merge/sources             查看当前具备取数资格的归并输入
- GET  /api/merge/tasks               查看归并任务（含读取/受阻状态）
- POST /api/merge/tasks               登记归并任务（拒绝过期来源）
- GET  /api/merge/tasks/{id}          查看单个归并任务
- POST /api/merge/tasks/{id}/start    读取主侧来源
- POST /api/merge/tasks/{id}/secondary 读取从侧来源并尝试交付
- GET  /api/merge/tasks/{id}/delivery 值班核对能否交付、需补哪项来源
- POST /api/merge/tasks/{id}/recover   补来源恢复受阻任务
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import service_for

router = APIRouter(prefix="/api", tags=["归档下架与数据归并"])


class RegisterMergePayload(BaseModel):
    rule_id: int
    primary_export_id: int
    secondary_export_id: int


class RecoverPayload(BaseModel):
    side: str = Field(description="primary 或 secondary")
    replacement_export_id: int


def _require(ok: bool, payload: dict[str, Any]) -> dict[str, Any]:
    if not ok:
        raise HTTPException(status_code=400, detail=payload.get("message", "操作失败"))
    return payload


# ---------- 归档下架清册 ----------


@router.get("/archive/roster")
def get_roster() -> dict[str, Any]:
    service = service_for("export_task")
    return service.build_roster()


@router.post("/archive/execute")
def execute_roster() -> dict[str, Any]:
    service = service_for("export_task")
    return service.execute_roster()


@router.post("/archive/exports/{export_id}")
def archive_one(export_id: int) -> dict[str, Any]:
    service = service_for("export_task")
    result = service.archive_one(export_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "下架失败"))
    return result


# ---------- 归并任务 ----------


@router.get("/merge/sources")
def merge_sources() -> dict[str, Any]:
    service = service_for("data_merge")
    items = service.list_available_sources()
    return {"items": items, "total": len(items)}


@router.get("/merge/tasks")
def merge_tasks() -> dict[str, Any]:
    service = service_for("data_merge")
    items = service.list_merge_tasks()
    return {"items": items, "total": len(items)}


@router.post("/merge/tasks")
def register_merge_task(payload: RegisterMergePayload) -> dict[str, Any]:
    service = service_for("data_merge")
    result = service.register_merge_task(
        payload.rule_id, payload.primary_export_id, payload.secondary_export_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@router.get("/merge/tasks/{task_id}")
def get_merge_task(task_id: int) -> dict[str, Any]:
    service = service_for("data_merge")
    task = service.get_merge_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"归并任务 {task_id} 不存在")
    return task


@router.post("/merge/tasks/{task_id}/start")
def start_merge(task_id: int) -> dict[str, Any]:
    service = service_for("data_merge")
    result = service.start_merge(task_id)
    return _require(result.get("ok", False), result)


@router.post("/merge/tasks/{task_id}/secondary")
def read_secondary(task_id: int) -> dict[str, Any]:
    service = service_for("data_merge")
    result = service.read_secondary(task_id)
    return _require(result.get("ok", False), result)


@router.get("/merge/tasks/{task_id}/delivery")
def check_delivery(task_id: int) -> dict[str, Any]:
    service = service_for("data_merge")
    result = service.check_delivery(task_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


@router.post("/merge/tasks/{task_id}/recover")
def recover_source(task_id: int, payload: RecoverPayload) -> dict[str, Any]:
    service = service_for("data_merge")
    result = service.recover_source(task_id, payload.side, payload.replacement_export_id)
    return _require(result.get("ok", False), result)
