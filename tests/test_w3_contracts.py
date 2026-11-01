import unittest
from core.models import (Actor, Case, Document, DocumentVersion, ExtractedField,
                         FieldValueStatus, TradeData, TradeLine, Workspace, to_dict,
                         TradeMilestone, CaseStatus, GeneratedArtifact)
from core.models import DocumentForm, DocumentType, ImportContext, SourceTable
from core.serialization import from_dict

class W3ContractTests(unittest.TestCase):
    def test_import_forms_and_source_tables_round_trip_without_human_approval(self):
        workspace = Workspace(Case(case_number="SYN-IMPORT"),
            document_forms=[DocumentForm(DocumentType.QUOTATION, values={"buyer": "Synthetic Buyer"})],
            import_context=ImportContext(archive_name="synthetic.zip", archive_sha256="a" * 64),
            documents=[Document(versions=[DocumentVersion(original_path="sources/order.xlsx",
                source_text="Synthetic source", source_tables=[SourceTable("Sheet1", [["Qty", "2"]])])])])
        restored = from_dict(Workspace, to_dict(workspace))
        self.assertEqual(restored, workspace)
        self.assertIsNone(restored.document_forms[0].confirmed_by)
        self.assertEqual(restored.approvals, [])
        self.assertEqual(restored.documents[0].versions[0].source_tables[0].rows[0][1], "2")

    def test_old_snapshot_defaults_new_import_fields(self):
        restored = from_dict(Workspace, {"case": {"case_number": "SYN-OLD"}})
        self.assertEqual(restored.document_forms, [])
        self.assertIsNone(restored.import_context)

    def test_snapshot_round_trip_preserves_nested_types_and_money(self):
        workspace = Workspace(Case(case_number="SYNTHETIC-1"),
            trade=TradeData(line_items=[TradeLine("DEMO", "Synthetic item", "2.5", "10.01")]),
            documents=[Document(versions=[DocumentVersion(fields=[ExtractedField(
                "quantity", value="2.50", confirmed_value="2.5", status=FieldValueStatus.CONFIRMED)])])],
            artifacts=[GeneratedArtifact(input_snapshot={"amount": "25.025"})])
        restored = from_dict(Workspace, to_dict(workspace))
        self.assertEqual(restored, workspace)
        self.assertIs(restored.case.status, CaseStatus.DRAFT)
        self.assertIs(restored.milestone, TradeMilestone.ORDER)
        self.assertEqual(restored.trade.line_items[0].unit_price, "10.01")

    def test_identities_default_to_unauthenticated_and_statuses_are_separate(self):
        self.assertFalse(Actor("person").authenticated)
        self.assertNotEqual(TradeMilestone.SHIPPED.value, CaseStatus.PASSED.value)

if __name__ == "__main__":
    unittest.main()

