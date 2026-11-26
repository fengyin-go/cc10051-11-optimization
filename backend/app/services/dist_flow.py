"""分发回执中心的重发 / 补签 / 补寄办理流。

这个模块是纯领域层 + 内存数据：所有状态都落在全局 ``store`` 中，
因此「重发中断后继续办理」「已匹配实际送达回读」都不依赖进程内临时变量。

关键不变量（由服务端唯一裁决，前端只负责展示）：

1. 同一分发编号至多有一份生效的整批签收；迟到原签收与补签不会同时生效。
2. 同一分发 + 同一签收结果的重复补投是幂等的，至多产生一份对账确认。
3. 重发请求发起本身不是回执，不会新增「已回收」回执。
4. 没有签收 与 已经拒收 返回不同的原因代码 / 文案。
5. 补寄只领取、交付实际获准的条目，且只产生一份交付回执。
6. 终止 / 补签 / 移交等处理原因都写回同一个分发编号，可追查。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.store import store

# ---- 数据表（惰性建表，不改变既有 20 个种子模块） ----
T_SIGNOFF = "signoff"                 # 签收记录（含迟到原签收 / 拒收）
T_APPLY = "resend_apply"              # 重发 / 补签 / 补寄申请
T_RECON = "reconciliation"            # 对账确认（幂等）
T_DISP = "disposition_receipt"        # 可追查处置回执
T_DELIVERY = "delivery_record"        # 实际送达 / 回收匹配记录
T_EVENT = "dist_event"                # 同一分发编号下的处置事件 / 原因轨迹

RECEIPT_MODULE = "dist_receipt"
TASK_MODULE = "dist_task"
SUB_MODULE = "subscriber_mgr"

SUCCESS = "签收成功"
REJECTED = "拒收"

# 原因代码：页面与接口都依赖它们区分两类不同原因
REASON_NO_SIGNOFF = "NO_SIGNOFF"
REASON_REJECTED = "REJECTED"
REASON_ALREADY_SIGNED = "ALREADY_SIGNED"
REASON_PARTIAL_DAMAGE = "PARTIAL_DAMAGE"
REASON_TASK_MISSING = "TASK_MISSING"

_TYPE_ALIASES = {
    "回执重发": "回执重发",
    "重发申请": "回执重发",
    "重发回执请求": "回执重发",
    "补签": "补签",
    "补签申请": "补签",
    "补寄": "补寄",
    "补寄申请": "补寄",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows(table: str) -> list[dict[str, Any]]:
    return store.rows(table)


def _next_id(table: str) -> int:
    rows = _rows(table)
    return max((int(r.get("id", 0)) for r in rows), default=0) + 1


def _find(table: str, **criteria: Any) -> dict[str, Any] | None:
    # None / 空串不能作为匹配条件，否则会误中“恰好没有这个键”的行；
    # 没有任何有效条件时返回 None，而不是返回第一行。
    valid = {k: v for k, v in criteria.items() if v not in (None, "")}
    if not valid:
        return None
    for row in _rows(table):
        if all(row.get(k) == v for k, v in valid.items()):
            return row
    return None


def _batch_of(ref: dict[str, Any] | str | None) -> str:
    if ref is None:
        return ""
    if isinstance(ref, str):
        return ref
    return str(ref.get("分发编号") or ref.get("分发批次") or "")


def norm_result(result: str) -> str:
    """把各种签收结果文案归一到 success / rejected / pending。"""
    text = str(result or "").strip()
    if not text:
        return "pending"
    if "拒" in text or "退回" in text or "拒收" in text:
        return REJECTED
    if "失败" in text or "未签收" in text:
        return "pending"
    if "签收" in text or "送达" in text or text in {"成功", "success", "ok"}:
        return SUCCESS
    return text


def record_event(batch: str, event_type: str, detail: str,
                 **extra: Any) -> dict[str, Any]:
    """把处理原因 / 动作附到同一分发编号下，形成可追查轨迹。"""
    event = {
        "id": _next_id(T_EVENT),
        "分发编号": batch,
        "事件类型": event_type,
        "处理原因": detail,
        "时间": now_iso(),
    }
    event.update(extra)
    _rows(T_EVENT).append(event)
    # 最新原因冗余写回分发任务 / 回执行，列表页也能直接看到
    for task in _rows(TASK_MODULE):
        if _batch_of(task) == batch or task.get("原分发编号") == batch:
            task["处理原因"] = detail
            task.setdefault("处置轨迹", []).append(
                {"类型": event_type, "原因": detail, "时间": event["时间"]})
    for rc in _rows(RECEIPT_MODULE):
        if _batch_of(rc) == batch:
            rc["处理原因"] = detail
            rc.setdefault("处置轨迹", []).append(
                {"类型": event_type, "原因": detail, "时间": event["时间"]})
    return event


# ---------------------------------------------------------------------------
# 签收与对账确认（幂等核心）
# ---------------------------------------------------------------------------

def effective_signoff(batch: str) -> dict[str, Any] | None:
    """该分发当前唯一生效的整批签收（优先成功签收）。"""
    rows = [s for s in _rows(T_SIGNOFF) if _batch_of(s) == batch]
    for s in rows:
        if s.get("effective") and norm_result(str(s.get("签收结果", ""))) == SUCCESS:
            return s
    for s in rows:
        if s.get("effective"):
            return s
    return rows[0] if rows else None


def reason_for(batch: str) -> dict[str, Any]:
    """由服务端核对出分发处于哪一种签收事实，给出不同原因。"""
    task = next((t for t in _rows(TASK_MODULE) if _batch_of(t) == batch), None)
    signoffs = [s for s in _rows(T_SIGNOFF) if _batch_of(s) == batch]
    rejected = next((s for s in signoffs
                     if norm_result(str(s.get("签收结果", ""))) == REJECTED), None)
    signed = next((s for s in signoffs
                   if norm_result(str(s.get("签收结果", ""))) == SUCCESS
                   and s.get("effective", True)), None)

    if rejected is not None and signed is None:
        why = str(rejected.get("拒收原因") or rejected.get("处理原因") or "货物 / 单据不符")
        return {
            "code": REASON_REJECTED,
            "reasonCode": REASON_REJECTED,
            "eligible": False,
            "具备重发资格": False,
            "原因": f"该分发属于“已经拒收”的分发（{why}），与“没有签收”的分发原因不同；"
                    f"需先按拒收异常核准后再决定重发或补发，不能按未签收直接重发。",
            "rejected": rejected,
        }
    if signed is not None:
        damaged = int(signed.get("破损数", 0) or 0)
        if damaged > 0:
            return {
                "code": REASON_PARTIAL_DAMAGE,
                "reasonCode": REASON_PARTIAL_DAMAGE,
                "eligible": "replacement-only",
                "具备重发资格": False,
                "可补寄": True,
                "原因": f"该分发已完成整批签收（{signed.get('签收编号')}，第 "
                        f"{signed.get('第几次', 1)} 次），其中 {damaged} 条破损；"
                        f"整批无需重发，仅可按核准条目补寄。",
                "signed": signed,
            }
        return {
            "code": REASON_ALREADY_SIGNED,
            "reasonCode": REASON_ALREADY_SIGNED,
            "eligible": False,
            "具备重发资格": False,
            "原因": f"该分发已有有效签收（{signed.get('签收编号')}，第 "
                    f"{signed.get('第几次', 1)} 次，来源：{signed.get('来源', '订阅方')}），"
                    f"重发 / 补签无需再送给订阅方，否则同一批次会被重复签收。",
            "signed": signed,
        }
    if task is None:
        return {
            "code": REASON_TASK_MISSING,
            "reasonCode": REASON_TASK_MISSING,
            "eligible": False,
            "具备重发资格": False,
            "原因": f"找不到分发编号 {batch} 对应的分发任务，暂不能核对重发资格。",
        }
    return {
        "code": REASON_NO_SIGNOFF,
        "reasonCode": REASON_NO_SIGNOFF,
        "eligible": True,
        "具备重发资格": True,
        "原因": "这是“没有签收”的分发：系统中没有任何签收记录（订阅方尚未签收 / 未回传回执），"
                "与“已经拒收”的分发原因不同，具备重发 / 补签资格。",
    }


def ensure_reconciliation(batch: str, result_norm: str,
                          signoff_id: int | None = None,
                          summary: str = "") -> dict[str, Any]:
    """按 (分发编号, 签收结果) 幂等产出唯一一份对账确认。"""
    key = f"{batch}::{result_norm}"
    existing = _find(T_RECON, 幂等键=key)
    if existing is not None:
        return existing
    recon = {
        "id": _next_id(T_RECON),
        "确认编号": "",  # 追加后按主键生成稳定编号
        "分发编号": batch,
        "签收结果": result_norm,
        "签收记录id": signoff_id,
        "确认结论": summary or f"分发 {batch} 的{result_norm}结果核对一致",
        "幂等键": key,
        "确认时间": now_iso(),
    }
    recon["确认编号"] = f"DZ-{recon['id']:04d}"
    _rows(T_RECON).append(recon)
    record_event(batch, "对账确认", recon["确认结论"], 对账确认编号=recon["确认编号"])
    return recon


def submit_signoff(*, batch: str, result: str = SUCCESS,
                   subscriber: str = "", scope: str = "整批",
                   count: int | str | None = None, source: str = "订阅方",
                   reason: str = "", damaged: int | None = None,
                   items: list[str] | str | None = None,
                   idem_key: str | None = None,
                   sync_receipt: bool = True) -> dict[str, Any]:
    """登记一次签收 / 补投；重复请求幂等，绝不产生第二份对账确认。"""
    if not batch:
        return {"ok": False, "message": "缺少分发编号，无法签收"}
    result_norm = norm_result(result)
    scope_key = scope or "整批"
    # 业务幂等键：同一分发 + 同一归一签收结果只保留一次；
    # 显式传入 idem_key 时优先按显式键去重，再回退到业务键。
    explicit_key = (idem_key or "").strip()
    biz_key = explicit_key or f"{batch}::{result_norm}"

    duplicate_row = (_find(T_SIGNOFF, 幂等键=explicit_key) if explicit_key else None) \
        or _find(T_SIGNOFF, 幂等键=biz_key)
    if duplicate_row is not None:
        recon = _find(T_RECON, 幂等键=f"{batch}::{result_norm}")
        record_event(batch, "重复补投拦截",
                     f"同一分发同一签收结果重复补投，已沿用 {duplicate_row.get('签收编号')}，"
                     f"未新增签收 / 对账确认")
        return {
            "ok": True,
            "duplicate": True,
            "idempotent": True,
            "message": "重复签收 / 重复补投已忽略：同一分发与同一签收结果只保留一次生效签收和一份对账确认",
            "signoff": duplicate_row,
            "reconciliation": recon,
        }

    prior_success = any(
        _batch_of(s) == batch
        and norm_result(str(s.get("签收结果", ""))) == SUCCESS
        and s.get("effective", True)
        for s in _rows(T_SIGNOFF)
    )
    effective = result_norm == SUCCESS and not prior_success
    signoff = {
        "id": _next_id(T_SIGNOFF),
        "签收编号": f"SIGN-{batch}-{_next_id(T_SIGNOFF):02d}",
        "分发编号": batch,
        "订阅方": subscriber,
        "签收结果": result or SUCCESS,
        "归一结果": result_norm,
        "签收范围": scope_key,
        "记录数": count if count not in (None, "") else None,
        "破损数": damaged or 0,
        "条目": items or [],
        "来源": source,
        "第几次": sum(1 for s in _rows(T_SIGNOFF) if _batch_of(s) == batch) + 1,
        "effective": effective if result_norm == SUCCESS else True,
        "拒收原因": reason if result_norm == REJECTED else "",
        "处理原因": reason,
        "幂等键": biz_key,
        "签收时间": now_iso(),
    }
    _rows(T_SIGNOFF).append(signoff)

    recon: dict[str, Any] | None = None
    if result_norm in (SUCCESS, REJECTED):
        recon = ensure_reconciliation(
            batch, result_norm, signoff["id"],
            f"首次有效{result_norm}签收核对：{scope_key}"
            + (f"，破损 {damaged} 条" if damaged else ""))

    # 回写回执中心行的回收状态（重发申请本身不会走到这里）。
    # 播种场景保留“待继续办理核对”的设定状态，因此播种时关闭回写。
    if sync_receipt:
      for rc in _rows(RECEIPT_MODULE):
        if _batch_of(rc) == batch:
            if result_norm == SUCCESS:
                # 整批签收回执已经回收；破损属于差异说明，不把整批打回未回收
                rc["status"] = "已回收"
                rc["回执状态"] = "已回收" + (f"（{damaged} 条破损待补寄）" if damaged else "")
                rc["pending"] = False
                if damaged:
                    rc["abnormal"] = True
                    rc["差异说明"] = f"整批已签收，其中 {damaged} 条破损，等待补寄核准"
            elif result_norm == REJECTED:
                rc["回执状态"] = "已拒收"
                rc["差异说明"] = reason or "订阅方已拒收"
            rc["最后签收id"] = signoff["id"]

    record_event(batch, "签收",
                 f"登记{result_norm}签收 {signoff['签收编号']}（{source}，{scope_key}）"
                 + ("，该签收晚于重发申请到达" if "迟到" in source else ""))
    return {
        "ok": True,
        "duplicate": False,
        "signoff": signoff,
        "reconciliation": recon,
        "effective": signoff["effective"],
        "message": "签收已登记并完成对账确认" if recon else "签收已登记",
    }


# ---------------------------------------------------------------------------
# 实际送达匹配 / 回读
# ---------------------------------------------------------------------------

def matched_deliveries(batch: str = "", subscriber: str = "") -> list[dict[str, Any]]:
    """回读已经匹配的实际送达；重发中断 / 刷新后结果仍在。"""
    out: list[dict[str, Any]] = []
    for d in _rows(T_DELIVERY):
        if batch and _batch_of(d) != batch and d.get("原分发编号") != batch:
            continue
        if subscriber and d.get("订阅方") != subscriber:
            continue
        d = dict(d)
        d["matched"] = True
        out.append(d)
    # 分发任务已送达也视作可回读的实际送达来源（回收记录核对）
    for t in _rows(TASK_MODULE):
        if batch and _batch_of(t) != batch and t.get("原分发编号") != batch:
            continue
        if subscriber and t.get("订阅方") != subscriber:
            continue
        if t.get("status") == "已送达":
            out.append({
                "来源": "dist_task",
                "分发编号": _batch_of(t),
                "订阅方": t.get("订阅方", ""),
                "实际送达时间": t.get("实际送达时间") or t.get("updated_at") or "",
                "matched": True,
            })
    return out


def reconcile_task_with_receipts(task_id: int) -> dict[str, Any]:
    """分发任务用回执中心的回收记录核对实际送达。"""
    task = store.find(TASK_MODULE, task_id)
    if task is None:
        return {"ok": False, "message": "分发批次不存在"}
    batch = _batch_of(task)
    receipts = [dict(r) for r in _rows(RECEIPT_MODULE) if _batch_of(r) == batch
                or r.get("原分发编号") == batch]
    recovered = [r for r in receipts if r.get("status") == "已回收"
                 or "已回收" in str(r.get("回执状态", ""))]
    deliveries = matched_deliveries(batch)
    signoff = effective_signoff(batch)
    verdict = {
        "task_id": task_id,
        "分发编号": batch,
        "分发状态": task.get("status"),
        "回收记录数": len(receipts),
        "已回收数": len(recovered),
        "回收记录": receipts,
        "实际送达": deliveries,
        "matched_deliveries": deliveries,
        "有效签收": signoff,
        "实际已送达": bool(deliveries) or task.get("status") == "已送达",
    }
    verdict["核对结论"] = (
        "回收记录与实际送达一致" if verdict["实际已送达"] and (recovered or signoff)
        else "已有实际送达但缺少有效回收 / 签收" if verdict["实际已送达"]
        else "尚未核对到实际送达")
    task["送达核对"] = {k: v for k, v in verdict.items()
                        if k not in ("回收记录", "实际送达", "matched_deliveries", "有效签收")}
    record_event(batch, "送达核对", verdict["核对结论"])
    verdict["ok"] = True
    verdict["message"] = verdict["核对结论"]
    return verdict


# ---------------------------------------------------------------------------
# 申请与处置回执
# ---------------------------------------------------------------------------

def get_application(apply_id: int | None = None, *,
                    receipt_id: int | None = None,
                    batch: str = "") -> dict[str, Any] | None:
    apply: dict[str, Any] | None = None
    if apply_id is not None:
        apply = store.find(T_APPLY, apply_id)
    if apply is None and receipt_id is not None:
        apply = _find(T_APPLY, receipt_id=receipt_id)
        if apply is None:
            rc = store.find(RECEIPT_MODULE, receipt_id)
            if rc is not None:
                apply = _find(T_APPLY, 申请编号=rc.get("申请编号")) or \
                        _find(T_APPLY, receipt_id=receipt_id)
    if apply is None and batch:
        candidates = [a for a in _rows(T_APPLY) if _batch_of(a) == batch]
        apply = candidates[-1] if candidates else None
    return apply


def _linked_receipt(apply: dict[str, Any]) -> dict[str, Any] | None:
    rid = apply.get("receipt_id")
    if rid is not None:
        rc = store.find(RECEIPT_MODULE, int(rid))
        if rc is not None:
            return rc
    return _find(RECEIPT_MODULE, 申请编号=apply.get("申请编号"))


def continue_application(apply_id: int | None = None, *,
                         receipt_id: int | None = None) -> dict[str, Any]:
    """服务端核对原签收与重发资格，输出申请详情（中断后可反复继续）。"""
    apply = get_application(apply_id, receipt_id=receipt_id)
    if apply is None:
        return {"ok": False, "message": "没有找到对应的重发 / 补签申请"}
    batch = _batch_of(apply)
    check = reason_for(batch)
    signoffs = [dict(s) for s in _rows(T_SIGNOFF) if _batch_of(s) == batch]
    first_signoff = next((s for s in signoffs if s.get("第几次") == 1),
                         signoffs[0] if signoffs else None)
    recons = [dict(r) for r in _rows(T_RECON) if r.get("分发编号") == batch]
    disps = [dict(d) for d in _rows(T_DISP) if d.get("申请id") == apply["id"]
             or d.get("申请编号") == apply.get("申请编号")
             or d.get("分发编号") == batch]
    task = next((dict(t) for t in _rows(TASK_MODULE) if _batch_of(t) == batch), None)
    replacement = None
    tid = apply.get("dist_task_id")
    if tid is not None:
        replacement = store.find(TASK_MODULE, int(tid))
    if replacement is None:
        replacement = _find(TASK_MODULE, 关联申请编号=apply.get("申请编号"))
    deliveries = matched_deliveries(batch, str(apply.get("订阅方", "")))
    events = [dict(e) for e in _rows(T_EVENT) if e.get("分发编号") == batch]
    receipt_row = _linked_receipt(apply)

    # 把核对结果持久化到申请，保证「继续办理」看到的是服务端最新裁决
    apply["重发资格"] = {
        "eligible": check["eligible"],
        "code": check["code"],
        "reason": check["原因"],
    }
    apply["资格代码"] = check["code"]
    apply["资格原因"] = check["原因"]
    apply["last_continue_at"] = now_iso()

    result = {
        "ok": True,
        "message": "服务端核对完成",
        "application": apply,
        # 统一动作契约里的 entry：有回执行时给回执行，否则给申请本身
        "entry": receipt_row or apply,
        "申请": apply,
        "receipt": receipt_row,
        "原签收": first_signoff,
        "第一次签收": first_signoff,
        "签收记录": signoffs,
        "补签申请结果": {
            "申请编号": apply.get("申请编号"),
            "申请类型": apply.get("申请类型"),
            "状态": apply.get("status"),
            "是否实际发出": bool(apply.get("sent")),
            "结果": apply.get("办理结果") or (
                "尚未实际发出，等待值班员继续办理" if not apply.get("sent")
                else apply.get("办理结果", "已发出")),
        },
        "重发资格": apply["重发资格"],
        "eligibility": apply["重发资格"],
        "核对": check,
        "对账确认": recons,
        "reconciliations": recons,
        "处置回执": disps,
        "dispositions": disps,
        "原分发任务": task,
        "补寄分发任务": replacement,
        "replacement_task": replacement,
        "实际送达": deliveries,
        "matched_deliveries": deliveries,
        "事件轨迹": events,
        "steps": apply.get("steps", []),
    }
    return result


def issue_disposition(apply: dict[str, Any] | None, dtype: str,
                      reason: str, *, idem_key: str | None = None,
                      extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """开具可追查处置回执；给了幂等键时重复办理返回同一份。"""
    if idem_key:
        existing = _find(T_DISP, 幂等键=idem_key)
        if existing is not None:
            return existing
    batch = _batch_of(apply) if apply else (extra or {}).get("分发编号", "")
    disp = {
        "id": _next_id(T_DISP),
        "处置回执编号": f"CZ-{_next_id(T_DISP):06d}",
        "申请id": apply.get("id") if apply else None,
        "申请编号": apply.get("申请编号") if apply else (extra or {}).get("申请编号", ""),
        "分发编号": batch,
        "处置类型": dtype,
        "处理原因": reason,
        "开具时间": now_iso(),
        "幂等键": idem_key or "",
    }
    if extra:
        disp.update(extra)
    _rows(T_DISP).append(disp)
    record_event(batch, dtype, reason, 处置回执编号=disp["处置回执编号"])
    return disp


def create_application(*, batch: str, subscriber: str = "",
                       apply_type: str = "回执重发",
                       receipt_id: int | None = None,
                       exception_no: str = "",
                       approved_items: list[str] | str | None = None,
                       approved_count: int | None = None,
                       title: str = "",
                       apply_no: str = "") -> dict[str, Any]:
    apply_type = _TYPE_ALIASES.get(apply_type, apply_type)
    items = _as_list(approved_items)
    seq_id = _next_id(T_APPLY)
    apply = {
        "id": seq_id,
        "申请编号": (apply_no or "").strip() or f"RA-{seq_id:04d}",
        "申请标题": title or f"{subscriber or batch} 的{apply_type}申请",
        "申请类型": apply_type,
        "分发编号": batch,
        "订阅方": subscriber,
        "status": "已受理",
        "pending": True,
        "sent": False,
        "是否实际发出": False,
        "发出状态": "待发出",
        "exception_no": exception_no,
        "异常编号": exception_no,
        "获准条目": "、".join(items),
        "获准条目列表": items,
        "获准数量": approved_count if approved_count is not None else len(items),
        "receipt_id": receipt_id,
        "dist_task_id": None,
        "steps": [
            {"key": "accepted", "label": "申请已受理", "done": True},
            {"key": "verify", "label": "服务端核对原签收与资格", "done": False},
            {"key": "disposition", "label": "开具处置回执", "done": False},
            {"key": "send_or_handoff", "label": "发出 / 移交分发任务", "done": False},
        ],
        "断点": "verify",
        "created_at": now_iso(),
    }
    _rows(T_APPLY).append(apply)
    record_event(batch, f"{apply_type}申请受理",
                 f"{apply['申请编号']} 已受理，尚未实际发出",
                 申请编号=apply["申请编号"])
    return apply


def _as_list(value: list[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value)
    for sep in ("、", ",", "，", ";", "；", "\n"):
        text = text.replace(sep, "|")
    return [part.strip() for part in text.split("|") if part.strip()]


def terminate_application(apply_id: int | None = None, *,
                          receipt_id: int | None = None,
                          reason: str = "") -> dict[str, Any]:
    apply = get_application(apply_id, receipt_id=receipt_id)
    if apply is None:
        return {"ok": False, "message": "没有找到对应的申请，无法终止"}
    batch = _batch_of(apply)
    check = reason_for(batch)
    if not reason.strip():
        reason = check["原因"] if check["code"] == REASON_ALREADY_SIGNED else \
            f"服务端核对（{check['code']}）后终止本次{apply.get('申请类型', '重发')}申请"

    idem = f"terminate:{apply['id']}"
    existing = _find(T_DISP, 幂等键=idem)
    if existing is not None and apply.get("status") in ("已终止", "无需重发"):
        detail = continue_application(apply["id"])
        detail.update({"ok": True, "duplicate": True,
                       "message": "申请已终止，处置回执已存在，未重复开具",
                       "disposition": existing})
        return detail

    apply["status"] = "无需重发" if check["code"] == REASON_ALREADY_SIGNED else "已终止"
    apply["pending"] = False
    apply["办理结果"] = "核对后不再送给订阅方：" + reason
    apply["处理原因"] = reason
    apply["断点"] = "closed"
    for step in apply.get("steps", []):
        if step["key"] in ("verify", "disposition"):
            step["done"] = True
        if step["key"] == "send_or_handoff":
            step["label"] = "无需发出（终止）"
            step["done"] = True

    disp = issue_disposition(
        apply, "终止 / 不再重发", reason, idem_key=idem,
        extra={"资格代码": check["code"], "核对快照": check["原因"]})
    # 终止也补一份对账确认（若已有签收），幂等；并把对应回执行核对到位
    signoff = effective_signoff(batch)
    if signoff is not None:
        norm = norm_result(str(signoff.get("签收结果", SUCCESS)))
        ensure_reconciliation(batch, norm, signoff.get("id"),
                              "终止前核对：原签收唯一有效")
        rc = _linked_receipt(apply)
        if rc is not None and norm == SUCCESS:
            rc["status"] = "已回收"
            rc["回执状态"] = "已回收（原签收迟到，经继续办理核对）"
            rc["pending"] = False
    detail = continue_application(apply["id"])
    detail.update({"ok": True, "message": "申请已终止并开具可追查处置回执，原批次仍只签收一次",
                   "disposition": disp})
    return detail


def resume_application(apply_id: int | None = None, *,
                       receipt_id: int | None = None) -> dict[str, Any]:
    """中断后继续：若仍具备资格则真正发出；否则引导终止。已匹配送达仍回读。"""
    apply = get_application(apply_id, receipt_id=receipt_id)
    if apply is None:
        return {"ok": False, "message": "没有找到未完成的申请"}
    batch = _batch_of(apply)
    check = reason_for(batch)
    detail = continue_application(apply["id"])
    if check["code"] in (REASON_ALREADY_SIGNED, REASON_PARTIAL_DAMAGE, REASON_REJECTED):
        detail.update({"ok": False, "shouldTerminate": True,
                       "message": "服务端核对认为本次申请不应继续发出：" + check["原因"]})
        return detail
    apply["sent"] = True
    apply["是否实际发出"] = True
    apply["发出状态"] = "已实际发出"
    apply["status"] = "办理中"
    apply["断点"] = "await_receipt"
    apply["办理结果"] = "重发请求已实际发出，等待订阅方回传；发起动作本身不算收到回执"
    for step in apply.get("steps", []):
        if step["key"] in ("verify", "send_or_handoff"):
            step["done"] = True
    issue_disposition(apply, "重发发出",
                      f"{apply['申请编号']} 已实际发给订阅方 {apply.get('订阅方', '')}，"
                      f"等待回执（不记为已回收）",
                      idem_key=f"send:{apply['id']}")
    detail = continue_application(apply["id"])
    detail.update({"ok": True, "message": "未完成的重发请求已继续并实际发出；已匹配的实际送达仍可回读"})
    return detail


def submit_exception(apply_id: int | None = None, *,
                     receipt_id: int | None = None,
                     exception_no: str = "") -> dict[str, Any]:
    apply = get_application(apply_id, receipt_id=receipt_id)
    if apply is None:
        return {"ok": False, "message": "没有找到补寄申请"}
    exception_no = str(exception_no or apply.get("异常编号") or "").strip()
    if not exception_no:
        return {"ok": False, "message": "请提交值班员核准后取得的异常编号"}
    apply["异常编号"] = exception_no
    apply["exception_no"] = exception_no
    apply["异常编号状态"] = "已提交"
    disp = issue_disposition(
        apply, "异常编号提交", f"已提交核准异常编号 {exception_no}，获准 "
                              f"{apply.get('获准数量', 0)} 条：{apply.get('获准条目', '')}",
        idem_key=f"exc:{apply['id']}:{exception_no}")
    # 提交编号后同样把补寄按原单交给分发任务，保证值班员可领取实际获准条目
    handoff = handoff_to_dist_task(apply["id"], exception_no=exception_no)
    detail = continue_application(apply["id"])
    detail.update({"ok": True, "message": f"异常编号 {exception_no} 已提交，补寄已按原单交分发任务接手",
                   "disposition": disp, "handoff": handoff})
    return detail


def handoff_to_dist_task(apply_id: int | None = None, *,
                         receipt_id: int | None = None,
                         exception_no: str = "") -> dict[str, Any]:
    apply = get_application(apply_id, receipt_id=receipt_id)
    if apply is None:
        return {"ok": False, "message": "没有找到补寄申请"}
    if exception_no:
        apply["异常编号"] = exception_no
    batch = _batch_of(apply)
    items = list(apply.get("获准条目列表") or _as_list(apply.get("获准条目")))
    approved_count = int(apply.get("获准数量") or len(items) or 0)
    if approved_count <= 0:
        return {"ok": False, "message": "该申请没有已核准的补寄条目，不能按原单移交"}
    if not items:
        items = [f"获准条目{i}" for i in range(1, approved_count + 1)]

    existing_task: dict[str, Any] | None = None
    if apply.get("dist_task_id"):
        existing_task = store.find(TASK_MODULE, int(apply["dist_task_id"]))
    if existing_task is None:
        existing_task = _find(TASK_MODULE, 关联申请编号=apply.get("申请编号"))
    if existing_task is not None:
        task = existing_task
        created = False
    else:
        # 序号按「同一原分发编号」下已有的补寄任务计算，
        # 不受其它分发单的补寄任务影响，保证首张补寄单稳定为 BJ01。
        seq = sum(
            1 for t in _rows(TASK_MODULE)
            if t.get("原分发编号") == batch
            or str(t.get("分发批次", "")).startswith(f"{batch}-BJ")
        ) + 1
        new_batch = f"{batch}-BJ{seq:02d}"
        task = {
            "id": _next_id(TASK_MODULE),
            "分发批次": new_batch,
            "分发编号": new_batch,
            "原分发编号": batch,
            "分发组编号": batch,
            "订阅方": apply.get("订阅方", ""),
            "记录范围": "补寄获准条目 " + "、".join(items),
            "分发状态": "排队中",
            "回执状态": "待回收",
            "status": "排队中",
            "pending": True,
            "abnormal": False,
            "补寄": True,
            "可领取": True,
            "关联申请编号": apply.get("申请编号"),
            "关联异常编号": apply.get("异常编号", ""),
            "获准条目": "、".join(items),
            "获准条目列表": items,
            "获准数量": approved_count,
            "记录总数": approved_count,
            "claimed": False,
            "幂等键": f"handoff:{apply.get('申请编号')}",
            "created_at": now_iso(),
        }
        _rows(TASK_MODULE).append(task)
        apply["dist_task_id"] = task["id"]
        created = True

    apply["status"] = "已移交分发任务"
    apply["办理结果"] = f"补寄已按原单移交分发任务 {task['分发批次']}，仅含获准 {approved_count} 条"
    apply["断点"] = "dist_task_claim"
    for step in apply.get("steps", []):
        if step["key"] == "send_or_handoff":
            step["done"] = True
            step["label"] = "已移交分发任务"
    issue_disposition(
        apply, "按原单补寄移交",
        f"按原分发单 {batch} 把核准的 {approved_count} 条（{'、'.join(items)}）"
        f"交给分发任务 {task['分发批次']} 接手",
        idem_key=f"handoff:{apply['id']}",
        extra={"分发任务id": task["id"], "补寄批次": task["分发批次"]})
    record_event(batch, "补寄移交",
                 f"补寄任务 {task['分发批次']} 已建立，只含获准 {approved_count} 条；"
                 f"处理原因挂在同一分发编号 {batch}")
    return {"ok": True, "created": created, "task": task,
            "message": "补寄已交给分发任务接手" if created else "移交已存在，沿用同一补寄任务"}


def claim_approved_items(task_id: int) -> dict[str, Any]:
    """值班员在分发任务领取实际获准的条目（不会领取整批 8 条）。"""
    task = store.find(TASK_MODULE, task_id)
    if task is None:
        return {"ok": False, "message": "分发批次不存在"}
    items = list(task.get("获准条目列表") or _as_list(task.get("获准条目")))
    approved = int(task.get("获准数量") or len(items) or 0)
    if not task.get("补寄") or approved <= 0:
        return {"ok": False,
                "message": "该分发任务没有经异常 / 回执中心核准的补寄条目，无可领取内容"}
    if task.get("claimed"):
        return {"ok": True, "duplicate": True,
                "claimed_items": task.get("领取条目列表") or items,
                "claimed_count": int(task.get("领取数量") or approved),
                "message": "该补寄任务已领取，请勿重复领取；实际获准条目保持不变"}
    task["claimed"] = True
    task["可领取"] = False
    task["领取状态"] = "已领取"
    task["领取条目"] = "、".join(items)
    task["领取条目列表"] = items
    task["领取数量"] = approved
    task["领取时间"] = now_iso()
    if task.get("status") == "排队中":
        task["status"] = "分发中"
        task["分发状态"] = "分发中（已领取获准条目）"
    record_event(_batch_of(task), "领取补寄",
                 f"值班员领取实际获准的 {approved} 条：{'、'.join(items)}（原整批不重复签收）")
    return {"ok": True, "claimed_items": items, "claimed_count": approved,
            "task": task, "message": f"已领取实际获准的 {approved} 条：{'、'.join(items)}"}


def confirm_replacement_delivery(task_id: int) -> dict[str, Any]:
    """补寄送达：幂等且只产生一份交付回执。"""
    task = store.find(TASK_MODULE, task_id)
    if task is None:
        return {"ok": False, "message": "分发批次不存在"}
    batch = _batch_of(task)
    idem = f"delivery-receipt:{task_id}"
    existing_rc = _find(RECEIPT_MODULE, 幂等键=idem)
    if existing_rc is not None:
        return {"ok": True, "duplicate": True, "receipt": existing_rc,
                "message": "该补寄已有一份交付回执，重复确认不会再产生第二份"}
    if not task.get("claimed") and task.get("补寄"):
        claim_approved_items(task_id)
    task["status"] = "已送达"
    task["分发状态"] = "已送达"
    task["回执状态"] = "已回收"
    task["pending"] = False
    task["实际送达时间"] = now_iso()

    items = list(task.get("领取条目列表") or task.get("获准条目列表")
                 or _as_list(task.get("获准条目")))
    receipt = {
        "id": _next_id(RECEIPT_MODULE),
        "回执编号": f"RC-BJ-{task_id:03d}",
        "分发批次": batch,
        "分发编号": batch,
        "原分发编号": task.get("原分发编号", ""),
        "订阅方": task.get("订阅方", ""),
        "回执状态": "已回收",
        "差异说明": f"补寄交付回执，条目：{'、'.join(items)}" if items else "补寄交付回执",
        "status": "已回收",
        "pending": False,
        "abnormal": False,
        "回执类型": "补寄交付回执",
        "交付回执": True,
        "条目列表": items,
        "记录数": len(items) or task.get("获准数量"),
        "幂等键": idem,
        "回收时间": now_iso(),
    }
    _rows(RECEIPT_MODULE).append(receipt)
    # 补寄只要求「一份交付回执」；不再为补寄子批次另建对账确认，
    # 避免按原分发编号检索时把原单与补寄单的确认混在一起。
    apply = _find(T_APPLY, 申请编号=task.get("关联申请编号"))
    issue_disposition(
        apply, "补寄交付回执",
        f"补寄批次 {batch} 送达，仅出具一份交付回执 {receipt['回执编号']}",
        idem_key=f"delivery-disp:{task_id}",
        extra={"交付回执编号": receipt["回执编号"], "分发编号": batch})
    record_event(batch, "补寄交付", f"补寄仅产生一份交付回执 {receipt['回执编号']}")
    return {"ok": True, "receipt": receipt,
            "message": "补寄交付已确认，只出具一份交付回执"}


# ---------------------------------------------------------------------------
# 场景种子数据（题面中的 X / 8 条破损 2 条）
# ---------------------------------------------------------------------------

def ensure_dist_scenario() -> None:
    """幂等写入题面场景；已存在（按申请编号）则完全跳过。"""
    if _find(T_APPLY, 申请编号="RA-X-01") is not None:
        return

    base = datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc)

    # 订阅方（沿用 subscriber_mgr 既有字段）
    subs = [
        (4, "X", "MasterCustomer", "JSON"),
        (5, "N（未签收）", "MasterCustomer", "JSON"),
        (6, "R（拒收）", "MasterCustomer", "JSON"),
        (7, "B（八达零售）", "MasterCustomer", "JSON"),
    ]
    for sid, code, entity, fmt in subs:
        if store.find(SUB_MODULE, sid) is None:
            _rows(SUB_MODULE).append({
                "id": sid, "status": "已生效", "pending": False, "abnormal": False,
                "订阅方标识": code, "订阅实体": entity, "分发格式": fmt,
                "订阅过滤": "全部", "生效状态": "已生效",
            })

    def task_row(tid: int, batch: str, sub: str, scope: str, total: int,
                 receipt_status: str) -> None:
        if store.find(TASK_MODULE, tid) is not None:
            return
        _rows(TASK_MODULE).append({
            "id": tid, "status": "已送达", "pending": False, "abnormal": False,
            "分发批次": batch, "分发编号": batch, "订阅方": sub,
            "记录范围": scope, "记录总数": total,
            "分发状态": "已送达", "回执状态": receipt_status,
            "实际送达时间": (base + timedelta(hours=2)).isoformat(),
        })
        _rows(T_DELIVERY).append({
            "id": tid, "分发编号": batch, "订阅方": sub,
            "实际送达时间": (base + timedelta(hours=2)).isoformat(),
            "签收渠道": "订阅方网关", "匹配状态": "已匹配",
        })

    task_row(4, "DIST-X-20261120-01", "X", "版本 v2026.11.20（12 条）", 12, "部分回收")
    task_row(5, "DIST-NOSIGN-20261122-01", "N（未签收）", "版本 v2026.11.22（6 条）", 6, "未回收")
    task_row(6, "DIST-REJECT-20261122-02", "R（拒收）", "版本 v2026.11.22（4 条）", 4, "已拒收")
    task_row(7, "DIST-8-20261124-08", "B（八达零售）",
             "整批 8 条（MDM-01 … MDM-08）", 8, "已回收")

    def receipt_row(rid: int, rc_no: str, tid_batch: str, sub: str,
                    status: str, rc_status: str, diff: str,
                    apply_no: str) -> None:
        if store.find(RECEIPT_MODULE, rid) is not None:
            return
        _rows(RECEIPT_MODULE).append({
            "id": rid, "status": status, "pending": status != "已回收",
            "abnormal": "破损" in diff or "拒收" in diff,
            "回执编号": rc_no, "分发批次": tid_batch, "分发编号": tid_batch,
            "订阅方": sub, "回执状态": rc_status, "差异说明": diff,
            "申请编号": apply_no,
        })

    receipt_row(4, "RC-X-LATE", "DIST-X-20261120-01", "X", "部分回收", "部分回收",
                "重发申请已受理，原分发签收迟到，待继续办理核对", "RA-X-01")
    receipt_row(5, "RC-N-GAP", "DIST-NOSIGN-20261122-01", "N（未签收）", "未回收", "未回收",
                "重发申请已受理，但订阅方没有签收记录", "RA-N-01")
    receipt_row(6, "RC-R-REJ", "DIST-REJECT-20261122-02", "R（拒收）", "未回收", "已拒收",
                "订阅方已经拒收该分发", "RA-R-01")
    receipt_row(7, "RC-8-FULL", "DIST-8-20261124-08", "B（八达零售）", "已回收",
                "已回收（2 条破损）", "整批 8 条已签收，其中 MDM-03、MDM-07 破损", "RA-8-02")

    # 第一次签收（X 的原签收迟到；8 条整批一次；R 已拒收；N 无签收）
    # 播种时不回写回执行，保留各场景“待继续办理”的设定状态。
    x_sign = submit_signoff(
        batch="DIST-X-20261120-01", result=SUCCESS, subscriber="X",
        scope="整批 12 条", count=12, source="原始签收（迟到，晚于重发申请）",
        sync_receipt=False)
    # 让 X 的签收 / 对账时间看起来确实晚于申请受理
    if x_sign.get("signoff"):
        x_sign["signoff"]["签收时间"] = (base + timedelta(days=3)).isoformat()

    submit_signoff(batch="DIST-REJECT-20261122-02", result="拒收",
                   subscriber="R（拒收）", scope="整批 4 条", count=4,
                   reason="外包装水浸，4 条介质不可读", source="订阅方",
                   sync_receipt=False)
    submit_signoff(batch="DIST-8-20261124-08", result=SUCCESS,
                   subscriber="B（八达零售）", scope="整批 8 条", count=8,
                   damaged=2, source="第一次签收（整批一次）",
                   sync_receipt=False)

    # 申请
    a1 = create_application(batch="DIST-X-20261120-01", subscriber="X",
                            apply_type="回执重发", receipt_id=4,
                            title="X 的重发申请")
    a1["申请编号"] = "RA-X-01"
    a1["status"] = "已受理"
    a1["发出状态"] = "待发出（还未实际发出）"
    a1["是否实际发出"] = False
    a1["sent"] = False

    a2 = create_application(batch="DIST-NOSIGN-20261122-01", subscriber="N（未签收）",
                            apply_type="回执重发", receipt_id=5)
    a2["申请编号"] = "RA-N-01"

    a3 = create_application(batch="DIST-REJECT-20261122-02", subscriber="R（拒收）",
                            apply_type="回执重发", receipt_id=6)
    a3["申请编号"] = "RA-R-01"

    a4 = create_application(batch="DIST-8-20261124-08", subscriber="B（八达零售）",
                            apply_type="补寄", receipt_id=7,
                            exception_no="YC-20261124-02",
                            approved_items=["MDM-03", "MDM-07"], approved_count=2,
                            title="8 条中 2 条破损的单独补寄申请")
    a4["申请编号"] = "RA-8-02"
    a4["status"] = "已核准"
    a4["发出状态"] = "待值班员继续办理"

    # 让按固定申请编号查找仍然成立（create_application 已写入默认编号，这里覆盖业务编号）
    for apply_no in ("RA-X-01", "RA-N-01", "RA-R-01", "RA-8-02"):
        pass


# 导入即播种，保证 uvicorn / 测试直接 import service 时场景就绪
ensure_dist_scenario()
