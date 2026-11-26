"""分发回执办理流的语义接口与路径别名。

主契约仍然是统一资源路由 ``POST /api/{module}/{id}/actions``；
这里额外提供 ``.../continue``、``.../terminate``、``.../claim`` 等
可读 REST 路径以及常见命名别名，降低不同用例拼装 URL 的成本。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.services import dist_flow as flow
from app.services import service_for
from app.store import store

router = APIRouter(prefix="/api", tags=["分发回执办理流"])


async def _body_params(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    params: dict[str, Any] = {}
    if isinstance(data.get("values"), dict):
        params.update(data["values"])
    params.update({k: v for k, v in data.items() if k not in ("action", "values")})
    if data.get("remark") is not None:
        params.setdefault("remark", data["remark"])
    return params


def _dispatch(module: str, entry_id: int, action: str, params: dict[str, Any]):
    service = service_for(module)
    if service is None:
        return {"ok": False, "message": f"未知模块 {module}"}
    return service.dispatch_action(entry_id, action, params)


# ---- 回执中心：语义动作段 ----

_RECEIPT_ACTIONS = {
    "continue": "继续办理", "detail": "继续办理", "verify": "服务端核对",
    "signoff": "签收", "sign": "签收", "resign": "补签",
    "terminate": "终止", "close": "终止",
    "resume": "继续发出", "send": "继续发出",
    "exception": "提交异常编号", "handoff": "按原单补寄",
    "reconcile": "对账", "reconciliation": "对账",
    "resend": "重发回执请求", "diff": "标记差异",
}

_TASK_ACTIONS = {
    "claim": "领取", "receive": "领取",
    "deliver": "确认送达", "delivery": "确认送达", "confirm": "确认送达",
    "reconcile": "核对实际送达", "check": "核对实际送达",
}


@router.get("/dist_receipt/{entry_id}/continue")
@router.get("/dist_receipt/{entry_id}/detail")
@router.get("/dist_receipt/{entry_id}/verify")
def receipt_continue_get(entry_id: int) -> dict[str, Any]:
    return flow.continue_application(receipt_id=entry_id)


@router.get("/dist_task/{entry_id}/reconcile")
@router.get("/dist_task/{entry_id}/detail")
def task_detail_get(entry_id: int) -> dict[str, Any]:
    from app.store import store
    if entry_id and store.find("dist_task", entry_id) is not None:
        return flow.reconcile_task_with_receipts(entry_id)
    return {"ok": False, "message": "分发批次不存在"}


@router.post("/dist_receipt/{entry_id}/{action_name}", response_model=None)
async def receipt_action(entry_id: int, action_name: str, request: Request):
    params = await _body_params(request)
    action = _RECEIPT_ACTIONS.get(action_name, action_name)
    return _dispatch("dist_receipt", entry_id, action, params)


@router.post("/dist_task/{entry_id}/{action_name}", response_model=None)
async def task_action(entry_id: int, action_name: str, request: Request):
    params = await _body_params(request)
    action = _TASK_ACTIONS.get(action_name, action_name)
    return _dispatch("dist_task", entry_id, action, params)


def _resolve_receipt_id(params: dict[str, Any]) -> int | None:
    rid = _as_id(params.get("receipt_id") or params.get("entry_id") or params.get("id"))
    if rid is not None:
        return rid
    batch = str(params.get("分发编号") or params.get("分发批次") or "")
    if batch:
        rc = next((r for r in store.rows("dist_receipt")
                   if r.get("分发编号") == batch or r.get("分发批次") == batch), None)
        if rc is not None:
            return int(rc["id"])
    return None


@router.post("/dist_receipt/{action_name}", response_model=None)
async def receipt_action_idless(action_name: str, request: Request):
    params = await _body_params(request)
    rid = _resolve_receipt_id(params)
    if rid is None:
        return {"ok": False, "message": "请在 body 中提供 id / receipt_id 或 分发编号"}
    action = _RECEIPT_ACTIONS.get(action_name, action_name)
    return _dispatch("dist_receipt", rid, action, params)


@router.post("/dist_task/{action_name}", response_model=None)
async def task_action_idless(action_name: str, request: Request):
    params = await _body_params(request)
    tid = _as_id(params.get("task_id") or params.get("entry_id") or params.get("id"))
    if tid is None:
        batch = str(params.get("分发编号") or params.get("分发批次") or "")
        if batch:
            from app.store import store
            t = next((x for x in store.rows("dist_task")
                      if x.get("分发编号") == batch or x.get("分发批次") == batch), None)
            tid = int(t["id"]) if t else None
    if tid is None:
        return {"ok": False, "message": "请在 body 中提供 id / task_id 或 分发编号"}
    action = _TASK_ACTIONS.get(action_name, action_name)
    return _dispatch("dist_task", tid, action, params)


# ---- 申请单维度：/api/resend_apply/{id}/terminate 等 ----

_APPLY_DIRECT = {
    "continue": lambda i, p: flow.continue_application(i),
    "detail": lambda i, p: flow.continue_application(i),
    "terminate": lambda i, p: flow.terminate_application(i, reason=str(p.get("reason") or p.get("remark") or p.get("处理原因") or "")),
    "resume": lambda i, p: flow.resume_application(i),
    "exception": lambda i, p: flow.submit_exception(i, exception_no=str(p.get("异常编号") or p.get("exception_no") or p.get("no") or "")),
    "handoff": lambda i, p: flow.handoff_to_dist_task(i, exception_no=str(p.get("异常编号") or p.get("exception_no") or "")),
}


@router.get("/resend_apply/{apply_id}/continue")
@router.get("/resend_apply/{apply_id}/detail")
def apply_detail(apply_id: int):
    return flow.continue_application(apply_id)


@router.post("/resend_apply/{apply_id}/{action_name}", response_model=None)
async def apply_action(apply_id: int, action_name: str, request: Request):
    params = await _body_params(request)
    handler = _APPLY_DIRECT.get(action_name)
    if handler is None:
        # 退回到申请资源的通用动作分派
        return _dispatch("resend_apply", apply_id, action_name, params)
    return handler(apply_id, params)


# ---- 顶层宽松分派：/api/dist_flow/{action}，body 带 id ----

_TOP_ACTIONS = {
    "continue": lambda p: flow.continue_application(_as_id(p.get("apply_id") or p.get("id")), receipt_id=_as_id(p.get("receipt_id") or p.get("entry_id"))),
    "signoff": lambda p: flow.submit_signoff(**_signoff_kwargs(p)),
    "terminate": lambda p: flow.terminate_application(_as_id(p.get("apply_id")), receipt_id=_as_id(p.get("receipt_id") or p.get("entry_id")), reason=str(p.get("reason") or p.get("处理原因") or "")),
    "exception": lambda p: flow.submit_exception(_as_id(p.get("apply_id")), receipt_id=_as_id(p.get("receipt_id") or p.get("entry_id")), exception_no=str(p.get("异常编号") or p.get("exception_no") or "")),
    "handoff": lambda p: flow.handoff_to_dist_task(_as_id(p.get("apply_id")), receipt_id=_as_id(p.get("receipt_id") or p.get("entry_id"))),
    "claim": lambda p: flow.claim_approved_items(int(p.get("task_id") or p.get("id"))),
    "deliver": lambda p: flow.confirm_replacement_delivery(int(p.get("task_id") or p.get("id"))),
    "reconcile": lambda p: flow.reconcile_task_with_receipts(int(p.get("task_id") or p.get("id"))),
}


@router.post("/dist_flow/{action_name}", response_model=None)
async def top_action(action_name: str, request: Request):
    params = await _body_params(request)
    handler = _TOP_ACTIONS.get(action_name)
    if handler is None:
        return {"ok": False, "message": f"未知办理动作 {action_name}"}
    return handler(params)


def _as_id(value: Any) -> int | None:
    if value in (None, "", "None"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _signoff_kwargs(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "batch": str(p.get("batch") or p.get("分发编号") or p.get("分发批次") or ""),
        "result": str(p.get("result") or p.get("签收结果") or flow.SUCCESS),
        "subscriber": str(p.get("subscriber") or p.get("订阅方") or ""),
        "scope": str(p.get("scope") or p.get("签收范围") or "整批"),
        "count": p.get("count") or p.get("记录数"),
        "source": str(p.get("source") or p.get("来源") or "订阅方"),
        "reason": str(p.get("reason") or p.get("处理原因") or p.get("remark") or ""),
        "damaged": p.get("damaged") or p.get("破损数"),
        "items": p.get("items") or p.get("条目"),
        "idem_key": p.get("idem_key") or p.get("幂等键"),
    }
