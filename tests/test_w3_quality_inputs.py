"""Grouped source review is explicit, scoped, version-bound and atomic."""
from copy import deepcopy
from io import BytesIO
import unittest

from openpyxl import Workbook
from core.errors import CrossBotError
from core.models import (Actor, CaseStatus, DocumentStatus, DocumentType as D, ExtractedField,
    FieldValueStatus as F, ReviewTask, ReviewStatus, SourceFieldSelection, SourceTable, to_dict)
from review.quality_inputs import confirm_source_fields, pending_source_fields
from validation.export_review import review_digest
from tests import test_w3_strict_review_integration as strict_harness
from tests import test_w3_simple_ui as ui_harness


def add_raw_source(repository, documents, case_id, actor, quantity="4"):
    table = SourceTable("Source", [["Product Code", "Description", "Quantity", "Unit"],
                                   ["SYN-A", "Synthetic product", quantity, "pcs"]])
    book = Workbook()
    book.active.title = table.name
    for row in table.rows:
        book.active.append(row)
    output = BytesIO()
    book.save(output)
    book.close()
    workspace = documents.upload(case_id, actor, "synthetic-counterpart.xlsx", output.getvalue(), D.PACKING_LIST)
    document = workspace.documents[-1]
    version = document.versions[-1]
    document.status = version.status = DocumentStatus.PARSED
    version.source_tables = [table]
    version.fields = [ExtractedField(name, value, status=F.CANDIDATE, sheet_name="Source", cell_reference=cell)
        for name, value, cell in (("product_code", "SYN-A", "A2"), ("quantity", quantity, "C2"),
                                  ("unit", "pcs", "D2"))]
    workspace.review_tasks.append(ReviewTask(case_id=case_id, document_id=document.document_id,
        field_names=[field.name for field in version.fields]))
    repository.save(workspace, expected_revision=workspace.revision)
    return workspace, document.document_id


class QualityInputsTests(unittest.TestCase):
    setUp = strict_harness.StrictReviewIntegrationTests.setUp
    tearDown = strict_harness.StrictReviewIntegrationTests.tearDown
    service = strict_harness.StrictReviewIntegrationTests.service
    save = strict_harness.StrictReviewIntegrationTests.save

    def prepare(self):
        self.save()
        workspace, document_id = add_raw_source(self.repo, self.documents, self.case_id, self.actor)
        return workspace, document_id, pending_source_fields(workspace, D.PROFORMA_INVOICE)

    def confirm(self, fields, digest, actor=None, ack=True):
        return confirm_source_fields(self.repo, self.case_id, actor or self.actor, D.PROFORMA_INVOICE,
            fields, expected_review_digest=digest, human_confirmed=ack)

    def test_explicit_current_source_review_unlocks_checks_without_changing_machine_values(self):
        workspace, document_id, fields = self.prepare()
        self.assertEqual({field.field_name for field in fields}, {"product_code", "quantity", "unit"})
        before = self.service().assess(self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=True)
        self.assertFalse(before.ready)
        result = self.confirm(fields, before.input_digest)
        current = next(doc for doc in result.documents if doc.document_id == document_id).versions[-1]
        self.assertTrue(all(field.status is F.CONFIRMED for field in current.fields))
        self.assertEqual(next(field.value for field in current.fields if field.name == "quantity"), "4")
        self.assertTrue(all(field.confirmed_by == self.actor.user_id and field.confirmed_at for field in current.fields))
        self.assertFalse(result.approvals)
        self.assertNotEqual(result.case.status, CaseStatus.PASSED)
        self.assertEqual(result.review_tasks[-1].status, ReviewStatus.APPROVED)
        event = next(event for event in result.audit if event.action == "external_fields_reviewed")
        self.assertEqual(event.actor_id, self.actor.user_id)
        self.assertEqual(event.after["reviewed_input_digest"], before.input_digest)
        self.assertTrue(all(change["before"]["status"] == "candidate"
                            and change["after"]["status"] == "confirmed" for change in event.after["fields"]))
        self.assertTrue(self.service().assess(self.case_id, self.actor, D.PROFORMA_INVOICE,
                                             human_confirmed=True).ready)

    def test_human_corrected_conflicting_value_preserves_machine_candidate_and_stays_blocked(self):
        workspace, document_id, fields = self.prepare()
        fields = [SourceFieldSelection(item.document_id, item.version_id, item.field_name,
            "9" if item.field_name == "quantity" else item.value) for item in fields]
        result = self.confirm(fields, review_digest(workspace, D.PROFORMA_INVOICE))
        quantity = next(field for doc in result.documents if doc.document_id == document_id
                        for field in doc.versions[-1].fields if field.name == "quantity")
        self.assertEqual((quantity.value, quantity.confirmed_value), ("4", "9"))
        self.assertFalse(self.service().assess(self.case_id, self.actor, D.PROFORMA_INVOICE,
                                              human_confirmed=True).ready)
        with self.assertRaises(CrossBotError):
            self.service().generate(self.case_id, self.actor, D.PROFORMA_INVOICE, human_confirmed=True)

    def test_invalid_ack_actor_digest_or_selections_leave_every_field_unchanged(self):
        workspace, _, fields = self.prepare()
        digest = review_digest(workspace, D.PROFORMA_INVOICE)
        invalid_fields = [[], [*fields, fields[0]],
            [fields[0], SourceFieldSelection(fields[1].document_id, "old-version", "quantity", "4")],
            [fields[0], SourceFieldSelection(fields[1].document_id, fields[1].version_id, "unrelated-field", "x")],
            [fields[0], SourceFieldSelection("other-case-document", fields[1].version_id, "quantity", "4")],
            [fields[0], SourceFieldSelection(fields[1].document_id, fields[1].version_id, "quantity", " ")]]
        for selections in invalid_fields:
            with self.subTest(fields=selections):
                before = to_dict(self.repo.get(self.case_id))
                with self.assertRaises(CrossBotError):
                    self.confirm(selections, digest)
                self.assertEqual(to_dict(self.repo.get(self.case_id)), before)
        for ack, actor, expected in ((False, self.actor, digest),
            (True, Actor("synthetic-ai", ["admin"], authenticated=True, kind="ai"), digest),
            (True, Actor("synthetic-anon", ["admin"], authenticated=False), digest),
            (True, self.actor, "stale-digest")):
            with self.subTest(ack=ack, actor=actor.user_id):
                before = to_dict(self.repo.get(self.case_id))
                with self.assertRaises(CrossBotError):
                    self.confirm(fields, expected, actor=actor, ack=ack)
                self.assertEqual(to_dict(self.repo.get(self.case_id)), before)

    def test_confirmed_values_can_be_corrected_again_but_previous_versions_are_rejected(self):
        workspace, document_id, fields = self.prepare()
        digest = review_digest(workspace, D.PROFORMA_INVOICE)
        self.confirm(fields, digest)
        current = self.repo.get(self.case_id)
        correction = [SourceFieldSelection(item.document_id, item.version_id, item.field_name,
            "9" if item.field_name == "quantity" else item.value) for item in fields]
        current = self.confirm(correction, review_digest(current, D.PROFORMA_INVOICE))
        quantity = next(field for doc in current.documents if doc.document_id == document_id
                        for field in doc.versions[-1].fields if field.name == "quantity")
        self.assertEqual((quantity.value, quantity.confirmed_value), ("4", "9"))
        self.assertFalse(self.service().assess(self.case_id, self.actor, D.PROFORMA_INVOICE,
                                              human_confirmed=True).ready)
        current = self.documents.upload(self.case_id, self.actor, "new-source.txt", b"Synthetic revised source",
                                        D.PACKING_LIST, document_id=document_id)
        before = to_dict(current)
        with self.assertRaises(CrossBotError):
            self.confirm(fields, review_digest(current, D.PROFORMA_INVOICE))
        self.assertEqual(to_dict(self.repo.get(self.case_id)), before)


