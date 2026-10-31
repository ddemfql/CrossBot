"""Versioned user XLSX templates with a deliberately small placeholder language."""
from copy import copy
from hashlib import sha256
from io import BytesIO
from pathlib import PurePath
import re
from zipfile import ZipFile

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import DocumentType, to_dict
from review.policy import audit, invalidate, require
from .catalog import FORM_FIELDS, ROW_FIELDS, SELLER_TYPES
from .workbook_renderer import _stable_xlsx

TOKEN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z_0-9.]*)\s*\}\}")
MAX_BYTES = 5 * 1024 * 1024


def _load(content, kind, fields=None, row_fields=None):
    from openpyxl import load_workbook
    if not isinstance(content, bytes) or not content or len(content) > MAX_BYTES:
        raise ValidationError("模板须为不超过 5 MB 的 XLSX 文件。")
    fields = FORM_FIELDS[kind] if fields is None else fields
    row_fields = ROW_FIELDS[kind] if row_fields is None else row_fields
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if (len(entries) > 1000 or sum(i.file_size for i in entries) > 30 * 1024 * 1024
                    or any(any(token in i.filename for token in ("vbaProject", "externalLinks/", "activeX/",
                           "embeddings/", "connections.xml")) for i in entries)
                    or any(b'TargetMode="External"' in archive.read(i) for i in entries
                           if i.filename.endswith(".rels"))):
                raise ValidationError("模板包含宏、外部链接或超出大小限制。")
        wb = load_workbook(BytesIO(content), keep_links=False)
        if len(wb.worksheets) > 10:
            raise ValidationError("模板最多支持 10 个工作表。")
        repeats, tokens = [], 0
        for sheet in wb:
            if sheet.max_row > 2000 or sheet.max_column > 50:
                raise ValidationError("模板表格尺寸过大。")
            if sheet.tables or sheet._charts:
                raise ValidationError("模板暂不支持 Excel 数据表或图表，请使用普通单元格排版。")
            for row in sheet:
                for cell in row:
                    if cell.data_type == "f" or cell.hyperlink:
                        raise ValidationError("模板暂不支持公式或链接，请将其改为固定内容。")
                    value = str(cell.value or "")
                    matches = list(TOKEN.finditer(value))
                    remaining = TOKEN.sub("", value)
                    if "{{" in remaining or "}}" in remaining:
                        raise ValidationError("模板占位符格式不正确。")
                    for match in matches:
                        name = match[1]
                        tokens += 1
                        if name.startswith("rows."):
                            if name[5:] not in row_fields:
                                raise ValidationError("模板包含不支持的明细字段：" + name)
                            repeats.append((sheet.title, cell.row))
                        elif name not in fields:
                            raise ValidationError("模板包含未选用或不支持的字段：" + name)
        repeats = list(dict.fromkeys(repeats))
        if len(repeats) > 1:
            raise ValidationError("模板明细占位符必须位于同一工作表的同一行。")
        if repeats:
            sheet_name, index = repeats[0]
            if any(r.min_row <= index <= r.max_row for r in wb[sheet_name].merged_cells.ranges):
                raise ValidationError("明细重复行不能包含合并单元格。")
            if any(r.min_row > index for r in wb[sheet_name].merged_cells.ranges):
                raise ValidationError("明细行下方暂不支持合并单元格，请移至明细行上方。")
        if not tokens:
            raise ValidationError("模板至少需要一个 {{字段名}} 占位符。")
        return wb, repeats[0] if repeats else None
    except CrossBotError:
        raise
    except Exception as exc:
        raise ValidationError("无法读取此 XLSX 模板，请检查文件格式。") from exc


def selected_template(workspace, kind):
    key = workspace.selected_template_ids.get(kind.value)
    if not key:
        return None
    template = next((t for t in workspace.document_templates if t.template_id == key
                     and t.document_type is kind), None)
    if template is None:
        raise ValidationError("所选模板不存在，请重新选择。")
    return template


