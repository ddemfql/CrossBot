"""Synthetic checks for the two-role, human-confirmed document workspace."""
from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from core.models import Actor, Case, DocumentType as D, TradeData, Workspace
from documents.service import DocumentService
from documents.simple_workbench import file_inventory, selected_bundle
from storage.blobs import LocalBlobStore
from storage.sqlite import SQLiteWorkspaceRepository


def widget(items, label):
    return next(item for item in items if item.label == label)


class SimpleUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"CROSSBOT_W3_DATA": str(self.root)})
        self.env.start()
        # Each AppTest imports a workspace bound to this test's temporary path.
        self.previous_module = sys.modules.pop("w3_app", None)
        self.repo = SQLiteWorkspaceRepository(self.root / "workspace.sqlite3")
        self.blobs = LocalBlobStore(self.root / "files")
        self.actor = Actor("synthetic-sales", ["sales"], authenticated=True)

    def tearDown(self):
        module = sys.modules.pop("w3_app", None)
        if module:
            module.repository.close()
            module.identity.close()
        if self.previous_module:
            sys.modules["w3_app"] = self.previous_module
        self.repo.close()
        self.env.stop()
        self.temp.cleanup()

    def app(self, actor=None):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string("import w3_app\nw3_app.render()", default_timeout=25)
        app.session_state["w3_actor"] = actor or self.actor
        app.run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        return app

    def test_sales_creates_incomplete_trade_and_exports_with_one_confirmation(self):
        app = self.app()
        widget(app.button, "新建交易").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        self.assertEqual([tab.label for tab in app.tabs], ["文件分类与查看", "生成与导出"])
        self.assertTrue(any("缺失文件" in item.value for item in app.warning))
        self.assertTrue(widget(app.button, "生成 Excel").disabled)
        self.assertFalse(any(item.label == "人工批准当前版本" for item in app.button))
        confirm = "我已人工核对当前资料，确认按现有内容生成"
        widget(app.checkbox, confirm).check().run()
        self.assertFalse(widget(app.button, "生成 Excel").disabled)
        widget(app.button, "生成 Excel").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        workspace = self.repo.list()[0]
        self.assertEqual(len(workspace.artifacts), 1)
        self.assertEqual(workspace.artifacts[0].created_by, self.actor.user_id)
        self.assertTrue(workspace.artifacts[0].filename.endswith(".xlsx"))
        self.assertFalse(workspace.approvals)
        self.assertFalse(widget(app.checkbox, confirm).value)
        self.assertTrue(widget(app.button, "生成 Excel").disabled)

    def test_admin_only_offers_two_account_roles(self):
        app = self.app(Actor("synthetic-admin", ["admin"], authenticated=True))
        self.assertEqual(widget(app.selectbox, "角色").options, ["业务员", "管理员"])
        self.assertFalse(any("岗位" in item.label for item in app.selectbox))

    def test_sales_reclassifies_source_and_missing_list_updates(self):
        workspace = Workspace(Case(case_number="SYN-SIMPLE"), trade=TradeData(),
            required_document_types=[D.COMMERCIAL_INVOICE],
            base_required_document_types=[D.COMMERCIAL_INVOICE])
        self.repo.save(workspace, expected_revision=0)
        service = DocumentService(self.repo, self.blobs)
        service.upload(workspace.case.case_id, self.actor, "synthetic.txt", b"Synthetic source", D.OTHER)
        app = self.app()
        self.assertTrue(any("缺失文件" in item.value for item in app.warning))
        widget(app.selectbox, "修正分类").select(D.COMMERCIAL_INVOICE)
        widget(app.button, "保存分类").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        changed = self.repo.get(workspace.case.case_id)
        self.assertIs(changed.documents[0].document_type, D.COMMERCIAL_INVOICE)
        self.assertFalse(any("缺失文件" in item.value for item in app.warning))

    def test_inventory_and_bundle_only_include_selected_active_sources(self):
        workspace = Workspace(Case(case_number="SYN-BUNDLE"),
            required_document_types=[D.COMMERCIAL_INVOICE])
        self.repo.save(workspace, expected_revision=0)
        service = DocumentService(self.repo, self.blobs)
        workspace = service.upload(workspace.case.case_id, self.actor, "one.txt", b"one", D.OTHER)
        workspace = service.upload(workspace.case.case_id, self.actor, "two.txt", b"two", D.PURCHASE_ORDER)
        rows = file_inventory(workspace)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["情况"], "待填写")
        with ZipFile(BytesIO(selected_bundle(workspace, service, self.actor,
                [workspace.documents[1].document_id], []))) as archive:
            self.assertEqual(len(archive.namelist()), 1)
            self.assertTrue(archive.namelist()[0].endswith("two.txt"))
            self.assertEqual(archive.read(archive.namelist()[0]), b"two")

    def test_case_link_and_unconfirmed_preview_do_not_write_artifacts(self):
        from case_workflow.service import CaseService
        from validation.rules import DeterministicRules
        cases = CaseService(self.repo, DeterministicRules())
        first = cases.create(self.actor, TradeData(order_no="SYN-LINK-A"))
        target = cases.create(self.actor, TradeData(order_no="SYN-LINK-B"))
        app = self.app()
        app.query_params["case"] = target.case.case_id
        app.run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        self.assertEqual(widget(app.selectbox, "选择交易").value, target.case.case_id)
        before = self.repo.get(target.case.case_id).revision
        widget(app.button, "准备待人工核对的 Excel 预览").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        saved = self.repo.get(target.case.case_id)
        self.assertEqual(saved.revision, before)
        self.assertFalse(saved.artifacts)
        self.assertFalse(saved.approvals)
        self.assertFalse(self.repo.get(first.case.case_id).artifacts)


if __name__ == "__main__":
    unittest.main()
