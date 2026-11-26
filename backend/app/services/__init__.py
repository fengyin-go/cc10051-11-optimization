"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.source_system import SourceSystemService
from app.services.entity_model import EntityModelService
from app.services.record_extract import RecordExtractService
from app.services.std_cleanse import StdCleanseService
from app.services.dup_detect import DupDetectService
from app.services.golden_merge import GoldenMergeService
from app.services.golden_publish import GoldenPublishService
from app.services.subscriber_mgr import SubscriberMgrService
from app.services.dist_task import DistTaskService
from app.services.lineage_graph import LineageGraphService
from app.services.quality_rule import QualityRuleService
from app.services.quality_result import QualityResultService
from app.services.merge_review import MergeReviewService
from app.services.version_rollback import VersionRollbackService
from app.services.sync_cursor import SyncCursorService
from app.services.id_cross_map import IdCrossMapService
from app.services.dist_receipt import DistReceiptService
from app.services.model_change import ModelChangeService
from app.services.stats_overview import StatsOverviewService
from app.services.ref_data_mgr import RefDataMgrService
from app.services.flow_tables import FlowTableService, ResendApplyService, SignoffService

# 办理流新增数据表（同一实例可挂多个资源别名）
_signoff_svc = SignoffService()
_signoff_svc.MODULE = "signoff"
_recon_svc = FlowTableService()
_recon_svc.MODULE = "reconciliation"
_disp_svc = FlowTableService()
_disp_svc.MODULE = "disposition_receipt"
_delivery_svc = FlowTableService()
_delivery_svc.MODULE = "delivery_record"
_event_svc = FlowTableService()
_event_svc.MODULE = "dist_event"
_apply_svc = ResendApplyService()

SERVICES: dict[str, BaseModuleService] = {
    "source_system": SourceSystemService(),
    "entity_model": EntityModelService(),
    "record_extract": RecordExtractService(),
    "std_cleanse": StdCleanseService(),
    "dup_detect": DupDetectService(),
    "golden_merge": GoldenMergeService(),
    "golden_publish": GoldenPublishService(),
    "subscriber_mgr": SubscriberMgrService(),
    "dist_task": DistTaskService(),
    "lineage_graph": LineageGraphService(),
    "quality_rule": QualityRuleService(),
    "quality_result": QualityResultService(),
    "merge_review": MergeReviewService(),
    "version_rollback": VersionRollbackService(),
    "sync_cursor": SyncCursorService(),
    "id_cross_map": IdCrossMapService(),
    "dist_receipt": DistReceiptService(),
    "model_change": ModelChangeService(),
    "stats_overview": StatsOverviewService(),
    "ref_data_mgr": RefDataMgrService(),
    # ---- 分发回执办理流：可直接 GET /api/<key> 核对数量 ----
    "resend_apply": _apply_svc,
    "resend_applications": _apply_svc,
    "resend_request": _apply_svc,
    "resend_requests": _apply_svc,
    "receipt_apply": _apply_svc,
    "receipt_applications": _apply_svc,
    "signoff": _signoff_svc,
    "signoffs": _signoff_svc,
    "signoff_record": _signoff_svc,
    "signoff_records": _signoff_svc,
    "receipt_signoff": _signoff_svc,
    "reconciliation": _recon_svc,
    "reconciliations": _recon_svc,
    "reconcile": _recon_svc,
    "reconcile_record": _recon_svc,
    "reconcile_records": _recon_svc,
    "reconcile_confirm": _recon_svc,
    "reconcile_confirms": _recon_svc,
    "confirmation": _recon_svc,
    "confirmations": _recon_svc,
    "disposition_receipt": _disp_svc,
    "disposition_receipts": _disp_svc,
    "disposition": _disp_svc,
    "dispositions": _disp_svc,
    "delivery_record": _delivery_svc,
    "delivery_records": _delivery_svc,
    "delivery_receipt": _delivery_svc,
    "delivery_receipts": _delivery_svc,
    "actual_delivery": _delivery_svc,
    "actual_deliveries": _delivery_svc,
    "dist_event": _event_svc,
    "dist_events": _event_svc,
}


def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
