"""Originals, fixed-template candidates and immutable seller draft versions."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from pathlib import PurePath
import re

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import (CaseStatus, CrossCheckResult, CrossCheckStatus, Document,
    DocumentStatus, DocumentType, DocumentVersion, GeneratedArtifact, ReviewTask,
    to_dict, utc_now)
from core.ports import BlobStore, DocumentRenderer, WorkspaceRepository
from review.policy import audit, invalidate, require
from validation.rules import validate_trade

from .parsers import FixedTemplateParser, PARSER_VERSION
from .rendering import HTMLRenderer, TITLES
from .workbook_renderer import XLSXRenderer
from .catalog import SELLER_TYPES
from .forms import form_conflicts, get_form, missing_form_fields, render_form

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MEDIA_TYPES = {
    ".pdf": "application/pdf", ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".txt": "text/plain", ".csv": "text/csv",
}


class DocumentService:
    def __init__(self, repository: WorkspaceRepository, blobs: BlobStore,
                 renderer: DocumentRenderer | None = None):
        self.repository, self.blobs = repository, blobs
        self.renderer = renderer or XLSXRenderer()
        self.parser = FixedTemplateParser()

    def _save(self, workspace):
        self.repository.save(workspace, expected_revision=workspace.revision)
        return workspace

    def _authorize(self, workspace, actor, action):
        try:
            require(workspace, actor, action)
            if action != "download" and workspace.case.status is CaseStatus.CLOSED:
                raise CrossBotError(ErrorCode.CONFLICT, "已关闭的交易不可修改。")
        except CrossBotError as exc:
            # require() records forbidden access; persist its event without duplicating it.
            if exc.code is not ErrorCode.FORBIDDEN:
                audit(workspace, actor, "mutation_rejected", entity_type="case",
                      entity_id=workspace.case.case_id, reason=action)
            self._save(workspace)
            raise

    @staticmethod
    def _document(workspace, document_id):
        document = next((d for d in workspace.documents if d.document_id == document_id), None)
        if document is None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "未找到该交易的文件。")
        return document

    @staticmethod
    def _current(document):
        version = next((v for v in document.versions if v.version_id == document.current_version_id), None)
        if version is None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "未找到文件当前版本。")
        return version

    def upload(self, case_id, actor, filename, content, document_type,
               document_id=None, source_party="", expires_on=None):
        workspace = self.repository.get(case_id)
        self._authorize(workspace, actor, "upload")
        if (not isinstance(filename, str) or not filename.strip() or len(filename) > 255
                or filename in {".", ".."} or any(ord(c) < 32 for c in filename)
                or "/" in filename or "\\" in filename):
            raise ValidationError("文件名不能为空或包含路径及控制字符。")
        extension = PurePath(filename).suffix.lower()
        if extension not in MEDIA_TYPES:
            raise CrossBotError(ErrorCode.FILE_TYPE_UNSUPPORTED, "暂不接受该文件格式。")
        if not isinstance(content, bytes) or not content:
            raise ValidationError("文件内容不能为空。")
        if len(content) > MAX_UPLOAD_BYTES:
            raise CrossBotError(ErrorCode.FILE_TOO_LARGE, "单个文件不能超过 20 MB。")
        try:
            document_type = DocumentType(document_type)
        except (ValueError, TypeError) as exc:
            raise ValidationError("请选择有效的文件类型。") from exc
        if not isinstance(source_party, str) or len(source_party) > 500:
            raise ValidationError("文件来源方不能超过 500 字。")
        if expires_on:
            try:
                date.fromisoformat(expires_on)
            except (ValueError, TypeError) as exc:
                raise ValidationError("有效期须为 YYYY-MM-DD。") from exc
        document = self._document(workspace, document_id) if document_id else Document(
            case_id=case_id, document_type=document_type, title=filename,
            required=document_type in workspace.required_document_types)
        if document.deleted_at is not None:
            raise CrossBotError(ErrorCode.CONFLICT, "已删除的资料不能新增版本；请创建一份新资料。")
        if document.document_type is not document_type:
            raise CrossBotError(ErrorCode.CONFLICT, "修订版的文件类型必须与原文件一致。")
        key = self.blobs.put(content)
        invalidate(workspace, actor, "文件原件新增或修订，需重新复核。")
        before = {"version_id": document.current_version_id}
        for old in document.versions:
            if old.version_id == document.current_version_id:
                old.status = DocumentStatus.SUPERSEDED
        version = DocumentVersion(document_id=document.document_id,
            version_number=max((v.version_number for v in document.versions), default=0) + 1,
            sha256=sha256(content).hexdigest(), original_filename=filename,
            storage_path=key, uploaded_by=actor.user_id,
            media_type=MEDIA_TYPES[extension], size_bytes=len(content),
            source_party=source_party.strip(), expires_on=expires_on or None)
        document.versions.append(version)
        document.current_version_id = version.version_id
        document.status = DocumentStatus.UPLOADED
        if not document_id:
            workspace.documents.append(document)
            workspace.case.document_ids.append(document.document_id)
        from .prefill import refresh_prefills
        refresh_prefills(workspace)
        audit(workspace, actor, "document_uploaded", entity_type="document",
              entity_id=document.document_id, before=before,
              after={"version_id": version.version_id, "sha256": version.sha256,
                     "size_bytes": version.size_bytes, "source_party": version.source_party,
                     "document_type": document_type.value},
              reason="新原件" if not document_id else "原件修订，保留历史版本")
        workspace.case.updated_at = utc_now()
        return self._save(workspace)

    def parse(self, case_id, document_id, actor):
        workspace = self.repository.get(case_id)
        self._authorize(workspace, actor, "parse")
        document = self._document(workspace, document_id)
        if document.deleted_at is not None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "该资料已移出当前工作区。")
        version = self._current(document)
        if version.status is DocumentStatus.PARSED:
            return workspace  # Do not erase candidates or human decisions on a rerun.
        extension = PurePath(version.original_filename).suffix.lower()
        if extension not in {".pdf", ".xls", ".xlsx", ".docx", ".jpg", ".jpeg", ".png", ".webp"}:
            audit(workspace, actor, "parse_unsupported", entity_type="document",
                  entity_id=document_id, after={"version_id": version.version_id},
                  reason="原件已保存；该格式暂无解析器。")
            self._save(workspace)
            raise CrossBotError(ErrorCode.FILE_TYPE_UNSUPPORTED, "该格式已保存原件，暂不支持字段解析。")
        invalidate(workspace, actor, "解析结果变化，需复核当前文件版本。")
        version.status = document.status = DocumentStatus.PARSING
        workspace.case.status = CaseStatus.PARSING
        audit(workspace, actor, "parse_started", entity_type="document", entity_id=document_id,
              after={"version_id": version.version_id})
        self._save(workspace)
        try:
            content = self.blobs.get(version.storage_path)
            if sha256(content).hexdigest() != version.sha256:
                raise CrossBotError(ErrorCode.CONFLICT, "原件完整性校验失败。")
            fields = self.parser.parse(content, extension)
        except Exception as exc:
            # Persist the failure without logging source content or arbitrary exception text.
            version.status = document.status = DocumentStatus.PARSE_FAILED
            version.parse_error = (exc.message if isinstance(exc, CrossBotError)
                                   else "解析失败，请核对固定模板或补充人工资料。")
            workspace.case.status = CaseStatus.PENDING_REVIEW
            audit(workspace, actor, "parse_failed", entity_type="document", entity_id=document_id,
                  after={"version_id": version.version_id, "code": "parse_failed"},
                  reason=version.parse_error)
            self._save(workspace)
            return workspace
        version.fields = fields
        version.parser_name = "fixed-template" + extension
        version.parser_version = PARSER_VERSION
        version.parse_error = None
        version.status = document.status = DocumentStatus.PARSED
        task = ReviewTask(case_id=case_id, document_id=document_id,
                          required=document.required, field_names=[f.name for f in fields])
        workspace.review_tasks.append(task)
        workspace.case.review_task_ids.append(task.task_id)
        workspace.case.status = CaseStatus.PENDING_REVIEW
        audit(workspace, actor, "document_parsed", entity_type="document", entity_id=document_id,
              after={"version_id": version.version_id, "fields": [f.name for f in fields],
                     "parser_version": PARSER_VERSION})
        from .catalog import FORM_FIELDS
        from .prefill import prefill_form, refresh_prefills
        refresh_prefills(workspace)
        if document.document_type in FORM_FIELDS:
            form = get_form(workspace, document.document_type)
            prefill_form(workspace, document.document_type, [*form.source_document_ids, document_id])
        workspace.case.updated_at = utc_now()
        return self._save(workspace)

    def generate(self, case_id, actor, document_type):
        workspace = self.repository.get(case_id)
        self._authorize(workspace, actor, "generate")
        try:
            document_type = DocumentType(document_type)
        except (ValueError, TypeError) as exc:
            raise ValidationError("请选择有效的草稿类型。") from exc
        if document_type not in SELLER_TYPES:
            raise CrossBotError(ErrorCode.FILE_TYPE_UNSUPPORTED,
                                "该类型由买方或外部机构提供，仅可托管原件。")
        if not workspace.trade.confirmed_by or workspace.trade.confirmed_at is None:
            raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "请先由授权人员确认交易资料。")
        checks = validate_trade(workspace)
        form = get_form(workspace, document_type)
        uses_form = bool(form.revision)
        if document_type not in TITLES or uses_form:
            if not form.confirmed_by or form.confirmed_at is None:
                raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "请先由真人核对并确认本类型填写表单。")
            missing = missing_form_fields(workspace.trade, form)
            checks.append(CrossCheckResult(case_id=case_id, rule_code="FORM_COMPLETE",
                status=CrossCheckStatus.PENDING_REVIEW if missing else CrossCheckStatus.PASS,
                message="表单待补字段：" + "、".join(missing) if missing else "表单所需字段已填写，须经业务规则与真人批准。",
                left_field="document_form", left_value=missing,
                left_source=f"form:{document_type.value}:r{form.revision}", rule_version="w3-forms-1"))
            conflicts = form_conflicts(workspace.trade, form)
            checks.append(CrossCheckResult(case_id=case_id, rule_code="FORM_CONSISTENCY",
                status=CrossCheckStatus.FAIL if conflicts else CrossCheckStatus.PASS,
                message="；".join(conflicts) if conflicts else "填写表单的交易标识和已填数字一致。",
                left_field="document_form", left_value=conflicts,
                left_source=f"form:{document_type.value}:r{form.revision}", rule_version="w3-forms-1"))
        try:
            if document_type not in TITLES:
                output = render_form(document_type, workspace.trade, form)
            elif uses_form and isinstance(self.renderer, XLSXRenderer):
                output = self.renderer.render(document_type, workspace.trade, form=form)
            elif uses_form:
                output = render_form(document_type, workspace.trade, form)
            else:
                output = self.renderer.render(document_type, workspace.trade)
            if not isinstance(output.content, bytes) or not output.content:
                raise ValueError("empty output")
            if (not output.template_id or not output.template_version
                    or not re.fullmatch(r"[0-9a-f]{64}", output.template_sha256)
                    or output.extension not in {"html", "pdf", "xlsx"}):
                raise ValueError("invalid renderer metadata")
            if output.extension == "html":
                text = output.content.decode("utf-8")
                unresolved = bool(re.search(r"\$\{[a-zA-Z_][a-zA-Z_0-9]*\}|\{\{[^{}]+\}\}", text))
            else:
                unresolved = False
            checks.append(CrossCheckResult(case_id=case_id, rule_code="RENDER_COMPLETE",
                status=CrossCheckStatus.FAIL if unresolved else CrossCheckStatus.PASS,
                message="模板仍有未替换变量。" if unresolved else "草稿渲染完成，模板变量已替换。",
                left_field="template", left_value=output.template_id,
                right_field="input_revision", right_value=workspace.trade.revision,
                left_source=f"template:{output.template_version}",
                right_source=f"trade:{workspace.trade.revision}", rule_version="w3-1"))
            key = self.blobs.put(output.content)
        except Exception as exc:
            audit(workspace, actor, "generation_failed", entity_type="artifact",
                  after={"document_type": document_type.value, "input_revision": workspace.trade.revision},
                  reason="草稿渲染或保存失败，未生成可用产物。")
            self._save(workspace)
            raise CrossBotError(ErrorCode.CONFIGURATION, "草稿生成失败，可核对模板后重试。") from exc
        old_versions = [a for a in workspace.artifacts if a.document_type is document_type]
        for artifact in workspace.artifacts:
            if artifact.document_type is document_type:
                artifact.status = "stale"
            elif artifact.status == "approved":
                artifact.status = "draft"
        artifact = GeneratedArtifact(document_type=document_type,
            version_number=max((a.version_number for a in old_versions), default=0) + 1,
            input_revision=workspace.trade.revision, input_snapshot=to_dict(workspace.trade),
            form_snapshot=to_dict(form) if uses_form else {},
            form_revision=form.revision if uses_form else 0,
            source_versions={d.document_id: d.current_version_id for d in workspace.documents
                             if d.current_version_id and d.deleted_at is None},
            template_id=output.template_id, template_version=output.template_version,
            template_sha256=output.template_sha256, storage_path=key,
            sha256=sha256(output.content).hexdigest(), media_type=output.media_type,
            filename=f"{document_type.value}-r{workspace.trade.revision}-v{len(old_versions) + 1}.{output.extension}",
            created_by=actor.user_id, status="draft" if checks and all(
                c.status is CrossCheckStatus.PASS for c in checks) else "invalid", checks=checks)
        workspace.artifacts.append(artifact)
        workspace.checks = []
        workspace.case.cross_check_result_ids = []
        workspace.case.status = CaseStatus.PENDING_REVIEW
        workspace.case.updated_at = utc_now()
        audit(workspace, actor, "artifact_generated", entity_type="artifact", entity_id=artifact.artifact_id,
              after={"version": artifact.version_number, "input_revision": artifact.input_revision,
                     "template_id": artifact.template_id, "template_sha256": artifact.template_sha256,
                     "sha256": artifact.sha256, "status": artifact.status},
              reason="生成新版本，需人工复核后批准。")
        return self._save(workspace)

    def download(self, case_id, actor, document_id=None, version_id=None, artifact_id=None):
        workspace = self.repository.get(case_id)
        self._authorize(workspace, actor, "download")
        if bool(document_id) == bool(artifact_id):
            raise ValidationError("请指定一份原件或生成草稿。")
        if artifact_id:
            item = next((a for a in workspace.artifacts if a.artifact_id == artifact_id), None)
            if item is None:
                raise CrossBotError(ErrorCode.NOT_FOUND, "未找到该交易的生成文件。")
        else:
            document = self._document(workspace, document_id)
            if document.deleted_at is not None:
                raise CrossBotError(ErrorCode.NOT_FOUND, "该资料已移出当前工作区。")
            item = (next((v for v in document.versions if v.version_id == version_id), None)
                    if version_id else self._current(document))
            if item is None:
                raise CrossBotError(ErrorCode.NOT_FOUND, "未找到该文件版本。")
        content = self.blobs.get(item.storage_path)
        if sha256(content).hexdigest() != item.sha256:
            raise CrossBotError(ErrorCode.CONFLICT, "文件完整性校验失败。")
        audit(workspace, actor, "file_downloaded", entity_type="artifact" if artifact_id else "document",
              entity_id=artifact_id or document_id,
              after={"sha256": item.sha256, "version_id": artifact_id or item.version_id})
        self._save(workspace)
        return content

    def delete_document(self, case_id, document_id, actor, reason):
        """Logical removal by a business user; preserve originals and history."""
        workspace = self.repository.get(case_id)
        self._authorize(workspace, actor, "delete_document")
        document = self._document(workspace, document_id)
        if document.deleted_at is not None:
            raise CrossBotError(ErrorCode.CONFLICT, "该资料已从当前工作区移除。")
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError("移除资料时必须填写原因。")
        invalidate(workspace, actor, "移除资料，相关生成文件需重新核对。")
        before = {"document_type": document.document_type.value,
                  "current_version_id": document.current_version_id,
                  "versions": [{"version_id": version.version_id, "sha256": version.sha256}
                               for version in document.versions]}
        document.deleted_at = utc_now()
        document.deleted_by = actor.user_id
        document.deletion_reason = reason.strip()
        from .prefill import refresh_prefills
        refresh_prefills(workspace)
        audit(workspace, actor, "document_removed_by_admin" if "admin" in actor.roles
              else "document_removed", "document", document_id,
              before=before, after={"deleted_at": document.deleted_at.isoformat()},
              reason=reason.strip())
        return self._save(workspace)
