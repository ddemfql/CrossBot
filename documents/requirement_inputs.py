"""Upload a genuine source against one current data requirement only."""
from pathlib import PurePath

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import DocumentType, DocumentStatus, SourceRequirementLink, ReviewEditTarget, ReviewEditArea as A, to_dict
from review.policy import audit, require
from .forms import get_form
from .catalog import SELLER_TYPES
from .requirements import data_requirements
from .service import DocumentService
from .workbench import collect_document


class RequirementInputService:
    def __init__(self, repository, blobs):
        self.repository, self.blobs = repository, blobs

    @staticmethod
    def manual_target(workspace, target_type, field_name, row_number=None):
        """Resolve one currently active input slot without changing any state."""
        from .forms import input_form_fields, input_form_row_fields, input_context_fields, input_rows
        from .simple_export import TRADE_EXTRA_FIELDS
        try:
            kind = DocumentType(target_type)
        except (ValueError, TypeError) as exc:
            raise ValidationError("请选择有效的目标文件。") from exc
        if kind not in SELLER_TYPES:
            raise ValidationError("这里只能填写我方文件所需的业务数据，不能制作外部机构原件。")
        if (not isinstance(field_name, str) or not field_name or isinstance(row_number, bool)
                or row_number is not None and (not isinstance(row_number, int) or row_number < 1)):
            raise ValidationError("目标填写项或明细行无效。")
        form = get_form(workspace, kind)
        headers, columns = input_form_fields(form), input_form_row_fields(form)
        rows = input_rows(workspace.trade, form)
        if row_number is None and field_name in headers:
            return ReviewEditTarget(A.FORM, kind, field_name)
        if row_number is not None and field_name in columns:
            if row_number > len(rows):
                raise ValidationError("这条明细尚不存在，请先补充整份明细。")
            return ReviewEditTarget(A.FORM, kind, field_name, row_number=row_number)
        if field_name == "rows" and row_number is None and columns:
            return ReviewEditTarget(A.FORM, kind, "rows")
        contexts = input_context_fields(form, workspace)
        groups = {"adjustments", "packages", "line_items"}
        if row_number is None and field_name in contexts and field_name in set(TRADE_EXTRA_FIELDS) | groups:
            return ReviewEditTarget(A.TRADE, kind, field_name)
        matching = [item for item in data_requirements(workspace, kind)
                    if item.field_name == field_name and item.row_number == row_number]
        if not matching:
            raise ValidationError("此资料项未启用，请先在本文件资料设置中选择。")
        item = matching[0]
        if field_name == "package_details" and row_number is None and "packages" in contexts:
            return ReviewEditTarget(A.TRADE, kind, "packages")
        if row_number is None and any(field_name.startswith(group + "[") for group in groups if group in contexts):
            return ReviewEditTarget(A.TRADE, kind, field_name)
        if row_number is not None and "line_items" in contexts:
            origin_number = item.origin.row_number if item.origin else None
            if origin_number is None and row_number <= len(rows):
                sku = rows[row_number - 1].get("product_code")
                matches = [i for i, line in enumerate(workspace.trade.line_items, 1)
                           if sku and line.product_code == sku]
                origin_number = matches[0] if len(matches) == 1 else None
            return ReviewEditTarget(A.TRADE, kind, field_name if origin_number else "line_items",
                                    row_number=origin_number)
        raise ValidationError("此资料项须在对应业务资料或原件核对入口填写。")

    def fill(self, case_id, actor, target_type, field_name, row_number=None, *,
             value=None, rows=None, reason, expected_revision, expected_review_digest):
        ws = self.repository.get(case_id)
        require(ws, actor, "manual_field")
        if (not isinstance(expected_revision, int) or isinstance(expected_revision, bool)
                or expected_revision != ws.revision):
            raise CrossBotError(ErrorCode.CONFLICT, "资料已变化，请刷新后重新填写。")
        target = self.manual_target(ws, target_type, field_name, row_number)
        from validation.export_review import review_export
        report = review_export(ws, DocumentType(target_type), human_confirmed=True)
        if not expected_review_digest or report.input_digest != expected_review_digest:
            raise CrossBotError(ErrorCode.CONFLICT, "当前资料要求已变化，请刷新后重新填写。")
        from review.editing import ReviewEditingService
        from case_workflow.service import CaseService
        from validation.rules import DeterministicRules
        editor = ReviewEditingService(self.repository, CaseService(self.repository, DeterministicRules()))
        return editor._apply(ws, actor, target, value=value, rows=rows, reason=reason,
                             expected_revision=expected_revision)

    def upload(self, case_id, actor, target_type, field_name, row_number,
               filename, content, source_type=None, expected_revision=None):
        ws = self.repository.get(case_id)
        require(ws, actor, "upload")
        if expected_revision is not None and expected_revision != ws.revision:
            raise CrossBotError(ErrorCode.CONFLICT, "资料已变化，请刷新缺失项后重新上传。")
        try:
            kind = DocumentType(target_type)
        except (ValueError, TypeError) as exc:
            raise ValidationError("请选择有效的目标文件。") from exc
        if kind not in SELLER_TYPES:
            raise ValidationError("请选择需要制作的我方文件作为资料目标。")
        if (not isinstance(field_name, str) or isinstance(row_number, bool)
                or (row_number is not None and (not isinstance(row_number, int) or row_number < 1))):
            raise ValidationError("目标填写项或明细行无效。")
        requirements = data_requirements(ws, kind)
        valid = any(item.field_name == field_name and item.row_number == row_number for item in requirements)
        from .forms import input_form_fields as form_fields, input_form_row_fields as form_row_fields, input_rows as effective_rows
        initial_form = get_form(ws, kind)
        if row_number is None and field_name in form_fields(initial_form):
            valid = True
        if (row_number is not None and row_number <= len(effective_rows(ws.trade, initial_form))
                and field_name in form_row_fields(initial_form)):
            valid = True
        if field_name == "rows" and row_number is None:
            valid = True
        if not valid:
            raise ValidationError("目标填写项不在当前资料需求中，请刷新后选择。")
        if not isinstance(filename, str):
            raise ValidationError("请提供有效的文件名。")
        from .intake import classify_source
        source_kind = source_type if source_type is not None else classify_source(filename)[0]
        revision = ws.revision
        saved = DocumentService(self.repository, self.blobs).upload(
            case_id, actor, filename, content, source_kind)
        document = saved.documents[-1]
        document_id, version_id = document.document_id, document.current_version_id
        # Collection here deliberately never pre-fills a whole form. Only the
        # requested slot may be changed by the field-level service below.
        collected = collect_document(self.repository, self.blobs, case_id, document_id, actor,
            ocr_images=PurePath(filename).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}, prefill=False)
        document = next(d for d in collected.documents if d.document_id == document_id)
        version = next(v for v in document.versions if v.version_id == version_id)
        if saved.revision != revision + 1:
            # The original remains retained even when an intervening edit makes
            # application unsafe. Never overwrite a newer human change.
            raise CrossBotError(ErrorCode.CONFLICT, "上传期间资料发生变化，原件已保存，请刷新后核对填写目标。")
        target = get_form(collected, kind)
        can_fill = (field_name == "rows" or (row_number is None and field_name in form_fields(target))
                    or (row_number is not None and field_name in form_row_fields(target)))
        if version.status is DocumentStatus.PARSED and can_fill:
            from .prefill import PrefillService
            PrefillService(self.repository).fill_requirement(case_id, actor, kind,
                field_name, document_id, row_number=row_number, expected_revision=collected.revision)
        current = self.repository.get(case_id)
        link = SourceRequirementLink(document_id, version_id, kind, field_name, row_number)
        if link not in current.source_requirement_links:
            current.source_requirement_links.append(link)
        audit(current, actor, "requirement_source_uploaded", "document_version", version_id,
            after={"requirement": to_dict(link), "parse_status": version.status.value},
            reason="为指定缺失项提供真实资料；未自动确认，未匹配内容仍待补充。")
        self.repository.save(current, expected_revision=current.revision)
        return current

