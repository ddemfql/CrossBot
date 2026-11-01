"""W3 entry point using the shared account, selected Case, and local database."""
from pathlib import Path
from copy import deepcopy
from hashlib import sha256
import json

import streamlit as st

from auth.cases import CaseAccessService
from case_workflow.inquiry_handoff import (FIELD_LABELS, compare_order_draft,
    import_order_draft, read_order_draft)
from case_workflow.preferences import UserDefaultsService
from case_workflow.service import CaseService
from core.errors import CrossBotError, ErrorCode
from core.identity import Permission
from core.models import CaseStatus, CrossCheckStatus, DocumentType
from documents.ocr import OCRBackend
from documents.service import DocumentService
from documents.simple_workbench import render_workspace
from review.access import shared_w3_session
from review.service import ReviewService
from storage.blobs import LocalBlobStore
from storage.integrated_workspace import IntegratedWorkspaceRepository
from storage.preferences import SQLiteUserDefaultsStore
from validation.rules import DeterministicRules


class _HandoffRepository(IntegratedWorkspaceRepository):
    def get(self, case_id):
        workspace = super().get(case_id)
        current = CaseAccessService(self.auth).get_case(self.token, case_id)
        if "w1_order_draft" in current.metadata:
            workspace.case.metadata["w1_order_draft"] = deepcopy(current.metadata["w1_order_draft"])
        else:
            workspace.case.metadata.pop("w1_order_draft", None)
        draft = current.metadata.get("w1_order_draft")
        fields = draft.get("fields") if isinstance(draft, dict) else None
        if isinstance(fields, dict) and workspace.trade.revision == 1 and not workspace.trade.confirmed_by:
            for name in ("order_no", "buyer", "destination_country", "currency", "incoterm"):
                item = fields.get(name, {})
                if isinstance(item, dict) and item.get("value") is None and name not in workspace.trade.supplied_fields:
                    setattr(workspace.trade, name, "")
        return workspace


def _render_inquiry_handoff(case, workspace=None, actor=None, cases=None):
    try:
        draft = read_order_draft(case.metadata)
    except CrossBotError as error:
        st.warning(str(error))
        return
    if draft is None:
        return
    st.markdown("**W1 订单草稿（未确认）**")
    st.caption(f"询盘：{draft['inquiry_id']} · 创建来源：{draft['created_by']}")
    st.dataframe([{"字段": label, "草稿值": draft["fields"][name]["value"],
        "原文依据": draft["fields"][name]["evidence"], "状态": "未确认"}
        for name, label in FIELD_LABELS.items()], hide_index=True, use_container_width=True)
    with st.expander("W1 原始历史"):
        for message in draft["messages"]:
            st.text(f"{message['role']}: {message['content']}")
    if workspace is None:
        return
    digest = sha256(json.dumps(draft, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    prefix = f"w1-handoff-{case.case_id}-{workspace.revision}-{digest}"
    closed = workspace.case.status is CaseStatus.CLOSED
    with st.expander("导入 W1 草稿"):
        acknowledged = st.checkbox("将草稿补入订单空字段，保留为未确认候选", key=prefix + "-ack",
            disabled=closed)
        if st.button("导入未确认候选", key=prefix + "-import", disabled=closed or not acknowledged):
            try:
                import_order_draft(cases, case.case_id, actor, case.metadata,
                    expected_revision=workspace.revision)
            except (CrossBotError, ValueError) as error:
                st.error(str(error))
            else:
                st.session_state["w3_notice"] = "草稿已补入订单空字段，仍待人工编辑和确认。"
                st.rerun()
    originals = [document for document in workspace.documents
        if document.deleted_at is None and document.current_version_id]
    originals.sort(key=lambda document: document.document_type is not DocumentType.PURCHASE_ORDER)
    options = [None, *[document.document_id for document in originals]]
    selected = st.selectbox("与草稿比较的平台订单原件", options,
        index=1 if originals else 0,
        format_func=lambda key: "尚未选择" if key is None else next(
            document.title + " · " + document.document_id for document in originals if document.document_id == key),
        key=f"w1-platform-source-{case.case_id}")
    checks = compare_order_draft(workspace, case.metadata, selected)
    if any(check.status is CrossCheckStatus.FAIL for check in checks):
        st.warning("草稿与平台订单存在冲突（FAIL），请核对原件。")
    else:
        st.info("平台订单比较待人工复核（PENDING_REVIEW）。")
    st.dataframe([{"字段": FIELD_LABELS[check.left_field], "W1 草稿": check.left_value,
        "平台订单": check.right_value, "结果": check.status.name, "说明": check.message,
        "规则": check.rule_code, "W1 来源": check.left_source,
        "原件来源": check.right_source, "原件版本": check.right_version_id}
        for check in checks], hide_index=True, use_container_width=True)
    if selected is None or closed:
        return
    with st.expander("人工补录或修正平台订单字段"):
        verified = st.checkbox("我已对照平台订单原件核实本次录入值",
            key=prefix + "-verify-" + selected)
        with st.form(prefix + "-manual-" + selected):
            name = st.selectbox("平台订单字段", list(FIELD_LABELS), format_func=FIELD_LABELS.get)
            value = st.text_input("原件核实值")
            reason = st.text_input("录入或修正理由")
            save = st.form_submit_button("保存人工复核值", disabled=not verified)
        if save and verified:
            try:
                ReviewService(cases.repository, cases.rules).enter_manual_field(
                    case.case_id, selected, {"buyer": "customer_name", "description": "product_name"}.get(name, name),
                    value, actor, reason)
            except (CrossBotError, ValueError) as error:
                st.error(str(error))
            else:
                st.session_state["w3_notice"] = "人工复核值已保存；W1 草稿仍未确认。"
                st.rerun()


def render_w3(auth, token, case):
    repository = _HandoffRepository(auth, token, case.case_id)
    defaults = SQLiteUserDefaultsStore(auth.store.path)
    blobs = LocalBlobStore(Path(auth.store.path).parent / "w3-files")
    try:
        with shared_w3_session(auth, token, case.case_id) as actor:
            try:
                workspace = repository.get(case.case_id)
            except CrossBotError as error:
                if error.code != ErrorCode.NOT_FOUND:
                    raise
                _render_inquiry_handoff(CaseAccessService(auth).get_case(token, case.case_id))
                st.info("当前 Case 尚未启用单证工作台。")
                from auth.service import ROLE_PERMISSIONS
                principal = auth.require_session(token)
                if st.button("启用 W3 单证工作台", disabled=Permission.CASE_UPDATE not in ROLE_PERMISSIONS[principal.active_role]):
                    repository.initialize(actor)
                    st.rerun()
                return
            notice = st.session_state.pop("w3_notice", None)
            if notice:
                st.success(notice)
            if not OCRBackend().available:
                st.caption("图片与扫描 PDF 可保存和人工录入；本环境未启用自动 OCR。")
            cases = CaseService(repository, DeterministicRules())
            _render_inquiry_handoff(workspace.case, workspace, actor, cases)
            render_workspace(workspace, actor,
                cases, DocumentService(repository, blobs),
                repository, blobs, defaults_service=UserDefaultsService(repository, defaults))
    except (CrossBotError, ValueError, OSError) as error:
        st.error(f"单证操作未完成：{error}")
    finally:
        defaults.close()
        repository.close()
