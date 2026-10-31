"""Authenticated, repeatable ZIP intake without writing archive paths to disk."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
import mimetypes
from pathlib import PurePosixPath
import re
import stat
from zipfile import BadZipFile, ZipFile

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import (Actor, Case, Document, DocumentForm, DocumentStatus,
    DocumentType as Type, DocumentVersion, ExtractedField, ImportContext,
    ReviewTask, TradeData, WorkItem, Workspace)
from documents.intake import (INTAKE_VERSION, MAX_FILE_BYTES, classify_source,
                              collect_source, source_label)
from review.policy import audit, require
from case_workflow.routing import ensure_source_tasks

MAX_ARCHIVE_BYTES = 300 * 1024 * 1024
MAX_TOTAL_BYTES = 300 * 1024 * 1024
MAX_ARCHIVE_FILES = 2000


def _archive_path(info) -> str:
    """Recover legacy Chinese names, reject traversal and special files."""
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("gb18030")
        except (UnicodeError, LookupError):
            pass
    name = name.replace("\\", "/")
    parts = name.split("/")
    if (not name or name.startswith("/") or len(name) > 2000
            or any(part in {"..", "."} for part in parts)
            or any(ord(character) < 32 for character in name)
            or any(":" in part for part in parts)):
        raise ValidationError("ZIP 包含不安全文件路径，导入已停止。")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
        raise ValidationError("ZIP 不接受符号链接或特殊文件。")
    if info.flag_bits & 1:
        raise ValidationError("ZIP 包含加密文件，无法安全导入。")
    if info.file_size > MAX_FILE_BYTES:
        raise CrossBotError(ErrorCode.FILE_TOO_LARGE, "ZIP 单个条目超过 20 MB 上限。")
    return str(PurePosixPath(name))


def _row_candidates(tables, allowed: dict[str, str]) -> list[dict[str, str]]:
    """Compatibility helper using the ordinary deterministic prefill mapper."""
    from core.models import DocumentVersion
    from documents.prefill import row_candidates
    return [row.values for row in row_candidates(DocumentVersion(source_tables=tables), "", allowed)]


def _candidate_forms(workspace: Workspace):
    from documents.catalog import FORM_FIELDS
    from documents.prefill import prefill_form
    for kind in FORM_FIELDS:
        ids = [doc.document_id for doc in workspace.documents
               if doc.document_type is kind
               or (doc.document_type is Type.COMBINED_INVOICE_PACKING_LIST
                   and kind in {Type.COMMERCIAL_INVOICE, Type.PACKING_LIST})]
        prefill_form(workspace, kind, ids)


class ArchiveImporter:
    def __init__(self, repository, blobs):
        self.repository, self.blobs = repository, blobs

    def import_zip(self, content: bytes, filename: str, actor: Actor) -> Workspace:
        require(None, actor, "create")
        if not isinstance(content, bytes) or not content or len(content) > MAX_ARCHIVE_BYTES:
            raise ValidationError("ZIP 内容为空或超过 300 MB 导入上限。")
        if (not isinstance(filename, str) or not filename.lower().endswith(".zip")
                or len(filename) > 255 or "/" in filename or "\\" in filename
                or any(ord(character) < 32 for character in filename)):
            raise ValidationError("请提供有效的 ZIP 文件名。")
        archive_hash = sha256(content).hexdigest()
        for existing in self.repository.list():
            if existing.import_context and existing.import_context.archive_sha256 == archive_hash:
                return existing
        try:
            package = ZipFile(BytesIO(content))
        except (BadZipFile, ValueError):
            raise ValidationError("ZIP 文件损坏或格式不正确。") from None
        with package:
            entries = package.infolist()
            if len(entries) > MAX_ARCHIVE_FILES or sum(item.file_size for item in entries) > MAX_TOTAL_BYTES:
                raise ValidationError("ZIP 条目数或解压总大小超过导入上限。")
            # Validate every name before storing any blobs or creating a case.
            paths = [(info, _archive_path(info)) for info in entries]
            files = [(info, path) for info, path in paths if not info.is_dir()]
            if not files:
                raise ValidationError("ZIP 没有可导入的文件。")
            workspace = Workspace(Case(case_id="case_import_" + archive_hash,
                owner_id=actor.user_id), trade=TradeData(currency=""))
            workspace.case.case_number = "ZIP-" + archive_hash[:12].upper()
            combined_path = filename + "/" + "/".join(path for _, path in files)
            workspace.import_context = ImportContext(archive_name=filename,
                archive_sha256=archive_hash, imported_by=actor.user_id,
                reported_trade_state="已发货（目录声明，未核验）" if any(
                    value in combined_path for value in ("已发货", "已经发货")) else "",
                reported_receivable_state="待收款（目录声明，未核验）" if any(
                    value in combined_path for value in ("待收钱", "待收款", "未收款", "等待收钱", "等待收款")) else "")
            self.blobs.put(content)
            for info, path in files:
                try:
                    original = package.read(info)
                except (BadZipFile, RuntimeError, OSError, ValueError):
                    raise ValidationError("ZIP 条目完整性校验失败；未创建交易。") from None
                if len(original) > MAX_FILE_BYTES or len(original) != info.file_size:
                    raise ValidationError("ZIP 条目实际大小超出限制；未创建交易。")
                kind, responsibility, note = classify_source(path)
                extension = PurePosixPath(path).suffix.lower()
                text, tables, fields, warnings = collect_source(original, extension)
                # PI-123 / PO#123 are explicit filename hints, never confirmed
                # order data and never a replacement for contradictory contents.
                order_match = re.search(r"(?:^|[^a-z])(?:pi|po)[\s#_-]+([a-z0-9][a-z0-9._-]{1,60})",
                    PurePosixPath(path).stem, re.IGNORECASE)
                if order_match and not any(field.name == "order_no" for field in fields):
                    fields.append(ExtractedField(name="order_no", value=order_match[1],
                        normalized_value=order_match[1], confidence=0.5,
                        note="文件名订单标识候选；须与单据正文及主订单人工核对。"))
                document = Document(case_id=workspace.case.case_id, document_type=kind,
                    title=PurePosixPath(path).name, required=False,
                    responsibility=responsibility, classification_note=note)
                version = DocumentVersion(document_id=document.document_id,
                    original_filename=PurePosixPath(path).name, original_path=path,
                    sha256=sha256(original).hexdigest(), storage_path=self.blobs.put(original),
                    uploaded_by=actor.user_id, size_bytes=len(original),
                    media_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
                    source_text=text, source_tables=tables, fields=fields,
                    extraction_warnings=warnings, parser_name="source-intake",
                    parser_version=INTAKE_VERSION,
                    status=DocumentStatus.PARSED if fields else DocumentStatus.UPLOADED)
                document.current_version_id, document.status = version.version_id, version.status
                document.versions.append(version)
                workspace.documents.append(document)
                workspace.case.document_ids.append(document.document_id)
                if fields:
                    task = ReviewTask(case_id=workspace.case.case_id,
                        document_id=document.document_id, required=False,
                        field_names=[field.name for field in fields])
                    workspace.review_tasks.append(task)
                    workspace.case.review_task_ids.append(task.task_id)
                audit(workspace, actor, "archive_document_imported", "document", document.document_id,
                    after={"original_path": path, "sha256": version.sha256,
                           "document_type": kind.value, "responsibility": responsibility},
                    reason="原件与未确认候选保留；分类及字段需人工复核。")
            _candidate_forms(workspace)
            workspace.tasks.append(WorkItem(title="核对导入分类、原始表格并补充确认交易资料",
                role="sales", assignee_id=actor.user_id,
                note="目录发货/收款文字仅为历史声明；不得替代真人批准及凭证。"))
            ensure_source_tasks(workspace, actor)
            audit(workspace, actor, "archive_imported", "case", workspace.case.case_id,
                after={"archive_sha256": archive_hash, "file_count": len(files)},
                reason="按原始路径保存全部文件；主订单、字段及表单均未自动确认。")
            try:
                self.repository.save(workspace, expected_revision=0)
            except CrossBotError as exc:
                if exc.code is not ErrorCode.CONFLICT:
                    raise
                # A simultaneous import of identical bytes shares the stable
                # case id and must return the winner instead of duplicating it.
                existing = self.repository.get(workspace.case.case_id)
                if (existing.import_context
                        and existing.import_context.archive_sha256 == archive_hash):
                    return existing
                raise
            return workspace
