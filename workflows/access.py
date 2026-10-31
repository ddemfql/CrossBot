"""V3 generation gate and immutable AI/manual drafts; B supplies model calls."""
from __future__ import annotations

import copy
import json

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.identity import Permission
from core.models import ExtractedField, FieldSource, FieldValueStatus, from_dict, to_dict
from core.schemas import GenerationResponse, ListingRequest, SalesRequest
from core.v3 import GenerationArtifact, LifecycleStatus, RequestContext, ResourceKind
from documents.access import AttachmentService
from knowledge.governance import KnowledgeService
from storage.v3_store import encode


class GenerationService:
    def __init__(self, auth):
        self.auth = auth
        self.repository = auth.repository

    def authorize_request(self, token, request, *, simulate=False):
        if not isinstance(request, (SalesRequest, ListingRequest)) or request.context is None:
            raise ValidationError("V3 生成请求必须带 RequestContext")
        request = copy.deepcopy(request)
        action = Permission.CUSTOMER_SIMULATE if simulate else Permission.W1_GENERATE if isinstance(request, SalesRequest) else Permission.W2_GENERATE
        actor = self.auth.require_permission(token, action, request.context.case_id)
        if simulate and not isinstance(request, SalesRequest):
            raise ValidationError("客户模拟使用 SalesRequest")
        if request.context.actor_id and request.context.actor_id != actor.user_id:
            raise CrossBotError(ErrorCode.PERMISSION_DENIED, "请求主体不能冒充其他用户")
        if isinstance(request, SalesRequest) and (not request.inquiry.strip() or not request.product_info.strip() or not request.target_language.strip()):
            raise ValidationError("询盘、产品资料和目标语言不能为空")
        if isinstance(request, ListingRequest) and (not request.product_description.strip() or not request.platforms or not request.target_language.strip()):
            raise ValidationError("产品描述、平台和语言不能为空")
        context = RequestContext(request.context.case_id, actor_id=actor.user_id,
                                 product_version_id=request.context.product_version_id,
                                 attachments=request.context.attachments)
        if context.product_version_id:
            version = KnowledgeService(self.auth).get_version(token, context.product_version_id)
            if version.kind != ResourceKind.PRODUCT or version.status not in {LifecycleStatus.PUBLISHED, LifecycleStatus.ARCHIVED}:
                raise ValidationError("模型只能引用已发布产品版本")
        for reference in context.attachments:
            attachment = AttachmentService(self.auth).read(token, reference, require_ready=True)
            if attachment.case_id != context.case_id:
                raise ValidationError("附件不属于请求 Case")
            for field in attachment.fields:
                if field.status == "confirmed":
                    context.confirmed_fields.append(ExtractedField(
                        name=field.field_name, value=field.confirmed_value,
                        source=FieldSource.HUMAN, status=FieldValueStatus.CONFIRMED,
                        page_number=field.location.page_number, sheet_name=field.location.sheet_name,
                        cell_reference=field.location.cell_reference,
                    ))
        # Caller-supplied confirmed_fields are discarded, never promoted to facts.
        request.context = context
        with self.repository.transaction() as conn:
            self.auth._require(conn, token, action, context.case_id)
            self.repository.audit(conn, "generation_authorized", actor.user_id, case_id=context.case_id, after={"action": action.value, "correlation_id": request.correlation_id})
        return request

    def generate(self, token, request, provider, *, simulate=False):
        request = self.authorize_request(token, request, simulate=simulate)
        # Keep the authorized input snapshot independent of provider-side mutation.
        response = provider(copy.deepcopy(request))
        return self._save_response(token, request, response, simulate=simulate)

    def save_result(self, token, request, response, *, simulate=False):
        """For streaming adapters: gate before streaming AND recheck before saving."""
        snapshot = copy.deepcopy(request)
        checked = self.authorize_request(token, request, simulate=simulate)
        if snapshot.context is None or to_dict(snapshot.context) != to_dict(checked.context):
            raise CrossBotError(ErrorCode.CONFLICT, "生成上下文已变化，请重新授权并生成")
        return self._save_response(token, checked, response, simulate=simulate)

    def _save_response(self, token, request, response, *, simulate=False):
        if not isinstance(response, GenerationResponse):
            raise ValidationError("模型适配器必须返回 GenerationResponse")
        action = Permission.CUSTOMER_SIMULATE if simulate else Permission.W1_GENERATE if isinstance(request, SalesRequest) else Permission.W2_GENERATE
        with self.repository.transaction() as conn:
            actor = self.auth._require(conn, token, action, request.context.case_id)
            artifact = GenerationArtifact(
                request.context.case_id, actor.user_id,
                "customer_simulation" if simulate else "sales" if isinstance(request, SalesRequest) else "listing",
                response.content, request.context, origin="simulation" if simulate else "ai",
                risks=to_dict(response.risks),
                pending_confirmations=list(response.pending_confirmations), provider=response.provider,
                model=response.model, request_payload=to_dict(request),
            )
            conn.execute("INSERT INTO generation_artifacts VALUES (?, ?, ?)", (artifact.artifact_id, artifact.case_id, encode(artifact)))
            self.repository.audit(conn, "generation_saved", actor.user_id, case_id=artifact.case_id, entity_id=artifact.artifact_id, after={"origin": artifact.origin})
            return artifact

    def _load(self, conn, artifact_id):
        row = conn.execute("SELECT payload FROM generation_artifacts WHERE id=?", (artifact_id,)).fetchone()
        if not row:
            raise CrossBotError(ErrorCode.NOT_FOUND, "生成记录不存在")
        return from_dict(GenerationArtifact, json.loads(row["payload"]))

    def read(self, token, artifact_id):
        with self.repository.transaction() as conn:
            artifact = self._load(conn, artifact_id)
            self.auth._require(conn, token, Permission.CASE_READ, artifact.case_id)
            return artifact

    def list_for_case(self, token, case_id, *, workflow=None):
        with self.repository.transaction() as conn:
            self.auth._require(conn, token, Permission.CASE_READ, case_id)
            artifacts = [from_dict(GenerationArtifact, json.loads(row["payload"])) for row in conn.execute("SELECT payload FROM generation_artifacts WHERE case_id=? ORDER BY rowid", (case_id,))]
            return [item for item in artifacts if workflow is None or item.workflow == workflow]

    def edit(self, token, artifact_id, content):
        if not content.strip():
            raise ValidationError("人工稿不能为空")
        with self.repository.transaction() as conn:
            previous = self._load(conn, artifact_id)
            action = Permission.W2_GENERATE if previous.workflow == "listing" else Permission.W1_GENERATE
            actor = self.auth._require(conn, token, action, previous.case_id)
            if previous.origin == "simulation":
                raise ValidationError("模拟客户输出不能直接转为业务最终稿")
            artifact = GenerationArtifact(previous.case_id, actor.user_id, previous.workflow, content,
                                          previous.context, parent_artifact_id=previous.artifact_id,
                                          origin="manual", risks=previous.risks,
                                          pending_confirmations=previous.pending_confirmations,
                                          request_payload=previous.request_payload)
            conn.execute("INSERT INTO generation_artifacts VALUES (?, ?, ?)", (artifact.artifact_id, artifact.case_id, encode(artifact)))
            self.repository.audit(conn, "manual_draft_saved", actor.user_id, case_id=artifact.case_id, entity_id=artifact.artifact_id)
            return artifact

    def review(self, token, artifact_id, *, approve, reason):
        if not reason.strip():
            raise ValidationError("复核需要原因")
        with self.repository.transaction() as conn:
            artifact = self._load(conn, artifact_id)
            actor = self.auth._require(conn, token, Permission.GENERATION_REVIEW, artifact.case_id)
            if actor.user_id == artifact.actor_id or artifact.origin == "simulation" or artifact.review_status != "pending_review":
                raise ValidationError("不能自审、审核模拟内容或重复审核")
            artifact.review_status = "approved" if approve else "returned"
            artifact.reviewed_by = actor.user_id
            conn.execute("UPDATE generation_artifacts SET payload=? WHERE id=?", (encode(artifact), artifact_id))
            self.repository.audit(conn, "generation_reviewed", actor.user_id, case_id=artifact.case_id, entity_id=artifact_id, after={"status": artifact.review_status}, reason=reason)
            return artifact