class QualityInputUITests(unittest.TestCase):
    setUp = ui_harness.SimpleUITests.setUp
    tearDown = ui_harness.SimpleUITests.tearDown
    app = ui_harness.SimpleUITests.app

    def test_grouped_external_review_requires_its_own_explicit_acknowledgement(self):
        from case_workflow.service import CaseService
        from core.models import TradeData
        from documents.forms import FormService
        from documents.service import DocumentService
        from validation.rules import DeterministicRules
        case_id = CaseService(self.repo, DeterministicRules()).create(self.actor, TradeData()).case.case_id
        FormService(self.repo).save(case_id, self.actor, D.PROFORMA_INVOICE,
            strict_harness.complete_values(D.PROFORMA_INVOICE),
            [strict_harness.complete_row(D.PROFORMA_INVOICE)], "Synthetic manual PI")
        add_raw_source(self.repo, DocumentService(self.repo, self.blobs), case_id, self.actor)
        app = self.app()
        ui_harness.widget(app.selectbox, "需要生成的文件").select(D.PROFORMA_INVOICE).run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        self.assertEqual([tab.label for tab in app.tabs], ["文件分类与查看", "生成与导出"])
        self.assertTrue(ui_harness.widget(app.button, "确认外部字段并重新审核").disabled)
        ui_harness.widget(app.checkbox, "我已逐项核对上表所选字段的原件和确认值").check().run()
        self.assertFalse(ui_harness.widget(app.button, "确认外部字段并重新审核").disabled)
        ui_harness.widget(app.button, "确认外部字段并重新审核").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        result = self.repo.get(case_id)
        self.assertTrue(all(field.status is F.CONFIRMED for doc in result.documents
                            for field in doc.versions[-1].fields))
        self.assertFalse(result.approvals)
        self.assertTrue(ui_harness.widget(app.button, "生成 Excel").disabled)


if __name__ == "__main__":
    unittest.main()
