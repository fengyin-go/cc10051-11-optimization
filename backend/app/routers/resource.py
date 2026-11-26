"""统一资源接口：查询、导出、登记和动作按模块 key 分派到领域服务。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services import service_for

router = APIRouter(prefix="/api", tags=["业务资源"])


def require_service(module: str):
    service = service_for(module)
    if service is None:
        raise HTTPException(status_code=404, detail=f"未知业务模块：{module}")
    return service


@router.get("/{module}", response_model=PageResult[dict])
def list_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    service = require_service(module)
    reserved = {"keyword", "status", "page", "size"}
    # 除分页 / 关键字 / 状态外的查询参数都作为字段过滤透传，
    # 是否真正生效由领域服务按数据行键裁决。
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key not in reserved and value.strip()
    }
    items, total = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{module}/export")
def export_entries(
    module: str,
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    service = require_service(module)
    reserved = {"keyword", "status", "page", "size"}
    filters = {
        key: value.strip()
        for key, value in request.query_params.items()
        if key not in reserved and value.strip()
    }
    items, _ = service.list_entries(
        filters=filters, keyword=keyword, status=status, page=1, size=1_000_000)
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *service.LIST_FIELDS, "status"])
    for item in items:
        values = [item.get("id"), *(item.get(field, "") for field in service.LIST_FIELDS),
                  item.get("status", "")]
        writer.writerow([
            "'" + str(value) if str(value or "").startswith(("=", "+", "-", "@")) else value
            for value in values
        ])
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{module}.csv"'},
    )


@router.get("/{module}/{entry_id}", response_model=dict)
def get_entry(module: str, entry_id: int) -> dict[str, Any]:
    service = require_service(module)
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"{service.ENTITY} {entry_id} 不存在或已归档")
    return entry


@router.post("/{module}", response_model=ActionResult)
def create_entry(module: str, payload: EntryPayload) -> ActionResult:
    service = require_service(module)
    values = dict(payload.values or {})
    # 兼容把字段直接平铺在 body 顶层的用例：合并进 values（不覆盖显式 values）
    for key, value in (payload.model_extra or {}).items():
        values.setdefault(key, value)
    entry, missing = service.create_entry(values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"{service.ENTITY}已登记", entry=entry)


@router.post("/{module}/{entry_id}/actions", response_model=None)
def run_action(module: str, entry_id: int, payload: ActionPayload) -> dict[str, Any]:
    service = require_service(module)
    params: dict[str, Any] = {}
    if payload.values:
        params.update(payload.values)
    if payload.remark is not None:
        params.setdefault("remark", payload.remark)
    extra = payload.model_extra or {}
    params.update({k: v for k, v in extra.items() if v is not None})
    result = service.dispatch_action(entry_id, payload.action.strip(), params)
    # 统一保证 ok / message / entry 三要素存在（entry 缺失时才回查，避免无谓副作用）
    result.setdefault("ok", True)
    result.setdefault("message", "")
    if result.get("entry") is None:
        result["entry"] = service.get_entry(entry_id)
    return result
