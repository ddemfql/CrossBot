"""Navigation, step state and classified uploads against synthetic temporary data."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from auth import AuthService
from auth.cases import CaseAccessService
from core.errors import CrossBotError
from core.identity import Role
from core.models import Case
from core.v3 import LifecycleStatus, ResourceKind, SourceKind
from knowledge.governance import KnowledgeService
from storage.blobs import LocalBlobStore
from storage.bootstrap import DEMO_ID, import_demo_case
from storage.sqlite_store import SQLiteStore
from ui.resources_panel import SAMPLES, parse_upload, save_upload_draft

APP = Path(__file__).resolve().parents[1] / "app.py"
PASSWORD = "synthetic-ui-password-123"


class WorkbenchUI(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.store = SQLiteStore(self.folder / "ui.db")
        self.auth = AuthService(self.store)
        self.auth.bootstrap_admin("ui-admin", PASSWORD)
        self.token = self.auth.login("ui-admin", PASSWORD)
        self.service = KnowledgeService(self.auth)
        self.blobs = LocalBlobStore(self.folder / "originals")
        self.patcher = patch("storage.bootstrap.open_store", return_value=self.store)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def page(self, *, real=False, route=None, token=None):
        app = AppTest.from_file(str(APP), default_timeout=20)
        app.session_state["session_token"] = token or self.token
        app.session_state["workbench_environment"] = "真实工作区" if real else "模拟演示区"
        if route:
            app.session_state["workbench_page"] = route
        return app.run()

    @staticmethod
    def upload(kind, *, resource_id="resource-001"):
        raw = json.dumps({"kind": kind.value, "entries": [{"resource_id": resource_id,
                            "content": SAMPLES[kind]}]}, ensure_ascii=False).encode("utf-8")
        stream = io.BytesIO(raw)
        stream.name = "synthetic.json"
        return stream

    def test_single_parent_three_separate_pages_without_case(self):
        page = self.page(route="products")
        self.assertFalse(page.exception)
        parent = next(item for item in page.expander if item.label == "资料与知识")
        self.assertEqual([item.label for item in parent.button], ["产品资料", "知识库", "风险词库"])
        for route, title in (("products", "产品资料"), ("knowledge", "知识库"), ("risk_terms", "风险词库")):
            page.button(key=f"nav_{route}").click().run()
            self.assertFalse(page.exception)
            self.assertEqual(page.title[0].value, title)
            self.assertTrue(any(item.label == f"选择{title}文件" for item in page.get("file_uploader")))
            self.assertFalse(any(item.label == "当前 Case" for item in page.selectbox))

    def test_admin_uploads_each_category_to_its_own_draft_and_keeps_original(self):
        for route, kind in (("products", ResourceKind.PRODUCT), ("knowledge", ResourceKind.KNOWLEDGE),
                            ("risk_terms", ResourceKind.PROHIBITED_TERM)):
            with self.subTest(kind=kind):
                uploaded = self.upload(kind, resource_id=f"{kind.value}-test")
                with patch("ui.resources_panel.st.file_uploader", return_value=uploaded):
                    page = self.page(real=True, route=route)
                    self.assertFalse(page.exception)
                    next(item for item in page.text_input if item.label == "内容来源（必填）").input("Synthetic verified source")
                    next(item for item in page.button if item.label.startswith("保存到")).click().run()
                    self.assertFalse(page.exception)
                    self.assertFalse(page.error)
                    page.run()
                versions = self.service.list_versions(self.token, kind=kind)
                self.assertEqual(len(versions), 1)
                version = versions[0]
                self.assertEqual(version.status, LifecycleStatus.DRAFT)
                self.assertEqual(version.kind, kind)
                originals = LocalBlobStore(self.folder / "resource-files")
                self.assertEqual(originals.get(version.content["_upload"]["sha256"]), uploaded.getvalue())
                self.assertFalse(self.service.list_published(self.token, kind=kind))

    def test_wrong_category_invalid_fields_and_duplicate_ids_rejected_before_save(self):
        for kind in ResourceKind:
            raw = self.upload(kind).getvalue()
            other = next(value for value in ResourceKind if value != kind)
            with self.assertRaises(CrossBotError):
                parse_upload("test.json", raw, other)
        payload = {"kind": "product", "entries": [{"resource_id": "one", "content": {"text": "wrong"}}]}
        with self.assertRaises(CrossBotError):
            parse_upload("bad.json", json.dumps(payload).encode(), ResourceKind.PRODUCT)
        payload["entries"] = [{"resource_id": "one", "content": {"name": "Lamp"}}] * 2
        with self.assertRaises(CrossBotError):
            parse_upload("bad.json", json.dumps(payload).encode(), ResourceKind.PRODUCT)
        self.assertFalse(self.service.list_versions(self.token))

    def test_selected_upload_preserves_versions_rejects_repetition_and_cross_category_id(self):
        arguments = dict(kind=ResourceKind.PRODUCT, resource_id="lamp", content={"name": "Lamp"},
                         source="Synthetic source", raw=b"original v1", filename="original.json")
        first = save_upload_draft(self.service, self.token, self.blobs, **arguments)
        with self.assertRaises(CrossBotError):
            save_upload_draft(self.service, self.token, self.blobs, **arguments, parent_version_id=first.version_id)
        arguments.update(content={"name": "Lamp v2"}, raw=b"original v2")
        second = save_upload_draft(self.service, self.token, self.blobs, **arguments, parent_version_id=first.version_id)
        self.assertEqual(second.version_number, 2)
        self.assertEqual(self.blobs.get(first.content["_upload"]["sha256"]), b"original v1")
        arguments.update(kind=ResourceKind.KNOWLEDGE, content={"text": "Business rule", "category": "FAQ"})
        with self.assertRaises(CrossBotError):
            save_upload_draft(self.service, self.token, self.blobs, **arguments, parent_version_id=second.version_id)

    def test_reader_cannot_upload_even_with_forged_ui_request(self):
        user = self.auth.create_user(self.token, "reader", PASSWORD, "Reader", [Role.CUSTOMER_SERVICE])
        token = self.auth.login(user.username, PASSWORD)
        page = self.page(real=True, route="products", token=token)
        self.assertFalse(page.exception)
        self.assertFalse(page.get("file_uploader"))
        with self.assertRaises(CrossBotError):
            save_upload_draft(self.service, token, self.blobs, kind=ResourceKind.PRODUCT,
                resource_id="forged", content={"name": "Lamp"}, source="Synthetic", raw=b"raw", filename="a.json")
        self.assertFalse(list(self.blobs.root.iterdir()))

    def test_real_and_demo_inputs_and_tools_are_isolated(self):
        import_demo_case(self.store)
        real = Case(case_number="REAL-UI", customer_name="Synthetic real-mode buyer", destination_country="DE")
        CaseAccessService(self.auth).create_case(self.token, real)
        with patch("providers.llm_client.HttpChatClient.complete") as network:
            page = self.page(real=True)
            self.assertEqual(page.session_state["selected_case_id"], real.case_id)
            self.assertEqual(page.text_area(key="sales_inquiry").value, "")
            self.assertNotIn("sales_demo", [item.key for item in page.button])
            self.assertNotIn("customer_generate", [item.key for item in page.button])
            page.text_area(key="sales_inquiry").input("Real input retained")
            page.button(key="sales_next").click().run()
            self.assertTrue(page.button(key="sales_generate").disabled)
            page.session_state["workbench_environment"] = "模拟演示区"
            page.run()
            self.assertEqual(page.session_state["selected_case_id"], DEMO_ID)
            self.assertIn("sales_demo", [item.key for item in page.button])
            page.session_state["workbench_environment"] = "真实工作区"
            page.run()
            self.assertEqual(page.text_area(key="sales_inquiry").value, "Real input retained")
            page.button(key="nav_cases").click().run()
            self.assertNotIn("import_demo", [item.key for item in page.button])
            network.assert_not_called()

    def test_steps_and_other_pages_preserve_inputs_language_and_versions(self):
        import_demo_case(self.store)
        page = self.page()
        page.text_area(key="sales_inquiry").input("Need 500 lamps")
        page.selectbox(key="sales_language").select("德文")
        page.button(key="sales_next").click().run()
        page.button(key="sales_generate").click().run()
        self.assertFalse(page.exception)
        page.button(key="nav_knowledge").click().run()
        page.button(key="nav_sales").click().run()
        page.button(key="sales_step_1").click().run()
        self.assertEqual(page.text_area(key="sales_inquiry").value, "Need 500 lamps")
        self.assertEqual(page.selectbox(key="sales_language").value, "德文")
        page.button(key="sales_demo").click().run()
        self.assertEqual(page.selectbox(key="sales_language").value, "德文")
        self.assertEqual(len(page.session_state["sales_conversation"].turns), 1)
        page.button(key="sales_step_3").click().run()
        self.assertTrue(page.get("download_button"))
        self.assertEqual(page.session_state["sales_conversation"].request.inquiry, "Need 500 lamps")

    def test_settings_survive_page_navigation_and_no_api_test_in_real_mode(self):
        page = self.page(real=True, route="model")
        page.text_input(key="provider_key").input("synthetic-not-a-real-key").run()
        page.checkbox(key="provider_confirm").check().run()
        page.button(key="nav_products").click().run()
        page.button(key="nav_model").click().run()
        self.assertEqual(page.text_input(key="provider_key").value, "synthetic-not-a-real-key")
        self.assertTrue(page.checkbox(key="provider_confirm").value)
        self.assertNotIn("provider_test", [item.key for item in page.button])

    def test_offline_fallback_needs_no_case_and_never_writes_business_state(self):
        with patch("providers.llm_client.HttpChatClient.complete") as network:
            page = self.page(real=True, route="offline")
            page.button(key="offline_sales").click().run()
            page.button(key="offline_listing").click().run()
            self.assertFalse(page.exception)
            self.assertFalse(page.error)
            self.assertEqual(page.session_state["offline_sales_result"].turns[0].response.provider, "mock")
            self.assertFalse(self.store.list_cases())
            self.assertNotIn("sales_conversation", page.session_state)
            network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
