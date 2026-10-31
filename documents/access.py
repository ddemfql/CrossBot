"""Authorized attachment handoff; C owns upload bytes and actual parsers."""
from __future__ import annotations

import copy
import json

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.identity import Permission
from core.models import Document, DocumentStatus, ExtractedField, FieldSource, FieldValueStatus, from_dict, to_dict, utc_now
from core.v3 import AttachmentContent, AttachmentStatus
from storage.v3_store import encode


class AttachmentService:
    def __init__(self, auth):
        self.auth = auth
        self.repository = auth.repository

    def _load(self, conn, version_id):
        row = conn.execute("SELECT payload FROM attachment_contents WHERE version_id=?", (version_id,)).fetchone()
        if not row:
            raise CrossBotError(ErrorCode.NOT_FOUND, "附件尚未提供解析结果")
        return from_dict(AttachmentContent, json.loads(row["payload"]))

    @staticmethod
    def _project_fields(conn, content):
        """Keep C/D's existing DocumentVersion contract aligned with V3 reviews."""
        row = conn.execute("SELECT payload FROM documents WHERE id=?", (content.reference.document_id,)).fetchone()
        document = from_dict(Document, json.loads(row["payload"]))
        version = next(v for v in document.versions if v.version_id == content.reference.version_id)
        version.status = DocumentStatus.PARSED if content.status == AttachmentStatus.READY else DocumentStatus.PARSE_FAILED if content.status == AttachmentStatus.FAILED else DocumentStatus.PARSING
        version.parse_error = content.error
        version.parser_name = content.method
        version.fields = [ExtractedField(
            name=f.field_name, value=f.confirmed_value if f.status == "confirmed" else f.candidate_value,
            normalized_value=f.confirmed_value if f.status == "confirmed" else None,
            source=FieldSource.HUMAN if f.status == "confirmed" else FieldSource.MACHINE,
            status=FieldValueStatus.CONFIRMED if f.status == "confirmed" else FieldValueStatus.CANDIDATE,
            page_number=f.location.page_number, sheet_name=f.location.sheet_name, cell_reference=f.location.cell_reference,
        ) for f in content.fields]
        if document.current_version_id == version.version_id:
            document.status = version.status
        conn.execute("UPDATE documents SET payload=? WHERE id=?", (encode(document), document.document_id))

    def save_document(self, token, document):
        """C stores bytes first, then registers immutable original-file metadata here."""
        with self.repository.transaction() as conn:
            actor = self.auth._require(conn, token, Permission.ATTACHMENT_WRITE, document.case_id)
            row = conn.execute("SELECT payload FROM documents WHERE id=?", (document.document_id,)).fetchone()
            if row:
                previous = json.loads(row["payload"])
                if previous["case_id"] != document.case_id:
                    raise ValidationError("不得将原件移动到其他 Case")
                new_versions = {v.version_id: v for v in document.versions}
                for old in previous["versions"]:
                    new = new_versions.get(old["version_id"])
                    if not new or to_dict(new) != old:
                        raise ValidationError("不得覆盖或删除已保存的原件版本")
            if not document.versions or not document.current_version_id or document.current_version_id not in {v.version_id for v in document.versions}:
                raise ValidationError("原件必须有有效的当前版本")
            if len({v.version_number for v in document.versions}) != len(document.versions) or len({v.version_id for v in document.versions}) != len(document.versions):
                raise ValidationError("版本号或版本 ID 重复")
            for version in document.versions:
                if version.document_id != document.document_id or version.version_number < 1:
                    raise ValidationError("文档版本关联不正确")
                import re
                if not re.fullmatch(r"[0-9a-fA-F]{64}", version.sha256):
                    raise ValidationError("文件哈希必须为 64 位 SHA-256")
            others = conn.execute("SELECT payload FROM documents WHERE case_id=? AND id<>?", (document.case_id, document.document_id)).fetchall()
            hashes = {version.sha256 for version in document.versions}
            if any(hashes.intersection(v["sha256"] for v in json.loads(other["payload"])["versions"]) for other in others):
                raise CrossBotError(ErrorCode.CONFLICT, "该 Case 已有相同原文件")
            conn.execute("INSERT INTO documents(id,case_id,payload) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (document.document_id, document.case_id, encode(document)))
            self.repository.audit(conn, "document_saved", actor.user_id, case_id=document.case_id, entity_id=document.document_id, after={"current_version_id": document.current_version_id})
            return document

    def register(self, token, content: AttachmentContent):
        content = copy.deepcopy(content)
        self.auth.require_permission(token, Permission.ATTACHMENT_WRITE, content.case_id)
        if not content.method.strip():
            raise ValidationError("必须填写识别方式")
        if content.confidence is not None and not 0 <= content.confidence <= 1:
            raise ValidationError("识别置信度应在 0 和 1 之间")
        if len({field.field_name for field in content.fields}) != len(content.fields):
            raise ValidationError("字段名重复，请用 items[0].quantity 等路径区分")
        for field in content.fields:
            if field.status != "candidate" or field.confirmed_by or field.confirmed_at or field.confirmed_value is not None or field.history:
                raise ValidationError("解析器不能自行设置人工确认或修改历史")
            location = field.location
            if location.page_number is not None and location.page_number < 1:
                raise ValidationError("页码从 1 开始")
            if not (location.page_number or location.text_excerpt or
                    (location.sheet_name and location.cell_reference) or
                    (location.row_number and location.column_name)):
                raise ValidationError("字段必须保留原文位置")
            if location.image_region is not None and (len(location.image_region) != 4 or any(not isinstance(v, (float, int)) for v in location.image_region)):
                raise ValidationError("图片区域使用 [x0, y0, x1, y1]")
        if content.status == AttachmentStatus.FAILED and not content.error:
            raise ValidationError("解析失败必须保留原因")
        if not content.text.strip() and not content.fields:
            if content.status != AttachmentStatus.FAILED:
                content.status = AttachmentStatus.PENDING_REVIEW
        if content.error or content.confidence is None or content.confidence < 0.8:
            if content.status != AttachmentStatus.FAILED:
                content.status = AttachmentStatus.PENDING_REVIEW
        with self.repository.transaction() as conn:
            actor = self.auth._require(conn, token, Permission.ATTACHMENT_WRITE, content.case_id)
            row = conn.execute("SELECT payload FROM documents WHERE id=?", (content.reference.document_id,)).fetchone()
            if not row:
                raise CrossBotError(ErrorCode.NOT_FOUND, "请先由 C 保存原件和文档版本")
            document = json.loads(row["payload"])
            versions = [v for v in document["versions"] if v["version_id"] == content.reference.version_id]
            if document["case_id"] != content.case_id or not versions or not versions[0]["sha256"] or versions[0]["document_id"] != content.reference.document_id:
                raise ValidationError("附件 Case、版本或文件哈希不匹配")
            if conn.execute("SELECT version_id FROM attachment_contents WHERE version_id=?", (content.reference.version_id,)).fetchone():
                raise CrossBotError(ErrorCode.CONFLICT, "识别结果已存在，重新识别请建立新文档版本")
            conn.execute("INSERT INTO attachment_contents VALUES (?, ?, ?, ?)", (content.reference.version_id, content.reference.document_id, content.case_id, encode(content)))
            self._project_fields(conn, content)
            self.repository.audit(conn, "attachment_parsed", actor.user_id, case_id=content.case_id, entity_type="attachment", entity_id=content.reference.version_id, after={"status": content.status.value, "method": content.method, "error": content.error})
            return content

    def get_document_version(self, token, reference):
        """Read original-file metadata under Case authorization (bytes stay with C)."""
        document = self.auth.store.get_document(reference.document_id)
        if document is None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "文档不存在")
        self.auth.require_permission(token, Permission.ATTACHMENT_READ, document.case_id)
        versions = [v for v in document.versions if v.version_id == reference.version_id]
        if len(versions) != 1:
            raise CrossBotError(ErrorCode.NOT_FOUND, "文档版本不存在")
        return versions[0]

    def approve_quality(self, token, reference, *, reason):
        """Reviewer acknowledges low-confidence extraction after checking the original."""
        if not reason.strip():
            raise ValidationError("人工质量复核必须说明原因")
        with self.repository.transaction() as conn:
            content = self._load(conn, reference.version_id)
            actor = self.auth._require(conn, token, Permission.ATTACHMENT_CONFIRM, content.case_id)
            if content.reference != reference or content.status == AttachmentStatus.FAILED:
                raise ValidationError("解析失败必须重新解析为新版本，不能直接放行")
            if not content.fields or any(field.status != "confirmed" for field in content.fields):
                raise ValidationError("需先逐项人工确认字段")
            content.status = AttachmentStatus.READY
            conn.execute("UPDATE attachment_contents SET payload=? WHERE version_id=?", (encode(content), reference.version_id))
            self._project_fields(conn, content)
            self.repository.audit(conn, "attachment_quality_approved", actor.user_id, case_id=content.case_id, entity_id=reference.version_id, reason=reason)
            return content

    def read(self, token, reference, *, require_ready=False):
        with self.repository.transaction() as conn:
            content = self._load(conn, reference.version_id)
            self.auth._require(conn, token, Permission.ATTACHMENT_READ, content.case_id)
            if reference.document_id != content.reference.document_id:
                raise ValidationError("附件版本引用不匹配")
            if require_ready and content.status != AttachmentStatus.READY:
                raise CrossBotError(ErrorCode.REVIEW_REQUIRED, "附件解析失败或质量不足，需人工处理")
            return content

    def list_for_case(self, token, case_id):
        with self.repository.transaction() as conn:
            self.auth._require(conn, token, Permission.ATTACHMENT_READ, case_id)
            return [from_dict(AttachmentContent, json.loads(row["payload"])) for row in conn.execute("SELECT payload FROM attachment_contents WHERE case_id=? ORDER BY rowid", (case_id,))]

    def confirm_field(self, token, reference, field_name, value, *, reason):
        if value is None or not reason.strip():
            raise ValidationError("确认值和修改理由不能为空")
        with self.repository.transaction() as conn:
            content = self._load(conn, reference.version_id)
            actor = self.auth._require(conn, token, Permission.ATTACHMENT_CONFIRM, content.case_id)
            if content.reference != reference:
                raise ValidationError("附件引用不匹配")
            matches = [f for f in content.fields if f.field_name == field_name]
            if len(matches) != 1:
                raise ValidationError("字段不存在或不唯一")
            field = matches[0]
            before = copy.deepcopy(field.confirmed_value)
            field.history.append({"before": before, "after": value, "actor_id": actor.user_id, "reason": reason, "at": utc_now().isoformat()})
            field.confirmed_value = copy.deepcopy(value)
            field.confirmed_by = actor.user_id
            field.confirmed_at = utc_now()
            field.status = "confirmed"
            conn.execute("UPDATE attachment_contents SET payload=? WHERE version_id=?", (encode(content), reference.version_id))
            self._project_fields(conn, content)
            self.repository.audit(conn, "attachment_field_confirmed", actor.user_id, case_id=content.case_id, entity_id=reference.version_id, before={field_name: before}, after={field_name: value}, reason=reason)
            return field