def render_template(content, kind, values, rows, *, human_confirmed, actor_id, fields=None, row_fields=None, audience="internal"):
    wb, repeat = _load(content, kind, fields, row_fields)
    if len(rows) > 1000:
        raise ValidationError("模板最多支持 1000 行明细。")
    if rows and not repeat:
        wb.close()
        raise ValidationError("当前文件有明细，模板需要一行 {{rows.字段名}} 占位符。")
    try:
        if repeat:
            sheet_name, index = repeat
            sheet = wb[sheet_name]
            bases = [(c.value, copy(c._style), copy(c.alignment), c.number_format) for c in sheet[index]]
            count = max(1, len(rows))
            if count > 1:
                sheet.insert_rows(index + 1, count - 1)
            for offset in range(count):
                for col, (value, style, alignment, fmt) in enumerate(bases, 1):
                    cell = sheet.cell(index + offset, col)
                    cell._style, cell.alignment, cell.number_format = copy(style), copy(alignment), fmt
                    cell.value = value
                    if isinstance(value, str):
                        data = rows[offset] if offset < len(rows) else {}
                        cell.value = TOKEN.sub(lambda m: str(data.get(m[1][5:], ""))
                            if m[1].startswith("rows.") else str(values.get(m[1], "")), value)
                        cell.data_type = "s"
        for sheet in wb:
            sheet.oddHeader.center.text = "人工确认生成" if human_confirmed else "未人工确认预览"
            for row in sheet:
                for cell in row:
                    if repeat and sheet.title == repeat[0] and repeat[1] <= cell.row < repeat[1] + max(1, len(rows)):
                        continue
                    if isinstance(cell.value, str):
                        cell.value = TOKEN.sub(lambda m: str(values.get(m[1], "")), cell.value)
                        cell.data_type = "s"
        if audience == "internal":
            title = "CrossBot核对记录"
            if title in wb.sheetnames:
                del wb[title]
            trace = wb.create_sheet(title)
            for row in (("文件状态", "人工确认生成" if human_confirmed else "未人工确认预览"),
                        ("人工确认人", actor_id if human_confirmed else ""),
                        ("说明", "卖方填写稿，不代表外部签发；预览不得作为已确认文件使用。")):
                trace.append(row)
            if not human_confirmed:
                wb.active = wb.index(trace)
        elif not human_confirmed:
            status = wb.create_sheet("预览说明")
            status.append(["未人工确认预览", "此文件尚未人工确认，请勿作为已确认文件交付。"])
            wb.active = wb.index(status)
        return _stable_xlsx(wb)
    finally:
        wb.close()


class TemplateService:
    def __init__(self, repository, blobs):
        self.repository, self.blobs = repository, blobs

    def _workspace(self, case_id, actor, expected_revision, *, action="manual_field"):
        ws = self.repository.get(case_id)
        try:
            require(ws, actor, action)
        except CrossBotError:
            self.repository.save(ws, expected_revision=ws.revision)
            raise
        if expected_revision is not None and expected_revision != ws.revision:
            raise CrossBotError(ErrorCode.CONFLICT, "交易已变化，请刷新后重新选择模板。")
        return ws

    @staticmethod
    def _kind(kind):
        try:
            kind = DocumentType(kind)
        except (ValueError, TypeError) as exc:
            raise ValidationError("请选择有效文件类型。") from exc
        if kind not in SELLER_TYPES:
            raise ValidationError("只能为我方可制作的文件设置模板。")
        return kind

    def upload(self, case_id, actor, kind, filename, content, reason, expected_revision=None):
        from core.models import DocumentTemplate
        ws = self._workspace(case_id, actor, expected_revision, action="template_manage")
        kind = self._kind(kind)
        if (not isinstance(filename, str) or len(filename) > 255 or
                PurePath(filename).suffix.lower() != ".xlsx" or any(c in filename for c in "/\\")
                or any(ord(c) < 32 for c in filename)):
            raise ValidationError("模板文件名须为有效的 .xlsx 文件名。")
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError("请填写模板上传说明。")
        wb, _ = _load(content, kind)
        wb.close()
        key = self.blobs.put(content)
        versions = [t.version_number for t in ws.document_templates if t.document_type is kind]
        template = DocumentTemplate(document_type=kind, filename=filename,
            sha256=sha256(content).hexdigest(), storage_path=key,
            version_number=max(versions, default=0) + 1, uploaded_by=actor.user_id)
        ws.document_templates.append(template)
        invalidate(ws, actor, reason.strip())
        audit(ws, actor, "document_template_uploaded", "template", template.template_id,
              after=to_dict(template), reason=reason.strip())
        self.repository.save(ws, expected_revision=ws.revision)
        return ws

    def select(self, case_id, actor, kind, template_id, reason, expected_revision=None):
        ws = self._workspace(case_id, actor, expected_revision)
        kind = self._kind(kind)
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError("请填写模板选择说明。")
        if template_id is not None and not any(t.template_id == template_id and t.document_type is kind
                                               for t in ws.document_templates):
            raise ValidationError("模板不存在或文件类型不匹配。")
        before = ws.selected_template_ids.get(kind.value)
        if template_id is None:
            ws.selected_template_ids.pop(kind.value, None)
        else:
            ws.selected_template_ids[kind.value] = template_id
        invalidate(ws, actor, reason.strip())
        audit(ws, actor, "document_template_selected", "document_form", kind.value,
              before=before, after=template_id, reason=reason.strip())
        self.repository.save(ws, expected_revision=ws.revision)
        return ws
