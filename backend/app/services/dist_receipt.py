"""分发回执中心领域服务：管理订阅方分发回执的回收核对、差异标记与重发请求。

数据链路位置：分发层对账端，负责消费分发批次的送达结果，
把回执差异回写给分发任务与订阅方。

本次增强：重发 / 补签申请的继续办理全部由服务端核对，
签收与对账确认幂等，处置开具可追查回执，处理原因挂回同一分发编号。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services import dist_flow as flow
from app.store import store


def _pick(params: dict[str, Any], *names: str) -> str:
    for name in names:
        value = params.get(name)
        if value not in (None, "", []):
            return str(value).strip()
    return ""


class DistReceiptService(BaseModuleService):
    MODULE = "dist_receipt"
    ENTITY = "回执记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["回执编号", "分发批次", "订阅方", "回执状态", "差异说明"]
    REQUIRED_FIELDS = ["回执编号", "分发批次", "订阅方"]
    STATUS_ORDER = ["已回收", "部分回收", "未回收"]
    ACTION_RULES = {"重发回执请求": "部分回收", "标记差异": "未回收"}
    NEGATIVE_ACTIONS = []

    # 动作别名 -> 规范动作，兼容不同用例 / 页面文案 / 英文命名
    CONTINUE_ACTIONS = {
        "继续办理", "办理", "继续", "查看详情", "申请详情", "详情",
        "核对", "核对签收", "核对原签收", "服务端核对", "核对资格",
        "continue", "detail", "details", "verify", "check",
    }
    SIGNOFF_ACTIONS = {
        "签收", "登记签收", "录入签收", "迟到签收", "原签收",
        "补签", "提交签收", "确认签收", "补投", "重复补投",
        "signoff", "sign", "resign", "submit_signoff",
    }
    TERMINATE_ACTIONS = {
        "终止", "终止申请", "不再重发", "结束办理", "驳回重发", "关闭申请",
        "terminate", "close", "stop", "cancel_apply",
    }
    RESUME_ACTIONS = {
        "恢复", "继续发出", "继续重发", "发出重发", "重发发出", "实际发出",
        "resume", "send", "issue",
    }
    EXCEPTION_ACTIONS = {
        "提交异常编号", "提交编号", "录入异常编号", "核准异常", "异常编号",
        "exception", "submit_exception",
    }
    HANDOFF_ACTIONS = {
        "按原单补寄", "按原单把补寄交给分发任务", "按原单移交", "交分发任务",
        "交给分发任务", "交给分发任务接手", "移交分发任务", "补寄移交",
        "分发任务接手", "按原单补寄移交", "handoff", "handoff_to_task",
    }
    RECONCILE_ACTIONS = {
        "对账", "生成对账确认", "对账确认", "核对实际送达", "用回收记录核对",
        "reconcile", "reconciliation",
    }

    # ---- 查询：GET 详情时附带服务端核对结果 ----

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        raw = super().get_entry(entry_id)
        if raw is None:
            return None
        # 返回副本再挂办理详情，绝不在 store 的活行上制造
        # entry.办理详情.receipt == entry 这样的循环引用。
        entry = dict(raw)
        try:
            detail = flow.continue_application(receipt_id=entry_id)
            if detail.get("ok"):
                entry["办理详情"] = detail
                entry["原签收"] = detail.get("原签收")
                entry["第一次签收"] = detail.get("第一次签收")
                entry["重发资格"] = detail.get("重发资格")
                entry["资格原因"] = (detail.get("重发资格") or {}).get("reason", "")
        except Exception:  # 详情核对失败不应让基础详情接口 500
            pass
        return entry

    def dispatch_action(self, entry_id: int, action: str,
                        params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": f"回执记录 {entry_id} 不存在"}
        batch = _pick(params, "分发编号", "分发批次") or str(entry.get("分发批次", ""))
        subscriber = _pick(params, "订阅方") or str(entry.get("订阅方", ""))
        remark = _pick(params, "remark", "备注", "处理原因", "原因")

        if action in self.CONTINUE_ACTIONS:
            return flow.continue_application(receipt_id=entry_id)

        if action in self.SIGNOFF_ACTIONS:
            result = _pick(params, "签收结果", "结果", "回执状态") or flow.SUCCESS
            scope = _pick(params, "签收范围", "范围") or "整批"
            count = params.get("记录数", params.get("count"))
            source = _pick(params, "来源") or (
                "补签 / 补投" if action in {"补签", "补投", "重复补投"} else "订阅方")
            res = flow.submit_signoff(
                batch=batch, result=result, subscriber=subscriber, scope=scope,
                count=count, source=source, reason=remark,
                damaged=_as_int(params.get("破损数") or params.get("damaged")),
                items=params.get("条目") or params.get("items"),
                idem_key=_pick(params, "幂等键", "idempotency_key"))
            res["entry"] = store.find(self.MODULE, entry_id)
            return res

        if action in self.TERMINATE_ACTIONS:
            res = flow.terminate_application(receipt_id=entry_id, reason=remark)
            res["entry"] = store.find(self.MODULE, entry_id)
            return res

        if action in self.RESUME_ACTIONS:
            return flow.resume_application(receipt_id=entry_id)

        if action in self.EXCEPTION_ACTIONS:
            no = _pick(params, "异常编号", "exception_no", "exceptionNo", "编号") or remark
            res = flow.submit_exception(receipt_id=entry_id, exception_no=no)
            res["entry"] = store.find(self.MODULE, entry_id)
            return res

        if action in self.HANDOFF_ACTIONS:
            no = _pick(params, "异常编号", "exception_no")
            res = flow.handoff_to_dist_task(receipt_id=entry_id, exception_no=no)
            res["entry"] = store.find(self.MODULE, entry_id)
            return res

        if action in self.RECONCILE_ACTIONS:
            task = next((t for t in store.rows("dist_task")
                         if flow._batch_of(t) == batch), None)
            if task is None:
                return {"ok": False, "message": f"分发编号 {batch} 没有对应分发任务"}
            return flow.reconcile_task_with_receipts(int(task["id"]))

        if action == "重发回执请求":
            return self.resend_request(entry_id, params)

        if action == "标记差异":
            diff = remark or _pick(params, "差异说明", "diff") or "人工标记差异"
            message = self.mark_diff(entry_id, diff)
            return {"ok": True, "message": message, "entry": entry}

        # 回退到基类的通用状态流转，保证既有动作契约不变
        moved, message = self.run_action(entry_id, action)
        return {"ok": moved is not None, "message": message, "entry": moved or entry}

    # ---- 既有锚点方法（增强但保持返回类型） ----

    def verify_receipt(self, entry_id: int) -> dict[str, Any]:
        """核对回执：服务端核对原签收、重发资格与分发结果。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "回执记录不存在"}
        batch = entry.get("分发批次", "")
        task = next((t for t in store.rows("dist_task")
                     if t.get("分发批次") == batch or t.get("分发编号") == batch), None)
        check = flow.reason_for(str(batch))
        if task is None:
            entry["status"] = "未回收"
            entry["差异说明"] = "分发批次不存在"
            return {"ok": False, "message": f"分发批次 {batch} 不存在", "核对": check}
        if task.get("status") == "已送达":
            # 保留既有锚点行为：已送达即把回执行核对为已回收
            entry["status"] = "已回收"
            entry["pending"] = False
            result: dict[str, Any] = {"ok": True, "message": "回执与分发结果一致", "核对": check}
        else:
            entry["status"] = "部分回收"
            entry["差异说明"] = f"分发状态为 {task.get('status')}"
            result = {"ok": False, "message": "回执与分发结果不一致", "核对": check}
        try:
            result["detail"] = flow.continue_application(receipt_id=entry_id)
        except Exception:
            pass
        return result

    def mark_diff(self, entry_id: int, diff: str) -> str:
        """标记回执差异并把分发任务置为失败重试。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return "回执记录不存在"
        entry["status"] = "未回收"
        entry["差异说明"] = diff
        entry["abnormal"] = True
        batch = entry.get("分发批次", "")
        for task in store.rows("dist_task"):
            if task.get("分发批次") == batch or task.get("分发编号") == batch:
                task["status"] = "分发失败"
                task["abnormal"] = True
        flow.record_event(str(batch), "标记差异", diff)
        return f"差异已标记: {diff}"

    def resend_request(self, entry_id: int,
                       params: dict[str, Any] | None = None) -> dict[str, Any]:
        """重发回执请求并等待订阅方回传。

        发起重发只建立「已受理 / 待发出」申请，不会新增已回收回执，
        也不会把重发请求本身算作收到回执。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return {"ok": False, "message": "回执记录不存在"}
        params = params or {}
        batch = str(entry.get("分发批次", ""))
        existing = flow.get_application(receipt_id=entry_id)
        if existing is None:
            existing = flow.create_application(
                batch=batch, subscriber=str(entry.get("订阅方", "")),
                apply_type=_pick(params, "申请类型") or "回执重发",
                receipt_id=entry_id)
        entry["status"] = "部分回收"
        entry["pending"] = True
        from datetime import datetime, timezone
        entry["重发时间"] = datetime.now(timezone.utc).isoformat()
        check = flow.reason_for(batch)
        return {
            "ok": True,
            # 保留既有锚点方法的原始文案，避免破坏直接调用该服务的断言
            "message": "回执请求已重发",
            "entry": entry,
            "application": existing,
            "重发资格": {"eligible": check["eligible"], "code": check["code"],
                       "reason": check["原因"]},
        }

    def consume_dist_results(self, entry_id: int) -> list[dict[str, Any]]:
        """读取分发模块的送达批次，作为核对输入。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return []
        subscriber = entry.get("订阅方", "")
        return [t for t in store.rows("dist_task")
                if t.get("订阅方") == subscriber and t.get("status") in ("已送达", "分发失败")]

    def stats_by_subscriber(self) -> dict[str, dict[str, int]]:
        """按订阅方统计回执回收情况，供对账看板消费。"""
        stats: dict[str, dict[str, int]] = {}
        for row in store.rows(self.MODULE):
            subscriber = row.get("订阅方", "未知")
            bucket = stats.setdefault(subscriber, {"已回收": 0, "部分回收": 0, "未回收": 0})
            status = row.get("status", "未回收")
            if status in bucket:
                bucket[status] += 1
        return stats

    def notify_receipt_gap(self, entry_id: int) -> str:
        """把回执缺口通知订阅方重新上传。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return "回执记录不存在"
        subscriber = entry.get("订阅方", "")
        for sub in store.rows("subscriber_mgr"):
            if sub.get("订阅方标识") == subscriber:
                sub["pending"] = True
        return f"已通知 {subscriber} 补传回执"


def _as_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
