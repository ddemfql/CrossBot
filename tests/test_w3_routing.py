"""Synthetic external-source queues: ownership, idempotence and import wiring."""
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from case_workflow.importer import ArchiveImporter
from case_workflow.routing import ensure_source_tasks, source_task_id
from core.models import (Actor, Case, CaseStatus, Document, DocumentType as Type,
                         TradeMilestone, Workspace)
from storage.blobs import LocalBlobStore
from storage.sqlite import SQLiteWorkspaceRepository


class SourceRoutingTests(unittest.TestCase):
    def test_all_external_types_enter_business_user_queue_once(self):
        kinds = [Type.PURCHASE_ORDER, Type.PURCHASE_CONTRACT,
                 Type.BILL_OF_LADING, Type.AIR_WAYBILL,
                 Type.FORWARDER_RECEIPT, Type.INSURANCE_POLICY,
                 Type.ISF_RECEIPT, Type.BANK_RECEIPT,
                 Type.TEST_REPORT, Type.COMPLIANCE, Type.OTHER]
        ws = Workspace(Case(), documents=[Document(document_type=kind,
            title=f"synthetic-{index}.pdf", responsibility="external")
            for index, kind in enumerate(kinds)])
        added = ensure_source_tasks(ws, Actor("synthetic-admin", ["admin"], authenticated=True))
        self.assertEqual([task.role for task in added], ["sales"] * len(kinds))
        for document, task in zip(ws.documents, added):
            self.assertIsNone(task.assignee_id)
            self.assertEqual(task.status, "pending")
            self.assertEqual(task.task_id, source_task_id(document.document_id))
            self.assertIn(document.document_id, task.note)
            self.assertIn(document.title, task.note)
            self.assertIn("核对来源方", task.note)
        added[0].status = "completed"
        self.assertFalse(ensure_source_tasks(ws))
        self.assertEqual(added[0].status, "completed")
        self.assertEqual(len(ws.tasks), len(kinds))
        self.assertEqual(ws.case.status, CaseStatus.DRAFT)
        self.assertEqual(ws.milestone, TradeMilestone.ORDER)
        self.assertFalse(ws.approvals)
        self.assertFalse(ws.payments)

    def test_deleted_sources_and_seller_files_skip_but_external_templates_queue(self):
        ws = Workspace(Case(), documents=[
            Document(document_type=Type.COMMERCIAL_INVOICE, responsibility="seller"),
            Document(document_type=Type.PURCHASE_ORDER, responsibility="external",
                     deleted_at=datetime.now(timezone.utc)),
            Document(document_type=Type.COMPLIANCE_DRAFT, responsibility="external"),
            Document(document_type=Type.BANK_RECEIPT),
        ])
        added = ensure_source_tasks(ws)
        self.assertEqual([task.role for task in added], ["sales", "sales"])
        self.assertEqual([task.task_id for task in added],
                         [source_task_id(doc.document_id) for doc in ws.documents[2:]])

    def test_archive_import_queues_external_records_without_approving(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository = SQLiteWorkspaceRepository(Path(temporary) / "workspace.db")
            try:
                stream = BytesIO()
                with ZipFile(stream, "w") as archive:
                    archive.writestr("buyer/PO-SYN-1.txt", b"Order No: SYN-1")
                    archive.writestr("Bank Receipt.txt", b"Bank Reference: SYN-PAY-1")
                actor = Actor("synthetic-admin", ["admin"], authenticated=True)
                ws = ArchiveImporter(repository, LocalBlobStore(Path(temporary) / "blobs")).import_zip(
                    stream.getvalue(), "synthetic.zip", actor)
                queued = [task for task in repository.get(ws.case.case_id).tasks
                          if task.task_id.startswith("source_")]
                self.assertEqual([task.role for task in queued], ["sales", "sales"])
                self.assertTrue(all(task.status == "pending" and task.assignee_id is None for task in queued))
                self.assertEqual(ws.case.status, CaseStatus.DRAFT)
                self.assertFalse(ws.trade.confirmed_by)
                self.assertFalse(ws.approvals)
            finally:
                repository.close()


if __name__ == "__main__":
    unittest.main()

