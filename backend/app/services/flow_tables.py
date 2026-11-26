"""办理流新增数据表的只读 / 动作服务。

把 signoff / resend_apply / reconciliation / disposition_receipt /
delivery_record 等也挂到统一资源路由下，方便 E2E 与对账脚本直接按
``/api/<table>?分发编号=...`` 核对数量。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services import dist_flow as flow
from app.store import store


class FlowTableService(BaseModuleService):
    MODULE = ""
    ENTITY = "流程记录"
    # 放宽过滤：真正的字段集合以数据行本身为准（见 list_entries）
    LIST_FIELDS = [
        "分发编号", "申请编号", "回执编号", "确认编号", "处置回执编号",
        "订阅方", "签收结果", "status", "幂等键", "申请类型", "处置类型",
    ]
    REQUIRED_FIELDS: list[str] = []
    STATUS_ORDER: list[str] = []
    ACTION_RULES: dict[str, str] = {}

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(self.MODULE))
        for field, expected in (filters or {}).items():
            # 只要行里确实有这个键就参与过滤；不再局限于固定 LIST_FIELDS
            rows = [
                row for row in rows
                if field in row
                and expected.casefold() in str(row.get(field, "")).casefold()
            ]
        if keyword:
            needle = keyword.casefold()
            rows = [row for row in rows if needle in _row_text(row)]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def create_entry(self, values: dict[str, Any]):  # 少用于这些表
        rows = store.rows(self.MODULE)
        # 带幂等键的流程记录：重复 POST 不产生第二行
        key = values.get("幂等键")
        if key:
            existed = next((r for r in rows if r.get("幂等键") == key), None)
            if existed is not None:
                return existed, []
        entry = {"id": max((int(r.get("id", 0)) for r in rows), default=0) + 1}
        entry.update(values)
        rows.append(entry)
        return entry, []


class SignoffService(FlowTableService):
    MODULE = "signoff"
    ENTITY = "签收记录"

    def create_entry(self, values: dict[str, Any]):
        # 让直接 POST /api/signoff 也走统一幂等 + 对账确认逻辑，
        # 避免绕过领域规则插入重复签收。
        result = flow.submit_signoff(
            batch=str(values.get("分发编号") or values.get("分发批次") or ""),
            result=str(values.get("签收结果") or flow.SUCCESS),
            subscriber=str(values.get("订阅方") or ""),
            scope=str(values.get("签收范围") or "整批"),
            count=values.get("记录数") or values.get("count"),
            source=str(values.get("来源") or "订阅方补投"),
            reason=str(values.get("处理原因") or values.get("reason") or ""),
            damaged=values.get("破损数") or values.get("damaged"),
            items=values.get("条目") or values.get("items"),
            idem_key=values.get("幂等键"),
        )
        return result.get("signoff"), [] if result.get("ok") else [result.get("message")]


def _row_text(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for value in row.values():
        if isinstance(value, (list, dict)):
            continue
        parts.append(str(value))
    return " ".join(parts).casefold()


class ResendApplyService(FlowTableService):
    MODULE = "resend_apply"
    ENTITY = "重发/补签申请"

    def create_entry(self, values: dict[str, Any]):
        batch = str(values.get("分发编号") or values.get("分发批次") or "")
        if not batch:
            return None, ["缺少分发编号，无法建立重发 / 补签申请"]
        apply = flow.create_application(
            batch=batch,
            subscriber=str(values.get("订阅方") or ""),
            apply_type=str(values.get("申请类型") or "回执重发"),
            receipt_id=values.get("receipt_id") or values.get("回执id"),
            exception_no=str(values.get("异常编号") or values.get("exception_no") or ""),
            approved_items=values.get("获准条目列表") or values.get("获准条目"),
            approved_count=values.get("获准数量"),
            title=str(values.get("申请标题") or ""),
            apply_no=str(values.get("申请编号") or ""),
        )
        return apply, []

    def dispatch_action(self, entry_id: int, action: str,
                        params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        if store.find(self.MODULE, entry_id) is None:
            return {"ok": False, "message": f"申请 {entry_id} 不存在"}
        reason = str(params.get("reason") or params.get("remark")
                     or params.get("处理原因") or "")
        no = str(params.get("异常编号") or params.get("exception_no")
                 or params.get("exceptionNo") or params.get("编号") or "")
        if action in {"继续办理", "continue", "detail", "查看详情", "核对"}:
            return flow.continue_application(entry_id)
        if action in {"终止", "terminate", "终止申请", "不再重发"}:
            return flow.terminate_application(entry_id, reason=reason)
        if action in {"恢复", "resume", "继续发出"}:
            return flow.resume_application(entry_id)
        if action in {"提交异常编号", "exception", "提交编号"}:
            return flow.submit_exception(entry_id, exception_no=no or reason)
        if action in {"按原单补寄", "handoff", "移交分发任务", "交给分发任务"}:
            return flow.handoff_to_dist_task(entry_id, exception_no=no)
        return super().dispatch_action(entry_id, action, params)
