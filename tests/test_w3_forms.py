"""Synthetic seller worksheet, traceability and draft boundary tests."""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from core.errors import CrossBotError
from core.models import (Actor, Case, CrossCheckStatus, DocumentForm,
    DocumentType as D, TradeData, TradeLine, Workspace, to_dict, utc_now)
from documents.catalog import (DOC_LABELS, EXTERNAL_TYPES, FORM_FIELDS, ROW_FIELDS,
    SELLER_TYPES)
from documents.forms import (FormService, form_conflicts, get_form,
    missing_form_fields, render_blank, render_form)
from documents.service import DocumentService
from review.service import ReviewService
from storage.blobs import LocalBlobStore
from storage.sqlite import SQLiteWorkspaceRepository
from validation.rules import DeterministicRules


class SellerFormTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = SQLiteWorkspaceRepository(Path(self.tmp.name) / "synthetic.sqlite")
        self.actor = Actor("synthetic-human", ["admin"], authenticated=True)
        self.trade = TradeData(order_no="SYN-100", seller="Synthetic Seller", buyer="Synthetic Buyer",
            destination_country="DE", currency="USD", incoterm="FCA Testport", payment_terms="30 days",
            transport_mode="sea", line_items=[TradeLine("SYN-1", "Synthetic item", "2", "3",
                packages=1, gross_weight_kg="3", net_weight_kg="2")],
            confirmed_by=self.actor.user_id, confirmed_at=utc_now())
        ws = Workspace(Case(case_number="SYN-FORMS"), trade=self.trade)
        self.case_id = ws.case.case_id
        self.repo.save(ws, expected_revision=0)
        self.forms = FormService(self.repo)
        self.docs = DocumentService(self.repo, LocalBlobStore(Path(self.tmp.name) / "blobs"))

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def save(self, kind=D.QUOTATION, values=None, rows=None, confirm=True):
        return self.forms.save(self.case_id, self.actor, kind,
            values if values is not None else {"document_no": "SYN-Q-1", "document_date": "2026-10-31"},
            rows if rows is not None else [{"description": "Synthetic item", "quantity": "2",
                "unit": "pcs", "unit_price": "3", "amount": "6"}],
            "Synthetic source checked", confirm=confirm)

    def test_catalog_has_all_types_and_18_seller_worksheets(self):
        self.assertEqual(len(SELLER_TYPES), 18)
        self.assertEqual(len(DOC_LABELS), 32)
        self.assertEqual(set(DOC_LABELS), set(D))
        self.assertEqual(set(FORM_FIELDS), set(D))
        self.assertEqual(set(ROW_FIELDS), set(D))
        self.assertFalse(set(SELLER_TYPES) & set(EXTERNAL_TYPES))

    def test_purchase_contract_and_forwarder_receipt_are_external_annotations(self):
        expected = {
            D.PURCHASE_CONTRACT: {"supplier", "buyer", "contract_no", "currency",
                "total_amount", "payment_terms", "delivery_date"},
            D.FORWARDER_RECEIPT: {"vessel", "voyage", "container_no", "seal_no",
                "volume_cbm", "packages", "gross_weight_kg", "reported_received_date", "document_status"},
        }
        for kind, fields in expected.items():
            with self.subTest(kind=kind):
                self.assertIn(kind, EXTERNAL_TYPES)
                self.assertNotIn(kind, SELLER_TYPES)
                self.assertTrue(fields <= FORM_FIELDS[kind].keys())
                self.assertTrue(ROW_FIELDS[kind])
                values = {field: "Synthetic copied value" for field in fields}
                saved = self.forms.save(self.case_id, self.actor, kind, values,
                    [], "Synthetic supplied record", confirm=True)
                self.assertEqual(get_form(saved, kind).values, values)
                with self.assertRaises(CrossBotError):
                    self.docs.generate(self.case_id, self.actor, kind)

    def test_blank_templates_are_empty_and_do_not_need_a_saved_trade(self):
        for kind in SELLER_TYPES:
            output = render_blank(kind)
            text = output.content.decode()
            self.assertIn("DRAFT", text)
            self.assertNotIn("Synthetic", text)
            self.assertNotIn("USD", text)
            self.assertEqual(output.content, render_form(kind, DocumentForm(kind, revision=0)).content)
            self.assertEqual(output.content, render_blank(kind).content)

    def test_external_form_is_annotation_and_cannot_generate_issuer_document(self):
        self.forms.save(self.case_id, self.actor, D.TEST_REPORT,
            {"external_reference": "SYN-REPORT", "recorded_conclusion": "Copied from supplied record"},
            [], "Synthetic supplied record", confirm=True)
        form = get_form(self.repo.get(self.case_id), D.TEST_REPORT)
        self.assertEqual(form.confirmed_by, self.actor.user_id)
        with self.assertRaises(CrossBotError):
            self.docs.generate(self.case_id, self.actor, D.TEST_REPORT)
        with self.assertRaises(CrossBotError):
            render_blank(D.TEST_REPORT)

    def test_confirm_requires_authenticated_human_and_valid_fields(self):
        for actor in (Actor("robot", ["admin"], kind="ai", authenticated=True),
                      Actor("anonymous", ["admin"], authenticated=False)):
            with self.assertRaises(CrossBotError):
                self.forms.save(self.case_id, actor, D.QUOTATION, {}, [], "test", confirm=True)
        with self.assertRaises(CrossBotError):
            self.forms.save(self.case_id, self.actor, D.QUOTATION, {"signature": "invented"}, [], "test")
        with self.assertRaises(CrossBotError):
            self.forms.save(self.case_id, self.actor, D.QUOTATION, {}, [{"unknown": "value"}], "test")
        with self.assertRaises(CrossBotError):
            self.forms.save(self.case_id, self.actor, D.QUOTATION, {}, [], "")

    def test_unconfirmed_form_blocks_generation(self):
        self.save(confirm=False)
        with self.assertRaises(CrossBotError):
            self.docs.generate(self.case_id, self.actor, D.QUOTATION)

    def test_form_save_is_detached_and_retains_revision_audit(self):
        saved = self.save()
        form = get_form(saved, D.QUOTATION)
        form.values["document_no"] = "changed outside service"
        self.assertEqual(get_form(saved, D.QUOTATION).values["document_no"], "SYN-Q-1")
        updated = self.save(values={"document_no": "SYN-Q-2"}, confirm=False)
        current = get_form(updated, D.QUOTATION)
        self.assertEqual(current.revision, 2)
        self.assertIsNone(current.confirmed_by)
        self.assertEqual(updated.audit[-1].before["values"]["document_no"], "SYN-Q-1")
        self.assertEqual(get_form(self.repo.get(self.case_id), D.QUOTATION).revision, 2)

    def test_partial_form_can_generate_invalid_draft_without_filling_unknowns(self):
        self.save(values={}, rows=[])
        ws = self.docs.generate(self.case_id, self.actor, D.QUOTATION)
        artifact = ws.artifacts[-1]
        self.assertEqual(artifact.status, "invalid")
        complete = next(check for check in artifact.checks if check.rule_code == "FORM_COMPLETE")
        self.assertIs(complete.status, CrossCheckStatus.PENDING_REVIEW)
        text = self.docs.download(self.case_id, self.actor, artifact_id=artifact.artifact_id).decode()
        self.assertNotIn("CE certified", text)
        self.assertIn("<th>单据编号</th><td></td>", text)

    def test_generated_form_binds_snapshot_and_regeneration_preserves_old_bytes(self):
        ws = self.save()
        form = get_form(ws, D.QUOTATION)
        ws = self.docs.generate(self.case_id, self.actor, D.QUOTATION)
        first = ws.artifacts[-1]
        self.assertEqual(first.form_snapshot, to_dict(form))
        self.assertEqual(first.form_revision, 1)
        data = self.docs.download(self.case_id, self.actor, artifact_id=first.artifact_id)
        ws = self.docs.generate(self.case_id, self.actor, D.QUOTATION)
        self.assertEqual(ws.artifacts[-1].sha256, first.sha256)
        self.assertEqual(ws.artifacts[-1].version_number, 2)
        self.assertEqual(self.docs.download(self.case_id, self.actor, artifact_id=first.artifact_id), data)
        ws = self.save(values={"document_no": "SYN-Q-2"}, confirm=False)
        self.assertTrue(all(item.status == "stale" for item in ws.artifacts))

    def test_html_escapes_all_user_values_and_rows(self):
        value = '<script>alert("synthetic")</script>'
        self.save(values={"document_no": value}, rows=[{"description": value}])
        ws = self.docs.generate(self.case_id, self.actor, D.QUOTATION)
        text = self.docs.download(self.case_id, self.actor, artifact_id=ws.artifacts[-1].artifact_id).decode()
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)

    def test_original_invoice_includes_confirmed_bank_and_invoice_data_as_literal_text(self):
        self.save(kind=D.COMMERCIAL_INVOICE, values={"document_no": "SYN-CI-1",
            "document_date": "2026-10-31", "bank_account": "=SYNTHETIC()",
            "invoice_no": "+SYN-INV", "invoice_date": "2026-10-30"}, rows=[])
        ws = self.docs.generate(self.case_id, self.actor, D.COMMERCIAL_INVOICE)
        artifact = ws.artifacts[-1]
        self.assertTrue(artifact.filename.endswith(".xlsx"))
        data = self.docs.download(self.case_id, self.actor, artifact_id=artifact.artifact_id)
        book = load_workbook(BytesIO(data), data_only=False)
        self.assertIn("Form Details", book.sheetnames)
        cells = [cell for row in book["Form Details"] for cell in row]
        account = next(cell for cell in cells if cell.value == "=SYNTHETIC()")
        self.assertEqual(account.data_type, "s")
        self.assertIn("+SYN-INV", [cell.value for cell in cells])
        book.close()

    def test_conflicting_trade_and_invalid_row_math_cannot_be_valid(self):
        ws = self.save(values={"order_no": "ANOTHER-ORDER"},
            rows=[{"description": "item", "quantity": "2", "unit": "pcs", "unit_price": "3", "amount": "100"}])
        self.assertTrue(form_conflicts(ws.trade, get_form(ws, D.QUOTATION)))
        ws = self.docs.generate(self.case_id, self.actor, D.QUOTATION)
        self.assertEqual(ws.artifacts[-1].status, "invalid")
        consistency = next(c for c in ws.artifacts[-1].checks if c.rule_code == "FORM_CONSISTENCY")
        self.assertIs(consistency.status, CrossCheckStatus.FAIL)

    def test_incomplete_and_nonfinite_row_values_remain_unusable(self):
        ws = self.save(rows=[{"description": "item", "quantity": "NaN"}])
        form = get_form(ws, D.QUOTATION)
        self.assertIn("rows[1].unit", missing_form_fields(ws.trade, form))
        self.assertTrue(form_conflicts(ws.trade, form))

    def matching_rows(self, kind, trade=None):
        rows = []
        for line in (trade or self.trade).line_items:
            row = {name: str(getattr(line, name)) for name in ROW_FIELDS[kind]
                   if hasattr(line, name)}
            if "amount" in ROW_FIELDS[kind]:
                from decimal import Decimal
                row["amount"] = str(Decimal(line.quantity) * Decimal(line.unit_price))
            rows.append(row)
        return rows

    def assert_approval_blocked(self):
        with self.assertRaises(CrossBotError):
            ReviewService(self.repo, DeterministicRules()).approve(self.case_id,
                self.actor, "Synthetic independent review")

    def test_invoice_product_conflict_is_invalid_and_cannot_be_approved(self):
        for kind in (D.PROFORMA_INVOICE, D.COMMERCIAL_INVOICE, D.PACKING_LIST):
            for field, incorrect in (("description", "DIFFERENT PRODUCT"), ("quantity", "999"),
                ("unit_price", "4"), ("packages", "999"),
                ("gross_weight_kg", "999"), ("net_weight_kg", "1")):
                if field not in ROW_FIELDS[kind]:
                    continue
                with self.subTest(kind=kind, field=field):
                    rows = self.matching_rows(kind)
                    rows[0][field] = incorrect
                    self.save(kind=kind, rows=rows)
                    ws = self.docs.generate(self.case_id, self.actor, kind)
                    artifact = ws.artifacts[-1]
                    self.assertEqual(artifact.status, "invalid")
                    check = next(c for c in artifact.checks if c.rule_code == "FORM_CONSISTENCY")
                    self.assertIs(check.status, CrossCheckStatus.FAIL)
                    self.assertIn(field, check.message)
                    self.assert_approval_blocked()

    def test_invoice_rows_require_unique_complete_product_mapping(self):
        for rows in ([{"description": "Synthetic item", "quantity": "2", "unit": "pcs", "unit_price": "3"}],
            [{"product_code": "UNKNOWN", "description": "Synthetic item", "quantity": "2", "unit": "pcs", "unit_price": "3"}],
            self.matching_rows(D.COMMERCIAL_INVOICE) * 2):
            with self.subTest(rows=rows):
                ws = self.save(kind=D.COMMERCIAL_INVOICE, rows=rows)
                self.assertTrue(form_conflicts(ws.trade, get_form(ws, D.COMMERCIAL_INVOICE)))

    def test_matching_reordered_invoice_rows_are_valid(self):
        ws = self.repo.get(self.case_id)
        ws.trade.line_items.append(TradeLine("SYN-2", "Synthetic accessory", "4", "5",
            packages=2, gross_weight_kg="8", net_weight_kg="7"))
        self.repo.save(ws, expected_revision=ws.revision)
        for kind in (D.PROFORMA_INVOICE, D.COMMERCIAL_INVOICE, D.PACKING_LIST):
            rows = list(reversed(self.matching_rows(kind, ws.trade)))
            self.save(kind=kind, rows=rows)
            generated = self.docs.generate(self.case_id, self.actor, kind)
            self.assertEqual(generated.artifacts[-1].status, "draft")
        # Each form revision invalidates prior outputs, so rebuild all current drafts.
        for kind in (D.PROFORMA_INVOICE, D.COMMERCIAL_INVOICE, D.PACKING_LIST):
            self.docs.generate(self.case_id, self.actor, kind)
        approved = ReviewService(self.repo, DeterministicRules()).approve(self.case_id,
            self.actor, "Synthetic reordered rows reviewed")
        self.assertEqual(approved.case.status.value, "passed")

    def test_header_invalid_numbers_and_dates_block_approval(self):
        base = {"document_no": "SYN-PAY-1", "document_date": "2026-10-31", "currency": "USD",
            "amount_due": "3", "due_date": "2026-11-15", "bank_account": "synthetic"}
        for name, invalid in (("amount_due", "NaN"), ("amount_due", "Infinity"),
            ("amount_due", "-1"), ("amount_due", "7"), ("due_date", "not-a-date"),
            ("due_date", "2026-02-30"), ("due_date", "20261115"),
            ("document_date", "2026-1-01")):
            with self.subTest(name=name, value=invalid):
                values = {**base, name: invalid}
                self.save(kind=D.PAYMENT_NOTICE, values=values)
                ws = self.docs.generate(self.case_id, self.actor, D.PAYMENT_NOTICE)
                artifact = ws.artifacts[-1]
                self.assertEqual(artifact.status, "invalid")
                check = next(c for c in artifact.checks if c.rule_code == "FORM_CONSISTENCY")
                self.assertIn(name, check.message)
                self.assertIs(check.status, CrossCheckStatus.FAIL)
                self.assert_approval_blocked()

    def test_partial_payment_notice_amount_is_valid_and_approvable(self):
        self.save(kind=D.PAYMENT_NOTICE, values={"document_no": "SYN-PAY-1",
            "document_date": "2026-10-31", "currency": "USD", "amount_due": "3",
            "due_date": "2026-11-15", "bank_account": "synthetic"})
        generated = self.docs.generate(self.case_id, self.actor, D.PAYMENT_NOTICE)
        self.assertEqual(generated.artifacts[-1].status, "draft")
        approved = ReviewService(self.repo, DeterministicRules()).approve(self.case_id,
            self.actor, "Synthetic instalment request reviewed")
        self.assertEqual(approved.case.status.value, "passed")

    def test_all_header_date_and_amount_fields_are_checked_without_requiring_empty_values(self):
        for kind in SELLER_TYPES:
            for name in FORM_FIELDS[kind]:
                if name.endswith(("_date", "_until", "_on")) or name in {"period_start", "period_end"}:
                    with self.subTest(kind=kind, date_field=name):
                        form = DocumentForm(kind, values={name: "2026-02-30"})
                        self.assertTrue(any(name in err for err in form_conflicts(self.trade, form)))
                        form.values[name] = ""
                        self.assertFalse(any(name in err for err in form_conflicts(self.trade, form)))
        for name in ("opening_balance", "closing_balance"):
            form = DocumentForm(D.STATEMENT_OF_ACCOUNT, values={name: "NaN"})
            self.assertTrue(any(name in err for err in form_conflicts(self.trade, form)))
        form = DocumentForm(D.STATEMENT_OF_ACCOUNT, values={"period_start": "2026-11-01", "period_end": "2026-10-31"})
        self.assertTrue(any("period_end" in err for err in form_conflicts(self.trade, form)))


if __name__ == "__main__":
    unittest.main()
