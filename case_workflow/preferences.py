"""Own-user defaults contain choices only, never copied transaction data."""
from copy import deepcopy

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.models import UserWorkbenchDefaults, UserDocumentDefaults, DocumentType, to_dict, utc_now
from documents.catalog import FORM_FIELDS, ROW_FIELDS, SELLER_TYPES
from documents.forms import get_form
from review.policy import require, invalidate, audit

CHOICE_FIELDS = ("included_fields", "included_row_fields", "included_context_fields",
    "required_input_fields", "required_input_row_fields", "required_input_context_fields")


def validate_defaults(defaults):
    from documents.simple_export import TRADE_EXTRA_FIELDS
    if not isinstance(defaults, UserWorkbenchDefaults):
        raise ValidationError("个人默认配置格式无效。")
    for types, allowed in ((defaults.source_types, FORM_FIELDS), (defaults.delivery_types, SELLER_TYPES)):
        if not isinstance(types, list) or any(not isinstance(kind, DocumentType) or kind not in allowed for kind in types):
            raise ValidationError("个人默认文件分类无效。")
    kinds = set()
    for document in defaults.documents:
        if not isinstance(document, UserDocumentDefaults) or document.document_type not in FORM_FIELDS or document.document_type in kinds:
            raise ValidationError("个人默认文件字段配置无效或重复。")
        kinds.add(document.document_type)
        for name in CHOICE_FIELDS:
            values = getattr(document, name)
            allowed = (set(TRADE_EXTRA_FIELDS) | {"adjustments", "packages", "line_items"} if "context" in name
                else ROW_FIELDS[document.document_type] if "row_fields" in name else FORM_FIELDS[document.document_type])
            if values is None and name.startswith("included_"):
                continue
            if not isinstance(values, list) or any(not isinstance(value, str) or value not in allowed for value in values):
                raise ValidationError("个人默认配置包含不支持的字段。")


def apply_document_choices(workspace, defaults, *, kinds=None):
    """Copy choice snapshots only; callers own invalidation, audit and persistence."""
    changed = False
    for choices in defaults.documents:
        if kinds is not None and choices.document_type not in kinds:
            continue
        form = get_form(workspace, choices.document_type)
        before = to_dict(form)
        for name in CHOICE_FIELDS:
            setattr(form, name, deepcopy(getattr(choices, name)))
        if to_dict(form) != before or not any(item.document_type is choices.document_type for item in workspace.document_forms):
            form.revision += 1
            form.confirmed_by = form.confirmed_at = None
            workspace.document_forms = [item for item in workspace.document_forms
                if item.document_type is not choices.document_type]
            workspace.document_forms.append(form)
            changed = True
    workspace.defaults_user_id, workspace.defaults_revision = defaults.user_id, defaults.revision
    return changed


class UserDefaultsService:
    def __init__(self, repository, store):
        self.repository, self.store = repository, store

    def get(self, actor):
        require(None, actor, "view")
        return self.store.get(actor.user_id) or UserWorkbenchDefaults(actor.user_id)

    def save_from_case(self, case_id, actor, expected_revision=None):
        workspace = self.repository.get(case_id)
        require(workspace, actor, "view")
        previous = self.get(actor)
        if expected_revision is not None and previous.revision != expected_revision:
            raise CrossBotError(ErrorCode.CONFLICT, "个人默认配置已更新，请刷新后重试。")
        sources = (workspace.required_source_types if workspace.required_source_types is not None else
            [kind for kind in workspace.base_required_document_types if kind not in SELLER_TYPES])
        deliveries = (workspace.required_delivery_types if workspace.required_delivery_types is not None else
            [kind for kind in workspace.base_required_document_types if kind in SELLER_TYPES])
        kinds = list(dict.fromkeys([*sources, *deliveries, *(form.document_type for form in workspace.document_forms)]))
        documents = [UserDocumentDefaults(kind, **{name: deepcopy(getattr(get_form(workspace, kind), name))
                     for name in CHOICE_FIELDS}) for kind in kinds]
        result = UserWorkbenchDefaults(actor.user_id, source_types=list(dict.fromkeys(sources)),
            delivery_types=list(dict.fromkeys(deliveries)), documents=documents,
            revision=previous.revision, updated_at=utc_now())
        validate_defaults(result)
        self.store.save(result, expected_revision=previous.revision)
        return result

    def apply_to_case(self, case_id, actor, expected_workspace_revision=None):
        workspace = self.repository.get(case_id)
        require(workspace, actor, "manual_field")
        if expected_workspace_revision is not None and workspace.revision != expected_workspace_revision:
            raise CrossBotError(ErrorCode.CONFLICT, "交易资料已更新，请刷新后重试。")
        defaults = self.get(actor)
        validate_defaults(defaults)
        before = {"sources": to_dict(workspace.required_source_types), "deliveries": to_dict(workspace.required_delivery_types),
            "defaults_user_id": workspace.defaults_user_id, "defaults_revision": workspace.defaults_revision}
        from .service import requirements
        workspace.required_source_types, workspace.required_delivery_types = list(defaults.source_types), list(defaults.delivery_types)
        workspace.base_required_document_types = list(dict.fromkeys([*defaults.source_types, *defaults.delivery_types]))
        workspace.required_document_types = requirements(workspace.trade, workspace.base_required_document_types, workspace.requirement_policies)
        workspace.case.required_document_types = list(workspace.required_document_types)
        for document in workspace.documents:
            document.required = document.document_type in workspace.required_document_types
        apply_document_choices(workspace, defaults)
        invalidate(workspace, actor, "人工应用个人默认配置；业务内容保持原样。")
        audit(workspace, actor, "personal_defaults_applied", "case", case_id, before=before,
            after={"source_types": to_dict(defaults.source_types), "delivery_types": to_dict(defaults.delivery_types),
                "defaults_user_id": defaults.user_id, "defaults_revision": defaults.revision},
            reason="仅应用资料要求及输出选择，不自动批准或修改业务数据。")
        self.repository.save(workspace, expected_revision=workspace.revision)
        return workspace
