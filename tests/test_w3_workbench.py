"""Synthetic UI and source-preservation checks for the grouped file center."""
from io import BytesIO
import importlib
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from core.errors import CrossBotError
from core.models import (Actor, Case, Document, DocumentForm, DocumentStatus,
                         DocumentType as D, DocumentVersion, ExtractedField,
                         FieldValueStatus, SourceTable, Workspace)
from documents.workbench import collect_document, export_bundle, inventory, reclassify
from storage.blobs import LocalBlobStore
from storage.sqlite import SQLiteWorkspaceRepository


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = SQLiteWorkspaceRepository(self.root / "workspace.sqlite3")
        self.addCleanup(self.repository.close)
        self.blobs = LocalBlobStore(self.root / "files")
        self.actor = Actor("synthetic-admin", ["admin"], authenticated=True)
        self.content = b"Order No: SYN-UI-1\nQuantity: 4\n"
        key = self.blobs.put(self.content)
        self.document = Document(document_type=D.PURCHASE_ORDER,
            title="Synthetic buyer PO.txt", responsibility="external")
        version = DocumentVersion(document_id=self.document.document_id,
            storage_path=key, sha256=key, original_filename="Synthetic buyer PO.txt",
            media_type="text/plain", size_bytes=len(self.content),
            original_path="Synthetic/Synthetic buyer PO.txt", source_tables=[SourceTable("Synthetic sheet", [["Order No", "SYN-UI-1"]])], fields=[ExtractedField(
                "quantity", value="3", confirmed_value="4", confirmed_by=self.actor.user_id,
                status=FieldValueStatus.CONFIRMED)], status=DocumentStatus.PARSED)
        self.document.versions.append(version)
        self.document.current_version_id = version.version_id
        self.workspace = Workspace(Case(case_number="SYN-UI-1"), documents=[self.document],
            document_forms=[DocumentForm(D.PURCHASE_ORDER, source_document_ids=[self.document.document_id])])
        self.repository.save(self.workspace, expected_revision=0)

    def test_inventory_lists_empty_templates_and_linked_originals(self):
        result = inventory(self.workspace, [D.QUOTATION, D.PURCHASE_ORDER])
        self.assertEqual(result[0]["原件数"], 0)
        self.assertEqual(result[0]["填写确认"], "")
        self.assertEqual(result[1]["原件数"], 1)

    def test_source_collection_preserves_confirmed_values_and_original(self):
        with patch("documents.intake.collect_source", return_value=("Synthetic source",
                [SourceTable("Synthetic sheet", [["Order No", "SYN-UI-1"]])],
                [ExtractedField("quantity", "5"), ExtractedField("order_no", "SYN-UI-1")], [])):
            collected = collect_document(self.repository, self.blobs,
                self.workspace.case.case_id, self.document.document_id, self.actor)
        version = collected.documents[0].versions[0]
        field = next(item for item in version.fields if item.name == "quantity")
        self.assertEqual((field.value, field.confirmed_value), ("3", "4"))
        self.assertEqual(self.blobs.get(version.storage_path), self.content)
        self.assertEqual(version.source_tables[0].rows[0][1], "SYN-UI-1")
        self.assertTrue(collected.review_tasks)

    def test_reclassification_is_audited_and_keeps_original(self):
        changed = reclassify(self.repository, self.workspace.case.case_id,
            self.document.document_id, self.actor, D.SALES_CONTRACT, "seller", "Synthetic classification reviewed")
        self.assertIs(changed.documents[0].document_type, D.SALES_CONTRACT)
        self.assertEqual(changed.documents[0].versions[0].storage_path,
                         self.document.versions[0].storage_path)
        self.assertEqual(changed.document_forms[0].source_document_ids, [])
        self.assertEqual(changed.audit[-1].action, "document_reclassified")
        with self.assertRaises(CrossBotError):
            reclassify(self.repository, changed.case.case_id, self.document.document_id,
                       Actor("synthetic-finance", ["finance"], authenticated=True), D.PURCHASE_ORDER,
                       "external", "Synthetic unauthorized change")

    def test_named_bundle_contains_structure_and_identical_original(self):
        from documents.service import DocumentService
        service = DocumentService(self.repository, self.blobs)
        with ZipFile(BytesIO(export_bundle(self.workspace, service, self.actor))) as archive:
            self.assertIn("结构化数据.json", archive.namelist())
            source = next(name for name in archive.namelist() if name.endswith("Synthetic buyer PO.txt"))
            self.assertTrue(source.startswith("外部资料/买方订单 PO/"))
            self.assertEqual(archive.read(source), self.content)

    def test_authenticated_admin_sees_both_groups_and_source_tables(self):
        from streamlit.testing.v1 import AppTest
        previous_module = sys.modules.pop("w3_app", None)
        def restore_app_module():
            sys.modules.pop("w3_app", None)
            if previous_module is not None:
                sys.modules["w3_app"] = previous_module
        self.addCleanup(restore_app_module)
        with patch.dict(os.environ, {"CROSSBOT_W3_DATA": str(self.root)}):
            module = importlib.import_module("w3_app")
            for store in (module.repository, module.identity, module.defaults_store):
                self.addCleanup(store.close)
            app = AppTest.from_string("import w3_app\nw3_app.render()", default_timeout=20)
            app.session_state["w3_actor"] = module.Actor(
                self.actor.user_id, list(self.actor.roles), authenticated=True)
            app.run()
            self.assertFalse(app.exception, [item.message for item in app.exception])
            labels = [tab.label for tab in app.tabs]
            self.assertEqual(labels, ["文件分类与查看", "生成与导出"])
            self.assertFalse(any("real_transaction" in str(item.value) for item in app.text))
            self.assertTrue(any("SYN-UI-1" in str(frame.value) for frame in app.dataframe))


if __name__ == "__main__":
    unittest.main()

