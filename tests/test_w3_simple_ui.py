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
from documents.simple_workbench import file_inventory, selected_bundle, source_inventory
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
            if hasattr(module, "defaults_store"):
                module.defaults_store.close()
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

    def test_sales_can_preview_incomplete_trade_but_cannot_formally_export(self):
        app = self.app()
        widget(app.button, "新建交易").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        self.assertEqual([tab.label for tab in app.tabs], ["文件分类与查看", "生成与导出"])
        self.assertTrue(any("缺失文件" in item.value for item in app.warning))
        self.assertTrue(widget(app.button, "确认并生成").disabled)
        self.assertFalse(any(item.label == "人工批准当前版本" for item in app.button))
        confirm = "我已人工核对当前资料，确认按现有内容生成"
        widget(app.checkbox, confirm).check().run()
        self.assertTrue(widget(app.button, "确认并生成").disabled)
        widget(app.button, "预览文件").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        workspace = self.repo.list()[0]
        self.assertFalse(workspace.artifacts)
        self.assertFalse(workspace.approvals)
        self.assertTrue(widget(app.button, "确认并生成").disabled)

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
            originals = [name for name in archive.namelist() if name.startswith("原件/")]
            self.assertEqual(len(originals), 1)
            self.assertTrue(originals[0].endswith("two.txt"))
            self.assertEqual(archive.read(originals[0]), b"two")
            self.assertIn("导出清单.json", archive.namelist())

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
        widget(app.button, "预览文件").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        saved = self.repo.get(target.case.case_id)
        self.assertEqual(saved.revision, before)
        self.assertFalse(saved.artifacts)
        self.assertFalse(saved.approvals)
        self.assertFalse(self.repo.get(first.case.case_id).artifacts)

    def test_manual_source_replaces_upload_without_completing_deliverable(self):
        from case_workflow.service import CaseService
        from validation.rules import DeterministicRules
        cases = CaseService(self.repo, DeterministicRules())
        ws = cases.create(self.actor, TradeData(currency=""), required_types=[])
        cases.set_document_checklist(ws.case.case_id, self.actor,
            [D.PURCHASE_ORDER], [D.COMMERCIAL_INVOICE])
        app = self.app()
        widget(app.radio, "补充方式").set_value("手动填写").run()
        widget(app.text_input, "订单号（提供资料）").set_value("SYN-MANUAL-PO")
        widget(app.text_input, "买方（提供资料）").set_value("Synthetic buyer")
        widget(app.button, "保存手动提供的资料").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        saved = self.repo.get(ws.case.case_id)
        self.assertFalse(saved.documents)
        self.assertFalse(saved.artifacts)
        rows = {r["文件分类"]: r for r in file_inventory(saved)}
        self.assertTrue(rows["买方订单 PO"]["手动填写"])
        self.assertEqual(rows["买方订单 PO"]["资料情况"], "已填写资料（非原件）")
        self.assertEqual(rows["商业发票 CI"]["生成文件"], 0)
        self.assertFalse(any("待提供资料" in item.value for item in app.warning))
        self.assertTrue(any("待制作" in item.value for item in app.warning))
        widget(app.button, "将手动资料填入当前表单").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        target = next(f for f in self.repo.get(ws.case.case_id).document_forms
                      if f.document_type is D.COMMERCIAL_INVOICE)
        self.assertEqual(target.values["buyer"], "Synthetic buyer")
        self.assertEqual(target.source_form_references[0].document_type, D.PURCHASE_ORDER)

    def test_all_po_files_and_same_name_paths_remain_visible_with_versions(self):
        ws = Workspace(Case(case_number="SYN-MULTI-PO"))
        self.repo.save(ws, expected_revision=0)
        service = DocumentService(self.repo, self.blobs)
        ws = service.upload(ws.case.case_id, self.actor, "PO.txt", b"first", D.PURCHASE_ORDER)
        first = ws.documents[-1].document_id
        ws = service.upload(ws.case.case_id, self.actor, "PO.txt", b"second", D.PURCHASE_ORDER)
        ws = service.upload(ws.case.case_id, self.actor, "PO-revised.txt", b"revised", D.PURCHASE_ORDER,
            document_id=first)
        rows = source_inventory(ws, D.PURCHASE_ORDER)
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(r["保存版本数"] for r in rows), [1, 2])
        self.assertEqual(file_inventory(ws)[0]["原件"], 2)
        app = self.app()
        self.assertFalse(app.exception)
        options = widget(app.selectbox, "查看文件").options
        self.assertEqual(len(options), 2)
        self.assertTrue(any("1/2" in option for option in options))
        self.assertTrue(any("2/2" in option for option in options))
        listing = next(frame.value for frame in app.dataframe
                       if "保存版本数" in frame.value.columns)
        self.assertEqual(len(listing), 2)


if __name__ == "__main__":
    unittest.main()
