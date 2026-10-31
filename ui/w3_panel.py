"""W3 entry point using the shared account, selected Case, and local database."""
from pathlib import Path

import streamlit as st

from case_workflow.preferences import UserDefaultsService
from case_workflow.service import CaseService
from core.errors import CrossBotError, ErrorCode
from core.identity import Permission
from documents.ocr import OCRBackend
from documents.service import DocumentService
from documents.simple_workbench import render_workspace
from review.access import shared_w3_session
from storage.blobs import LocalBlobStore
from storage.integrated_workspace import IntegratedWorkspaceRepository
from storage.preferences import SQLiteUserDefaultsStore
from validation.rules import DeterministicRules


def render_w3(auth, token, case):
    repository = IntegratedWorkspaceRepository(auth, token, case.case_id)
    defaults = SQLiteUserDefaultsStore(auth.store.path)
    blobs = LocalBlobStore(Path(auth.store.path).parent / "w3-files")
    try:
        with shared_w3_session(auth, token, case.case_id) as actor:
            try:
                workspace = repository.get(case.case_id)
            except CrossBotError as error:
                if error.code != ErrorCode.NOT_FOUND:
                    raise
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
            render_workspace(workspace, actor,
                CaseService(repository, DeterministicRules()), DocumentService(repository, blobs),
                repository, blobs, defaults_service=UserDefaultsService(repository, defaults))
    except (CrossBotError, ValueError, OSError) as error:
        st.error(f"单证操作未完成：{error}")
    finally:
        defaults.close()
        repository.close()
