"""Synthetic W1 handoff, current-source comparisons and real local OCR tests."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from auth import AuthService
from auth.cases import CaseAccessService
from case_workflow.inquiry_handoff import (FIELD_LABELS, candidate_trade,
    compare_order_draft, import_order_draft, read_order_draft)
from case_workflow.service import CaseService
from core.errors import CrossBotError, ErrorCode
from core.identity import Role
from core.models import (Actor, Case, CaseStatus, CrossCheckStatus as Status, Document,
    DocumentStatus, DocumentType, DocumentVersion, ExtractedField, FieldValueStatus,
    TradeData, TradeLine, Workspace, to_dict, utc_now)
from documents.ocr import OCRBackend, RecognizedLine
from documents.service import DocumentService
from documents.workbench import collect_document
from providers.windows_ocr import WindowsOCREngine
from review.access import shared_w3_session
from review.service import ReviewService
from storage.blobs import LocalBlobStore
from storage.sqlite import SQLiteWorkspaceRepository
from storage.sqlite_store import SQLiteStore
from ui.w3_panel import _HandoffRepository
from validation.rules import DeterministicRules


VALUES = {"order_no": "SYN-DEMO", "buyer": "Synthetic Buyer", "destination_country": "DE",
    "product_code": "SKU-ABC", "description": "Synthetic lamp", "quantity": "120",
    "unit_price": "12.50", "currency": "USD", "incoterm": "FOB", "payment_terms": "T/T"}
LABELS = {"order_no": "Order no", "buyer": "Buyer", "destination_country": "Destination country",
    "product_code": "Product code", "description": "Product name", "quantity": "Quantity",
    "unit_price": "Unit price", "currency": "Currency", "incoterm": "Incoterm", "payment_terms": "Payment terms"}


def metadata():
    return {"w1_order_draft": {"inquiry_id": "synthetic-inquiry", "created_by": "synthetic-owner",
        "messages": [{"role": "user", "content": "Synthetic original request"}],
        "fields": {name: {"value": value, "evidence": name + ": " + value, "confirmed": False}
                   for name, value in VALUES.items()}}}


def order_document():
    version = DocumentVersion(original_filename="synthetic.xlsx", status=DocumentStatus.PARSED,
        fields=[ExtractedField({"buyer": "customer_name", "description": "product_name"}.get(name, name),
            value=value, sheet_name="Order", cell_reference=f"B{index}")
            for index, (name, value) in enumerate(VALUES.items(), 1)])
    return Document(document_type=DocumentType.PURCHASE_ORDER,
        current_version_id=version.version_id, versions=[version])


def order_image():
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (1600, 900), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 42)
    for index, (name, value) in enumerate(VALUES.items()):
        draw.text((40, 30 + index * 70), LABELS[name] + ": " + value, fill="black", font=font)
    data = BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


class InquiryHandoffTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.repo = SQLiteWorkspaceRepository(Path(folder.name) / "workspace.db")
        self.addCleanup(self.repo.close)
        self.blobs = LocalBlobStore(Path(folder.name) / "blobs")
        self.actor = Actor("synthetic-owner", ["sales"], authenticated=True)
        self.cases = CaseService(self.repo, DeterministicRules())
        self.ws = Workspace(Case(case_number="SYN-HANDOFF", metadata=metadata()),
            trade=TradeData(currency=""))
        self.repo.save(self.ws, expected_revision=0)
        self.document = order_document()
        self.ws.documents = [self.document]

    def checks(self, document_id=None):
        return {check.left_field: check for check in compare_order_draft(self.ws,
            self.ws.case.metadata, document_id or self.document.document_id)}

    def test_absent_draft_is_noop_and_partial_fields_stay_missing(self):
        self.assertIsNone(read_order_draft({}))
        self.assertEqual(compare_order_draft(self.ws, {}), [])
        del self.ws.case.metadata["w1_order_draft"]["fields"]["buyer"]
        self.assertIs(self.checks()["buyer"].status, Status.PENDING_REVIEW)

    def test_metadata_confirmed_flag_never_becomes_signoff(self):
        self.ws.case.metadata["w1_order_draft"]["fields"]["buyer"]["confirmed"] = True
        draft = read_order_draft(self.ws.case.metadata)
        self.assertFalse(draft["fields"]["buyer"]["confirmed"])
        self.assertTrue(self.ws.case.metadata["w1_order_draft"]["fields"]["buyer"]["confirmed"])
        self.assertTrue(all(check.status is Status.PENDING_REVIEW for check in self.checks().values()))

    def test_equal_candidates_remain_pending_with_version_and_sources(self):
        before = to_dict(self.ws)
        checks = self.checks()
        self.assertEqual(len(checks), 10)
        self.assertTrue(all(check.status is Status.PENDING_REVIEW for check in checks.values()))
        self.assertIn("B1", checks["order_no"].right_source)
        self.assertIn("synthetic-inquiry", checks["order_no"].left_source)
        self.assertEqual(checks["order_no"].right_version_id, self.document.current_version_id)
        self.assertEqual(to_dict(self.ws), before)

    def test_conflicts_are_fail_and_numeric_format_is_exact(self):
        self.document.versions[0].fields[0].value = "OTHER-ORDER"
        self.document.versions[0].fields[5].value = "120.00"
        checks = self.checks()
        self.assertIs(checks["order_no"].status, Status.FAIL)
        self.assertIs(checks["quantity"].status, Status.PENDING_REVIEW)
        self.document.versions[0].fields[5].value = "121"
        self.assertIs(self.checks()["quantity"].status, Status.FAIL)

    def test_missing_invalid_and_ambiguous_fields_are_pending(self):
        for value in (None, "", "NaN", "1,200", "-1", "0", "=1+1"):
            with self.subTest(value=value):
                self.document.versions[0].fields[5].value = value
                self.assertIs(self.checks()["quantity"].status, Status.PENDING_REVIEW)
        self.document.versions[0].fields.append(ExtractedField("buyer", value="Other"))
        self.assertIs(self.checks()["buyer"].status, Status.PENDING_REVIEW)

    def test_only_current_version_and_effective_human_value_are_used(self):
        old = self.document.versions[0]
        current = deepcopy(old)
        current.version_id = "synthetic-v2"
        current.version_number = 2
        current.fields[0].value = "OTHER-ORDER"
        self.document.versions.append(current)
        self.document.current_version_id = current.version_id
        self.assertIs(self.checks()["order_no"].status, Status.FAIL)
        current.fields[0].status = FieldValueStatus.CONFIRMED
        current.fields[0].confirmed_by = self.actor.user_id
        current.fields[0].confirmed_at = utc_now()
        current.fields[0].confirmed_value = VALUES["order_no"]
        self.assertIs(self.checks()["order_no"].status, Status.PENDING_REVIEW)
        self.assertEqual(old.fields[0].value, VALUES["order_no"])

    def test_failed_unparsed_and_unselected_sources_never_use_old_fields(self):
        self.document.versions[0].status = DocumentStatus.PARSE_FAILED
        self.assertTrue(all(check.right_value is None for check in self.checks().values()))
        self.assertTrue(all(check.status is Status.PENDING_REVIEW
            for check in compare_order_draft(self.ws, self.ws.case.metadata)))
        self.document.deleted_at = utc_now()
        with self.assertRaises(CrossBotError):
            self.checks()
        with self.assertRaises(CrossBotError):
            compare_order_draft(self.ws, self.ws.case.metadata, "another-case-document")

    def test_manual_import_uses_update_trade_without_confirmation_or_metadata_edits(self):
        before = deepcopy(self.ws.case.metadata)
        result = import_order_draft(self.cases, self.ws.case.case_id, self.actor,
            self.ws.case.metadata, expected_revision=self.ws.revision)
        self.assertEqual(result.trade.buyer, VALUES["buyer"])
        self.assertEqual(result.trade.line_items[0].quantity, "120")
        self.assertEqual(result.trade.line_items[0].unit, "")
        self.assertIsNone(result.trade.confirmed_by)
        self.assertIsNone(result.trade.confirmed_at)
        self.assertIs(result.case.status, CaseStatus.PENDING_REVIEW)
        self.assertEqual(result.case.metadata, before)
        self.assertFalse(result.approvals)
        event = next(item for item in result.audit if item.action == "trade_updated")
        self.assertIn("synthetic-inquiry", event.reason)
        self.assertIn("evidence", event.reason)

    def test_existing_human_values_and_ambiguous_line_items_are_preserved(self):
        trade = TradeData(buyer="Human buyer", supplied_fields=["buyer"], currency="EUR")
        result = candidate_trade(trade, read_order_draft(self.ws.case.metadata))
        self.assertEqual(result.buyer, "Human buyer")
        self.assertEqual(result.currency, "EUR")
        trade.line_items = [TradeLine("A", "A", "1", "1"), TradeLine("B", "B", "1", "1")]
        with self.assertRaises(CrossBotError):
            candidate_trade(trade, read_order_draft(self.ws.case.metadata))

    def test_case_pending_markers_are_not_real_missing_draft_values(self):
        data = metadata()
        data["w1_order_draft"]["fields"]["buyer"]["value"] = None
        data["w1_order_draft"]["fields"]["destination_country"]["value"] = None
        candidate = candidate_trade(TradeData(buyer="pending buyer", destination_country="pending country"),
            read_order_draft(data))
        self.assertEqual(candidate.buyer, "")
        self.assertEqual(candidate.destination_country, "")
        self.ws.case.customer_name = "pending buyer"
        checks = compare_order_draft(self.ws, data, self.document.document_id)
        self.assertIs(next(check for check in checks if check.left_field == "buyer").status, Status.PENDING_REVIEW)

    def test_bad_numbers_stale_revisions_ai_and_closed_case_cannot_import(self):
        original = to_dict(self.repo.get(self.ws.case.case_id))
        data = metadata()
        data["w1_order_draft"]["fields"]["quantity"]["value"] = "NaN"
        with self.assertRaises(CrossBotError):
            import_order_draft(self.cases, self.ws.case.case_id, self.actor, data,
                expected_revision=self.ws.revision)
        with self.assertRaises(CrossBotError) as failure:
            import_order_draft(self.cases, self.ws.case.case_id, self.actor, metadata(), expected_revision=-1)
        self.assertIs(failure.exception.code, ErrorCode.CONFLICT)
        with self.assertRaises(CrossBotError):
            import_order_draft(self.cases, self.ws.case.case_id,
                Actor("ai", ["admin"], authenticated=True, kind="ai"), metadata(), expected_revision=self.ws.revision)
        self.assertEqual(to_dict(self.repo.get(self.ws.case.case_id))["trade"], original["trade"])
        stored = self.repo.get(self.ws.case.case_id)
        stored.case.status = CaseStatus.CLOSED
        self.repo.save(stored, expected_revision=stored.revision)
        with self.assertRaises(CrossBotError):
            import_order_draft(self.cases, self.ws.case.case_id, self.actor, metadata(), expected_revision=stored.revision)

    def test_malformed_draft_rejected_without_mutation(self):
        for key, value in (("fields", []), ("inquiry_id", None), ("messages", "bad"), ("created_by", {})):
            with self.subTest(key=key):
                data = metadata()
                data["w1_order_draft"][key] = value
                before = deepcopy(data)
                with self.assertRaises(CrossBotError):
                    read_order_draft(data)
                self.assertEqual(data, before)

    def collect(self, filename, content):
        service = DocumentService(self.repo, self.blobs)
        workspace = service.upload(self.ws.case.case_id, self.actor, filename, content, DocumentType.PURCHASE_ORDER)
        document = workspace.documents[-1]
        self.ws = collect_document(self.repo, self.blobs, self.ws.case.case_id, document.document_id,
            self.actor, ocr_images=filename.endswith(".png"), prefill=False)
        self.document = self.ws.documents[-1]
        self.assertEqual(self.blobs.get(self.document.versions[-1].storage_path), content)
        return self.checks()

    def test_real_excel_upload_intake_and_comparison(self):
        from openpyxl import Workbook
        book = Workbook()
        book.active.title = "Order"
        for name, value in VALUES.items():
            book.active.append([LABELS[name], value])
        data = BytesIO()
        book.save(data)
        book.close()
        checks = self.collect("synthetic-order.xlsx", data.getvalue())
        self.assertTrue(all(check.right_value is not None for check in checks.values()))
        self.assertTrue(all(check.status is Status.PENDING_REVIEW for check in checks.values()))
        self.assertIn("Order", checks["quantity"].right_source)

    def test_real_text_pdf_upload_intake_and_comparison(self):
        from reportlab.pdfgen.canvas import Canvas
        data = BytesIO()
        canvas = Canvas(data)
        for index, (name, value) in enumerate(VALUES.items()):
            canvas.drawString(40, 790 - 25 * index, LABELS[name] + ": " + value)
        canvas.save()
        checks = self.collect("synthetic-order.pdf", data.getvalue())
        self.assertTrue(all(check.right_value is not None for check in checks.values()))
        self.assertTrue(all(check.status is Status.PENDING_REVIEW for check in checks.values()))
        self.assertIn("page:1", checks["quantity"].right_source)

    def test_unknown_ocr_confidence_preserves_candidate_and_never_approves(self):
        from documents.field_mapping import from_ocr_lines
        fields = from_ocr_lines([RecognizedLine("Quantity: 120", None, (0, 0, 300, 30))])
        self.assertIsNone(fields[0].confidence)
        self.assertIs(fields[0].status, FieldValueStatus.CANDIDATE)
        self.assertIn("not provided", fields[0].note)
        paired = from_ocr_lines([RecognizedLine("Quantity", None, (0, 0, 100, 30)),
            RecognizedLine("120", None, (120, 0, 180, 30))])
        self.assertEqual(paired[0].value, "120")
        self.assertIsNone(paired[0].confidence)

    @unittest.skipUnless(WindowsOCREngine.available(), "Windows OCR languages are unavailable")
    def test_real_windows_image_upload_ocr_and_comparison(self):
        with patch.dict("sys.modules", {"rapidocr_onnxruntime": None}):
            checks = self.collect("synthetic-order.png", order_image())
        self.assertEqual(checks["quantity"].right_value, "120")
        self.assertEqual(checks["currency"].right_value, "USD")
        self.assertIs(checks["quantity"].status, Status.PENDING_REVIEW)
        self.assertIsNone(self.ws.trade.confirmed_by)
        self.assertFalse(self.ws.approvals)

    @unittest.skipUnless(WindowsOCREngine.available(), "Windows OCR languages are unavailable")
    def test_real_scanned_pdf_uses_local_windows_ocr(self):
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen.canvas import Canvas
        data = BytesIO()
        canvas = Canvas(data, pagesize=(800, 450))
        canvas.drawImage(ImageReader(BytesIO(order_image())), 0, 0, width=800, height=450)
        canvas.save()
        with patch.dict("sys.modules", {"rapidocr_onnxruntime": None}):
            result = OCRBackend().recognize_pdf(data.getvalue())
        self.assertTrue(any("120" in line.text for line in result.lines))
        self.assertTrue(all(line.page_number == 1 for line in result.lines))
        self.assertTrue(all(line.confidence is None for line in result.lines))
        with patch.dict("sys.modules", {"rapidocr_onnxruntime": None}):
            checks = self.collect("synthetic-scanned-order.pdf", data.getvalue())
        self.assertEqual(checks["quantity"].right_value, "120")
        self.assertIs(checks["quantity"].status, Status.PENDING_REVIEW)

    def test_missing_ocr_requires_manual_input_and_preserves_original(self):
        with patch.object(OCRBackend, "recognize_image", side_effect=__import__(
                "documents.ocr", fromlist=["OCRUnavailableError"]).OCRUnavailableError("unavailable")):
            checks = self.collect("synthetic-order.png", b"synthetic image bytes")
        self.assertTrue(all(check.status is Status.PENDING_REVIEW for check in checks.values()))
        result = ReviewService(self.repo, DeterministicRules()).enter_manual_field(
            self.ws.case.case_id, self.document.document_id, "quantity", "120", self.actor,
            "Human checked the original synthetic image")
        self.assertEqual(result.documents[-1].versions[-1].fields[0].confirmed_by, self.actor.user_id)
        self.assertFalse(result.approvals)


    def panel(self):
        from streamlit.testing.v1 import AppTest
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        app = AppTest.from_string(
            "import streamlit as st\n"
            "from ui.w3_panel import _render_inquiry_handoff\n"
            "cases = st.session_state['cases']\n"
            "workspace = cases.repository.get(st.session_state['case_id'])\n"
            "_render_inquiry_handoff(workspace.case, workspace, st.session_state['actor'], cases)\n",
            default_timeout=20)
        app.session_state["cases"] = self.cases
        app.session_state["case_id"] = self.ws.case.case_id
        app.session_state["actor"] = self.actor
        app.run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        return app

    def test_ui_shows_sources_without_auto_import_and_requires_ack(self):
        app = self.panel()
        self.assertTrue(any("synthetic-inquiry" in item.value for item in app.caption))
        self.assertTrue(next(item for item in app.button if item.label == "导入未确认候选").disabled)
        self.assertEqual(self.repo.get(self.ws.case.case_id).trade.buyer, "")
        next(item for item in app.checkbox if item.label == "将草稿补入订单空字段，保留为未确认候选").check().run()
        next(item for item in app.button if item.label == "导入未确认候选").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        result = self.repo.get(self.ws.case.case_id)
        self.assertEqual(result.trade.buyer, VALUES["buyer"])
        self.assertIsNone(result.trade.confirmed_by)
        self.assertFalse(result.approvals)

    def test_ui_manual_review_is_human_only_and_keeps_raw_candidate(self):
        app = self.panel()
        self.assertTrue(next(item for item in app.button if item.label == "保存人工复核值").disabled)
        next(item for item in app.checkbox if item.label == "我已对照平台订单原件核实本次录入值").check().run()
        next(item for item in app.selectbox if item.label == "平台订单字段").select("quantity")
        next(item for item in app.text_input if item.label == "原件核实值").input("121")
        next(item for item in app.text_input if item.label == "录入或修正理由").input("Checked original")
        next(item for item in app.button if item.label == "保存人工复核值").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        result = self.repo.get(self.ws.case.case_id)
        field = next(item for item in result.documents[0].versions[0].fields if item.name == "quantity")
        self.assertEqual(field.value, "120")
        self.assertEqual(field.confirmed_value, "121")
        self.assertTrue(any("FAIL" in item.value for item in app.warning))
        self.assertFalse(result.approvals)


class SharedCaseHandoffTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = SQLiteStore(Path(folder.name) / "shared.db")
        self.addCleanup(self.store.close)
        self.auth = AuthService(self.store)
        self.auth.bootstrap_admin("synthetic-admin", "synthetic-password-123")
        self.token = self.auth.login("synthetic-admin", "synthetic-password-123")
        self.case = Case(case_number="SYN-SHARED", customer_name="Synthetic Buyer",
            destination_country="DE", metadata=metadata())
        CaseAccessService(self.auth).create_case(self.token, self.case)
        self.repo = _HandoffRepository(self.auth, self.token, self.case.case_id)
        self.addCleanup(self.repo.close)
        with shared_w3_session(self.auth, self.token, self.case.case_id) as actor:
            self.repo.initialize(actor)

    def test_fresh_frozen_shared_metadata_survives_w3_updates(self):
        current = self.store.get_case(self.case.case_id)
        current.metadata["w1_order_draft"]["inquiry_id"] = "new-synthetic-snapshot"
        self.store.save_case(current)
        with shared_w3_session(self.auth, self.token, self.case.case_id) as actor:
            workspace = self.repo.get(self.case.case_id)
            self.assertEqual(workspace.case.metadata, current.metadata)
            result = import_order_draft(CaseService(self.repo, DeterministicRules()), self.case.case_id,
                actor, current.metadata, expected_revision=workspace.revision)
        self.assertEqual(result.case.metadata["w1_order_draft"], current.metadata["w1_order_draft"])
        self.assertEqual(self.store.get_case(self.case.case_id).metadata["w1_order_draft"],
            current.metadata["w1_order_draft"])

    def test_initial_case_placeholders_are_cleared_when_draft_fields_are_null(self):
        current = self.store.get_case(self.case.case_id)
        for name in ("buyer", "destination_country"):
            current.metadata["w1_order_draft"]["fields"][name]["value"] = None
        current.customer_name = "pending buyer"
        current.destination_country = "pending country"
        self.store.save_case(current)
        workspace = self.repo.get(self.case.case_id)
        self.assertEqual(workspace.trade.buyer, "")
        self.assertEqual(workspace.trade.destination_country, "")

    def test_other_document_user_requires_admin_assignment(self):
        user = self.auth.create_user(self.token, "synthetic-doc", "synthetic-password-123",
            "Synthetic document user", [Role.DOCUMENT])
        token = self.auth.login("synthetic-doc", "synthetic-password-123")
        with self.assertRaises(CrossBotError):
            _HandoffRepository(self.auth, token, self.case.case_id)
        self.auth.assign_case(self.token, self.case.case_id, user.user_id)
        repository = _HandoffRepository(self.auth, token, self.case.case_id)
        self.addCleanup(repository.close)
        self.assertEqual(repository.get(self.case.case_id).case.case_id, self.case.case_id)


if __name__ == "__main__":
    unittest.main()
