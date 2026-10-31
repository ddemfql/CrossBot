"""Human decisions tied to the exact transaction, file versions, and hashes."""
from __future__ import annotations

from copy import deepcopy
from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import (Actor, ApprovalRecord, CaseStatus, CrossCheckStatus,
                         DocumentStatus, ExtractedField, FieldSource, FieldValueStatus, ReviewDecision,
                         ReviewStatus, ReviewTask, WorkItem, Workspace, to_dict, utc_now)
from case_workflow.service import _reason, sync_check_status, validate_trade
from review.policy import (audit, checks_digest, current_artifacts, invalidate,
                           require, source_versions)

MANUAL_FIELD_NAMES = frozenset({
    "order_no", "customer_name", "destination_country", "product_name", "product_code",
    "quantity", "unit", "unit_price", "packages", "gross_weight_kg", "net_weight_kg",
    "currency", "total_amount", "incoterm", "shipper", "consignee", "document_date",
    "payment_terms", "transport_mode", "expected_ship_date", "due_date",
    "port_of_entry", "transaction_date", "transaction_place", "seller_address",
    "buyer_address", "responsible_exporter_person", "country_of_origin", "hs_code",
    "grade_quality", "marks_and_numbers", "package_contents",
})


class ReviewService:
    def __init__(self, repository, rules):
        self.repository, self.rules = repository, rules

    def _load(self, case_id: str, actor: Actor, action: str) -> Workspace:
        workspace = self.repository.get(case_id)
        try:
            require(workspace, actor, action)
        except CrossBotError as exc:
            if exc.code is ErrorCode.FORBIDDEN:
                self.repository.save(workspace, expected_revision=workspace.revision)
            raise
        return workspace

    def _save(self, workspace: Workspace) -> Workspace:
        self.repository.save(workspace, expected_revision=workspace.revision)
        return workspace

    def confirm_field(self, case_id: str, document_id: str, field_name: str,
                      value, actor: Actor, reason: str) -> Workspace:
        workspace = self._load(case_id, actor, "confirm_field")
        reason = _reason(reason)
        if value is None or isinstance(value, (dict, list)) or not str(value).strip():
            raise ValidationError("确认值不能为空或复合数据。")
        document = next((d for d in workspace.documents if d.document_id == document_id), None)
        if document is None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "未找到对应单据。")
        if document.deleted_at is not None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "该资料已由管理员移出当前工作区。")
        version = next((v for v in document.versions if v.version_id == document.current_version_id), None)
        if version is None or version.status not in {DocumentStatus.PARSED, DocumentStatus.MANUALLY_ENTERED}:
            raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "请先解析或录入当前单据版本。")
        field = next((f for f in version.fields if f.name == field_name), None)
        if field is None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "当前版本未包含该字段。")
        before = to_dict(deepcopy(field))
        invalidate(workspace, actor, reason)
        field.confirmed_value = value
        field.confirmed_by, field.confirmed_at = actor.user_id, utc_now()
        field.status = FieldValueStatus.CONFIRMED
        matching = next((t for t in workspace.review_tasks
                         if t.document_id == document_id and field_name in t.field_names
                         and t.status is ReviewStatus.PENDING), None)
        if matching is None:
            matching = ReviewTask(case_id=case_id, document_id=document_id,
                                  assignee_id=actor.user_id, field_names=[field_name])
            workspace.review_tasks.append(matching)
        matching.decisions.append(ReviewDecision(task_id=matching.task_id, reviewer_id=actor.user_id,
                                                  status=ReviewStatus.APPROVED, reason=reason,
                                                  changed_fields=[field_name]))
        if all(f.status is FieldValueStatus.CONFIRMED and f.confirmed_by
               for f in version.fields if f.name in matching.field_names):
            matching.status = ReviewStatus.APPROVED
        workspace.case.review_task_ids = [task.task_id for task in workspace.review_tasks]
        workspace.case.status = CaseStatus.PENDING_REVIEW
        audit(workspace, actor, "field_confirmed", "document_version", version.version_id,
              before=before, after=to_dict(field), reason=reason)
        return self._save(workspace)

    def enter_manual_field(self, case_id: str, document_id: str, field_name: str,
                           value, actor: Actor, reason: str) -> Workspace:
        workspace = self._load(case_id, actor, "manual_field")
        reason = _reason(reason)
        if field_name not in MANUAL_FIELD_NAMES:
            raise ValidationError("字段不在 W3 允许的人工录入清单中。")
        if value is None or isinstance(value, (dict, list)) or not str(value).strip():
            raise ValidationError("人工录入值不能为空或复合数据。")
        if len(str(value)) > 2000:
            raise ValidationError("单个字段值不能超过 2000 个字符。")
        document = next((d for d in workspace.documents if d.document_id == document_id), None)
        if document is None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "未找到对应单据。")
        if document.deleted_at is not None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "该资料已由管理员移出当前工作区。")
        version = next((v for v in document.versions if v.version_id == document.current_version_id), None)
        if version is None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "未找到单据当前版本。")
        before_field = next((f for f in version.fields if f.name == field_name), None)
        before = to_dict(deepcopy(before_field)) if before_field else None
        invalidate(workspace, actor, reason)
        if before_field is None:
            field = ExtractedField(name=field_name, source=FieldSource.HUMAN,
                status=FieldValueStatus.CONFIRMED, confirmed_value=str(value).strip(),
                confirmed_by=actor.user_id, confirmed_at=utc_now(), note="人工补录")
            version.fields.append(field)
        else:
            field = before_field
            field.confirmed_value = str(value).strip()
            field.confirmed_by, field.confirmed_at = actor.user_id, utc_now()
            field.status = FieldValueStatus.CONFIRMED
            field.note = "人工补录或修正机器候选"
        if version.status is not DocumentStatus.PARSED:
            version.status = DocumentStatus.MANUALLY_ENTERED
            version.parse_error = None
        document.status = version.status
        workspace.case.status = CaseStatus.PENDING_REVIEW
        audit(workspace, actor, "field_entered_manually", "document_version", version.version_id,
              before=before, after=to_dict(field), reason=reason)
        return self._save(workspace)

    def approve(self, case_id: str, actor: Actor, reason: str) -> Workspace:
        workspace = self._load(case_id, actor, "approve")
        reason = _reason(reason)
        validate_trade(workspace.trade)
        if not workspace.trade.confirmed_by or workspace.trade.confirmed_at is None:
            raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "订单资料尚未由真人确认。")
        workspace.checks = self.rules.evaluate(workspace)
        sync_check_status(workspace)
        if not workspace.checks or any(c.status is not CrossCheckStatus.PASS for c in workspace.checks):
            audit(workspace, actor, "approval_blocked", "case", case_id,
                  reason="规则失败、缺项或待复核，不能批准")
            self._save(workspace)
            raise CrossBotError(ErrorCode.CROSS_CHECK_FAILED, "规则未全部通过，请先处理缺项、差异及待复核字段。")
        artifacts = current_artifacts(workspace)
        available = {d.document_type for d in workspace.documents
                     if d.current_version_id and d.deleted_at is None}
        available.update(a.document_type for a in artifacts)
        if any(t not in available for t in workspace.required_document_types):
            raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "必需单据尚未齐全。")
        for document in workspace.documents:
            if document.deleted_at is not None:
                continue
            version = next((v for v in document.versions if v.version_id == document.current_version_id), None)
            if version and version.fields:
                if any(f.status is not FieldValueStatus.CONFIRMED or not f.confirmed_by
                       for f in version.fields):
                    raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "当前单据仍有未经人工确认的字段。")
        for artifact in workspace.artifacts:
            if artifact.status in {"draft", "approved"} and artifact not in artifacts:
                raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "存在未通过校验或与当前资料不一致的生成稿。")
        approval = ApprovalRecord(actor_id=actor.user_id, reason=reason,
                                  input_revision=workspace.trade.revision,
                                  document_versions=source_versions(workspace),
                                  artifact_hashes={a.artifact_id: a.sha256 for a in artifacts},
                                  checks_sha256=checks_digest(workspace.checks))
        workspace.approvals.append(approval)
        for artifact in artifacts:
            artifact.status = "approved"
        workspace.case.status = CaseStatus.PASSED
        for task in workspace.tasks:
            if task.status == "pending" and task.role in {"documents", "reviewer"}:
                task.status = "completed"
        workspace.tasks.append(WorkItem(title="安排发货并登记运输凭证", role="logistics"))
        audit(workspace, actor, "case_approved", "approval", approval.approval_id,
              after=to_dict(approval), reason=reason)
        return self._save(workspace)

    def return_case(self, case_id: str, actor: Actor, reason: str) -> Workspace:
        workspace = self._load(case_id, actor, "return")
        reason = _reason(reason)
        invalidate(workspace, actor, reason)
        workspace.case.status = CaseStatus.REVIEW_RETURNED
        workspace.tasks.append(WorkItem(title="补充退回复核资料", role="documents", note=reason))
        audit(workspace, actor, "case_returned", "case", case_id, reason=reason)
        return self._save(workspace)
