import unittest
from core.models import (AuditEvent, Case, Document, DocumentStatus, DocumentType as Type,
    DocumentVersion, ExtractedField, FieldValueStatus, GeneratedArtifact, TradeData,
    TradeLine, Workspace, CrossCheckResult, CrossCheckStatus as Status, to_dict, utc_now)
from review.policy import checks_digest
from validation.rules import DeterministicRules, trade_values, validate_trade


def synthetic_workspace():
    trade = TradeData(order_no="SYNTHETIC-001", seller="Synthetic Seller", buyer="Synthetic Buyer",
        destination_country="DE", currency="USD", incoterm="FCA Test Port", payment_terms="Net 30",
        transport_mode="sea", line_items=[TradeLine("DEMO-1", "Synthetic lamp", "10", "5.25",
            packages=2, gross_weight_kg="12", net_weight_kg="10")], confirmed_by="sales-human",
        confirmed_at=utc_now())
    return Workspace(Case(order_no=trade.order_no, customer_name=trade.buyer, destination_country="DE"),
                     trade=trade, required_document_types=[Type.COMMERCIAL_INVOICE, Type.PACKING_LIST])


def upload(workspace, kind):
    fields = [ExtractedField(name, value=value, confirmed_value=value, confirmed_by="reviewer-human",
        status=FieldValueStatus.CONFIRMED, page_number=1) for name, value in trade_values(workspace.trade).items()]
    document = Document(document_type=kind)
    version = DocumentVersion(document_id=document.document_id, fields=fields, status=DocumentStatus.PARSED)
    document.current_version_id = version.version_id
    document.versions.append(version)
    workspace.documents.append(document)
    return version


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.rules = DeterministicRules()

    def test_all_confirmed_matching_documents_pass_with_version_evidence(self):
        workspace = synthetic_workspace()
        left = upload(workspace, Type.COMMERCIAL_INVOICE)
        right = upload(workspace, Type.PACKING_LIST)
        checks = self.rules.evaluate(workspace)
        self.assertTrue(checks)
        self.assertTrue(all(c.status == Status.PASS for c in checks))
        quantity = next(c for c in checks if c.rule_code == "CI_PL_QUANTITY")
        self.assertEqual((quantity.left_version_id, quantity.right_version_id), (left.version_id, right.version_id))
        self.assertIn("page 1", quantity.left_source)

    def test_conflict_missing_candidate_and_nonfinite_have_distinct_results(self):
        workspace = synthetic_workspace()
        upload(workspace, Type.COMMERCIAL_INVOICE)
        version = upload(workspace, Type.PACKING_LIST)
        quantity = next(f for f in version.fields if f.name == "quantity")
        quantity.confirmed_value = "11"
        checks = self.rules.evaluate(workspace)
        self.assertEqual(next(c.status for c in checks if c.rule_code == "CI_PL_QUANTITY"), Status.FAIL)
        for bad in (None, "NaN", "Infinity", "eleven"):
            quantity.confirmed_value = bad
            checks = self.rules.evaluate(workspace)
            self.assertEqual(next(c.status for c in checks if c.rule_code == "CI_PL_QUANTITY"), Status.PENDING_REVIEW)
        quantity.confirmed_value = "10"
        quantity.status = FieldValueStatus.CANDIDATE
        checks = self.rules.evaluate(workspace)
        self.assertEqual(next(c.status for c in checks if c.rule_code == "CI_PL_QUANTITY"), Status.PENDING_REVIEW)

    def test_empty_master_and_missing_documents_cannot_pass(self):
        checks = self.rules.evaluate(Workspace(Case(), required_document_types=[Type.COMMERCIAL_INVOICE]))
        self.assertTrue(any(c.status == Status.PENDING_REVIEW for c in checks))
        workspace = synthetic_workspace()
        workspace.trade.line_items[0].quantity = "NaN"
        workspace.trade.line_items[0].gross_weight_kg = "1"
        checks = self.rules.evaluate(workspace)
        self.assertEqual(next(c.status for c in checks if c.rule_code == "LINE_1_QUANTITY"), Status.PENDING_REVIEW)
        self.assertEqual(next(c.status for c in checks if c.rule_code == "LINE_1_WEIGHT_ORDER"), Status.FAIL)

    def test_trade_validation_does_not_require_artifacts_to_exist(self):
        checks = validate_trade(synthetic_workspace())
        self.assertTrue(checks)
        self.assertTrue(all(c.status == Status.PASS for c in checks))
        self.assertFalse(any(c.rule_code.startswith("REQUIRED_DOCUMENT") for c in checks))

    def test_rule_evidence_is_stable_across_non_trade_workspace_revisions(self):
        workspace = synthetic_workspace()
        original = self.rules.evaluate(workspace)
        workspace.revision += 5
        workspace.audit.append(AuditEvent(case_id=workspace.case.case_id,
                                          action="synthetic unrelated action"))
        after = self.rules.evaluate(workspace)
        self.assertEqual(checks_digest(original), checks_digest(after))

    def test_unsupported_attachment_is_custody_only_and_does_not_block_rules(self):
        workspace = synthetic_workspace()
        upload(workspace, Type.COMMERCIAL_INVOICE)
        upload(workspace, Type.PACKING_LIST)
        document = Document(document_type=Type.PHOTO)
        version = DocumentVersion(document_id=document.document_id)
        document.current_version_id = version.version_id
        document.versions.append(version)
        workspace.documents.append(document)
        self.assertTrue(all(c.status == Status.PASS for c in self.rules.evaluate(workspace)))

    def test_unparsed_optional_external_document_is_custody_only(self):
        workspace = synthetic_workspace()
        bill = Document(document_type=Type.BILL_OF_LADING, status=DocumentStatus.UPLOADED)
        version = DocumentVersion(document_id=bill.document_id, status=DocumentStatus.UPLOADED)
        bill.versions.append(version)
        bill.current_version_id = version.version_id
        workspace.documents.append(bill)
        source_versions = {bill.document_id: version.version_id}
        for kind in workspace.required_document_types:
            workspace.artifacts.append(GeneratedArtifact(document_type=kind,
                input_revision=workspace.trade.revision, input_snapshot=to_dict(workspace.trade),
                source_versions=source_versions, storage_path="synthetic-key", sha256="synthetic-hash",
                template_sha256="synthetic-template", checks=[CrossCheckResult(status=Status.PASS)]))
        self.assertTrue(all(c.status == Status.PASS for c in self.rules.evaluate(workspace)))

    def test_latest_generated_artifact_must_match_snapshot_and_current_sources(self):
        workspace = synthetic_workspace()
        for kind in workspace.required_document_types:
            workspace.artifacts.append(GeneratedArtifact(document_type=kind, input_revision=workspace.trade.revision,
                input_snapshot=to_dict(workspace.trade), storage_path="synthetic-key", sha256="synthetic-hash",
                template_sha256="synthetic-template", checks=[CrossCheckResult(status=Status.PASS)]))
        self.assertTrue(all(c.status == Status.PASS for c in self.rules.evaluate(workspace)))
        workspace.artifacts[-1].status = "stale"
        self.assertTrue(any(c.status == Status.PENDING_REVIEW for c in self.rules.evaluate(workspace)))
        workspace.artifacts[-1].status = "draft"
        workspace.artifacts[-1].input_snapshot["buyer"] = "Outdated Buyer"
        self.assertTrue(any(c.status == Status.PENDING_REVIEW for c in self.rules.evaluate(workspace)))

    def test_parse_failure_and_new_upload_never_reuse_old_review(self):
        workspace = synthetic_workspace()
        upload(workspace, Type.COMMERCIAL_INVOICE)
        previous = upload(workspace, Type.PACKING_LIST)
        document = workspace.documents[-1]
        failed = DocumentVersion(document_id=document.document_id, status=DocumentStatus.PARSE_FAILED,
                                 parse_error="Synthetic corrupt workbook")
        document.versions.append(failed)
        document.current_version_id = failed.version_id
        checks = self.rules.evaluate(workspace)
        required = next(c for c in checks if c.rule_code == "REQUIRED_DOCUMENT_PACKING_LIST")
        self.assertEqual(required.status, Status.PENDING_REVIEW)
        self.assertFalse(any(c.rule_code == "CI_PL_QUANTITY" for c in checks))
        self.assertNotEqual(failed.version_id, previous.version_id)

    def test_contract_and_transport_evidence_are_checked_when_provided(self):
        workspace = synthetic_workspace()
        upload(workspace, Type.COMMERCIAL_INVOICE)
        upload(workspace, Type.PACKING_LIST)
        contract = upload(workspace, Type.SALES_CONTRACT)
        bill = upload(workspace, Type.BILL_OF_LADING)
        next(f for f in contract.fields if f.name == "currency").confirmed_value = "EUR"
        next(f for f in bill.fields if f.name == "consignee").confirmed_value = "Other synthetic buyer"
        checks = self.rules.evaluate(workspace)
        self.assertEqual(next(c.status for c in checks if c.rule_code == "CI_CONTRACT_CURRENCY"), Status.FAIL)
        self.assertEqual(next(c.status for c in checks if c.rule_code == "PL_BL_CONSIGNEE"), Status.FAIL)

    def test_source_version_changes_and_empty_artifact_checks_require_review(self):
        workspace = synthetic_workspace()
        for kind in workspace.required_document_types:
            workspace.artifacts.append(GeneratedArtifact(document_type=kind, input_revision=workspace.trade.revision,
                input_snapshot=to_dict(workspace.trade), storage_path="synthetic-key", sha256="synthetic-hash",
                template_sha256="synthetic-template", checks=[CrossCheckResult(status=Status.PASS)]))
        attachment = Document(document_type=Type.PHOTO)
        version = DocumentVersion(document_id=attachment.document_id)
        attachment.versions.append(version)
        attachment.current_version_id = version.version_id
        workspace.documents.append(attachment)
        self.assertTrue(any(c.status == Status.PENDING_REVIEW for c in self.rules.evaluate(workspace)))
        for artifact in workspace.artifacts:
            artifact.source_versions = {attachment.document_id: version.version_id}
        self.assertTrue(all(c.status == Status.PASS for c in self.rules.evaluate(workspace)))
        workspace.artifacts[-1].checks.clear()
        self.assertTrue(any(c.status == Status.PENDING_REVIEW for c in self.rules.evaluate(workspace)))

if __name__ == "__main__":
    unittest.main()
