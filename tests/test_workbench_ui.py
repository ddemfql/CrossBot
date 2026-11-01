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
from core.errors import CrossBotError, ErrorCode
from core.identity import Role
from core.models import Case, CaseStatus
from core.v3 import LifecycleStatus, ResourceKind, SourceKind
from knowledge.governance import KnowledgeService
from storage.blobs import LocalBlobStore
from storage.bootstrap import import_demo_case
from storage.sqlite_store import SQLiteStore
from ui.resources_panel import SAMPLES, parse_upload, save_upload_draft
from workflows.inquiry import InquiryService

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
        for target in ("ui.auth_panel.browser_cookie", "providers.llm_client.HttpChatClient.complete"):
            patcher = patch(target, return_value=None)
            mocked = patcher.start()
            self.addCleanup(patcher.stop)
            if target.endswith("complete"):
                self.provider = mocked
                self.provider.return_value = '{"reply":"Synthetic reply", "fields":{}}'

    def page(self, *, route=None, token=None):
        app = AppTest.from_file(str(APP), default_timeout=20)
        app.session_state["session_token"] = token or self.token
        if route:
            app.session_state["workbench_page"] = route
        return app.run()

    def configure_provider(self, page):
        page.button(key="nav_model").click().run()
        page.text_input(key="provider_url").input("https://provider.invalid").run()
        page.text_input(key="provider_key").input("synthetic-not-a-real-key").run()
        page.checkbox(key="provider_confirm").check().run()
        page.button(key="nav_sales").click().run()
        self.assertFalse(page.exception)
        return page

    def submit(self, page, text):
        page.text_area(key="inquiry_customer_text").input(text)
        next(button for button in page.button if button.label == "保存并生成回复").click().run()
        self.assertFalse(page.exception)
        self.assertFalse(page.error)
        return page.session_state["active_inquiry"]

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
                    page = self.page(route=route)
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
        page = self.page(route="products", token=token)
        self.assertFalse(page.exception)
        self.assertFalse(page.get("file_uploader"))
        with self.assertRaises(CrossBotError):
            save_upload_draft(self.service, token, self.blobs, kind=ResourceKind.PRODUCT,
                resource_id="forged", content={"name": "Lamp"}, source="Synthetic", raw=b"raw", filename="a.json")
        self.assertFalse(list(self.blobs.root.iterdir()))

    def test_real_only_navigation_requires_confirmation_and_hides_demo_cases(self):
        demo = import_demo_case(self.store)
        real = Case(case_number="REAL-UI", customer_name="Synthetic buyer", destination_country="DE")
        CaseAccessService(self.auth).create_case(self.token, real)
        page = self.page()
        self.assertFalse(page.exception)
        self.assertFalse(any(widget.label == "当前 Case" for widget in page.selectbox))
        self.assertTrue(next(button for button in page.button if button.label == "保存并生成回复").disabled)
        page.button(key="nav_model").click().run()
        page.text_input(key="provider_key").input("synthetic-not-a-real-key").run()
        page.button(key="nav_sales").click().run()
        self.assertTrue(next(button for button in page.button if button.label == "保存并生成回复").disabled)
        self.assertNotIn("workbench_environment", [widget.key for widget in page.radio])
        self.assertNotIn("nav_offline", [button.key for button in page.button])
        self.assertNotIn("sales_demo", [button.key for button in page.button])
        self.assertNotIn("customer_generate", [button.key for button in page.button])
        page.button(key="nav_cases").click().run()
        selector = next(widget for widget in page.selectbox if widget.label == "当前 Case")
        self.assertEqual(selector.value, real.case_id)
        self.assertFalse(any(demo.case_number in option for option in selector.options))
        self.assertNotIn("import_demo", [button.key for button in page.button])
        self.assertNotIn("import_animal_lamp_demo", [button.key for button in page.button])
        self.provider.assert_not_called()

    def test_other_pages_and_inquiry_selector_preserve_language_product_and_versions(self):
        product = self.service.create_draft(self.token, resource_id="synthetic-lamp", kind=ResourceKind.PRODUCT,
            content={"name": "Synthetic lamp", "sku": "SYN-001"}, source="Synthetic verified source")
        self.service.submit(self.token, product.version_id)
        reviewer = self.auth.create_user(self.token, "resource-reviewer", PASSWORD, "Reviewer", [Role.REVIEWER])
        reviewer_token = self.auth.login(reviewer.username, PASSWORD)
        self.service.review(reviewer_token, product.version_id, approve=True, reason="Synthetic source verified")
        self.service.publish(self.token, product.version_id)
        draft = self.service.create_draft(self.token, resource_id="unpublished-lamp", kind=ResourceKind.PRODUCT,
            content={"name": "Unpublished lamp"}, source="Synthetic draft source")
        page = self.configure_provider(self.page())
        self.assertEqual(page.selectbox(key="inquiry_product").options,
                         ["按询盘关键词检索", "Synthetic lamp · SYN-001"])
        page.selectbox(key="inquiry_product").set_value(product.resource_id)
        page.selectbox(key="inquiry_language").select("德文")
        inquiry_id = self.submit(page, "Need 500 synthetic lamps")
        inquiry = InquiryService(self.auth).get(self.token, inquiry_id)
        self.assertEqual(inquiry.target_language, "德文")
        self.assertEqual(inquiry.product_resource_id, product.resource_id)
        self.assertEqual(inquiry.citations[0]["version_id"], product.version_id)
        self.assertNotIn(draft.version_id, [citation["version_id"] for citation in inquiry.citations])
        payload = json.loads(self.provider.call_args.args[0][1]["content"])
        self.assertEqual(payload["target_language"], "德文")
        page.button(key="nav_knowledge").click().run()
        page.button(key="nav_sales_history").click().run()
        page.selectbox(key="active_inquiry").set_value(inquiry_id).run()
        self.assertFalse(page.exception)
        self.assertTrue(page.get("download_button"))
        self.assertIn("Need 500 synthetic lamps", [item.value for item in page.markdown])
        page.button(key="nav_sales").click().run()
        page.selectbox(key="active_inquiry").set_value(None).run()
        second_id = self.submit(page, "A separate inquiry")
        self.assertNotEqual(second_id, inquiry_id)
        page.selectbox(key="active_inquiry").set_value(inquiry_id).run()
        page.text_area(key="inquiry_revision_text").input("Make it shorter")
        self.provider.return_value = '{"reply":"Short revised reply", "fields":{}}'
        next(button for button in page.button if button.label == "生成修改稿").click().run()
        self.assertFalse(page.exception)
        inquiry = InquiryService(self.auth).get(self.token, inquiry_id)
        self.assertEqual([message["role"] for message in inquiry.messages], ["user", "assistant", "assistant"])
        self.assertEqual(inquiry.messages[-1]["revision_instruction"], "Make it shorter")
        self.assertEqual(inquiry.messages[-1]["content"], "Short revised reply")
        self.assertEqual(len(InquiryService(self.auth).get(self.token, second_id).messages), 2)
        self.assertFalse(self.store.list_cases())

    def test_generation_failure_preserves_message_across_pages_and_retry(self):
        page = self.configure_provider(self.page())
        self.provider.side_effect = CrossBotError(ErrorCode.PROVIDER_TIMEOUT, "Synthetic provider timeout", retryable=True)
        page.text_area(key="inquiry_customer_text").input("Please quote 500 lamps")
        next(button for button in page.button if button.label == "保存并生成回复").click().run()
        self.assertFalse(page.exception)
        self.assertIn("Synthetic provider timeout", [item.value for item in page.error])
        inquiry_id = page.session_state["active_inquiry"]
        inquiry = InquiryService(self.auth).get(self.token, inquiry_id)
        self.assertTrue(inquiry.awaiting_reply)
        self.assertEqual([message["content"] for message in inquiry.messages], ["Please quote 500 lamps"])
        page.button(key="nav_products").click().run()
        page.button(key="nav_sales").click().run()
        page.selectbox(key="active_inquiry").set_value(inquiry_id).run()
        self.assertTrue(page.button(key="inquiry_retry"))
        self.provider.side_effect = None
        page.button(key="inquiry_retry").click().run()
        self.assertFalse(page.exception)
        self.assertFalse(page.error)
        inquiry = InquiryService(self.auth).get(self.token, inquiry_id)
        self.assertEqual([message["role"] for message in inquiry.messages], ["user", "assistant"])
        self.assertFalse(inquiry.awaiting_reply)
        self.assertEqual(self.provider.call_count, 2)
        self.assertFalse(self.store.list_cases())

    def test_explicit_order_hands_off_one_unconfirmed_case_to_w3(self):
        other = Case(case_number="OTHER-UI", customer_name="Other synthetic buyer", destination_country="DE")
        CaseAccessService(self.auth).create_case(self.token, other)
        text = "We confirm the order: 500 lamps, buyer Synthetic GmbH, destination Germany."
        self.provider.return_value = json.dumps({"reply": "Hello. Your order draft awaits review.", "fields": {
            "quantity": {"value": "500", "evidence": text},
            "buyer": {"value": "Synthetic GmbH", "evidence": text},
            "destination_country": {"value": "Germany", "evidence": text}}})
        page = self.configure_provider(self.page())
        inquiry_id = self.submit(page, text)
        inquiry = InquiryService(self.auth).get(self.token, inquiry_id)
        case = CaseAccessService(self.auth).get_case(self.token, inquiry.case_id)
        self.assertEqual(case.status, CaseStatus.DRAFT)
        self.assertEqual(case.customer_name, "Synthetic GmbH")
        draft = case.metadata["w1_order_draft"]
        self.assertEqual(draft["messages"][0]["content"], text)
        self.assertEqual(draft["fields"]["quantity"]["value"], "500")
        self.assertTrue(all(not field["confirmed"] for field in draft["fields"].values()))
        page.button(key="inquiry_open_case").click().run()
        self.assertFalse(page.exception)
        self.assertEqual(page.session_state["selected_case_id"], case.case_id)
        self.assertEqual(page.session_state["workbench_page"], "documents")
        selector = next(widget for widget in page.selectbox if widget.label == "当前 Case")
        self.assertEqual(selector.value, case.case_id)
        self.assertIn("OTHER-UI · Other synthetic buyer", selector.options)
        next(button for button in page.button if button.label == "启用 W3 单证工作台").click().run()
        self.assertFalse(page.exception)
        self.assertTrue(self.store.get_case(case.case_id).metadata["w3_workspace"])
        page.button(key="nav_sales").click().run()
        page.selectbox(key="active_inquiry").set_value(inquiry_id).run()
        page.button(key="inquiry_open_case").click().run()
        self.assertFalse(page.exception)
        self.assertEqual(len(self.store.list_cases()), 2)
        self.assertEqual(self.provider.call_count, 1)

    def test_settings_survive_page_navigation_and_no_api_test_in_real_mode(self):
        page = self.page(route="model")
        page.text_input(key="provider_key").input("synthetic-not-a-real-key").run()
        page.checkbox(key="provider_confirm").check().run()
        page.button(key="nav_products").click().run()
        page.button(key="nav_model").click().run()
        self.assertEqual(page.text_input(key="provider_key").value, "synthetic-not-a-real-key")
        self.assertTrue(page.checkbox(key="provider_confirm").value)
        self.assertNotIn("provider_test", [item.key for item in page.button])

    def test_removed_offline_route_falls_back_without_generation_or_business_state(self):
        page = self.page(route="offline")
        self.assertFalse(page.exception)
        self.assertNotEqual(page.session_state["workbench_page"], "offline")
        self.assertNotIn("offline_sales", [button.key for button in page.button])
        self.assertNotIn("offline_listing", [button.key for button in page.button])
        self.assertFalse(self.store.list_cases())
        self.assertFalse(InquiryService(self.auth).list(self.token))
        self.provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
