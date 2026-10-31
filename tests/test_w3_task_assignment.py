"""Task routing must identify one existing colleague and complete one task."""
import unittest

from case_workflow.service import CaseService
from core.errors import CrossBotError
from core.models import WorkItem
from tests.test_w3_workflow import MemoryRepository, PendingRules, actor, trade


class Directory:
    def list_users(self):
        return [{"user_id": "docs-a", "roles": ["sales"]},
                {"user_id": "outsider", "roles": ["unknown"]}]


class TaskAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.service = CaseService(self.repo, PendingRules(), identity=Directory())
        self.sales = actor("seller", "sales")
        self.workspace = self.service.create(self.sales, trade(), required_types=[])
        self.case_id = self.workspace.case.case_id

    def test_handoff_routes_to_existing_colleague_without_finishing_unrelated_tasks(self):
        self.workspace.tasks.append(WorkItem(title="Independent work", role="sales", assignee_id=self.sales.user_id))
        self.repo.save(self.workspace, expected_revision=self.workspace.revision)
        old_id = self.workspace.tasks[0].task_id
        result = self.service.handoff(self.case_id, self.sales, "Review source", "sales", "docs-a")
        self.assertEqual(result.tasks[0].status, "pending")
        self.assertEqual(result.tasks[-1].assignee_id, "docs-a")
        result = self.service.handoff(self.case_id, self.sales, "Check source", "sales", "docs-a",
                                     source_task_id=old_id)
        self.assertEqual(result.tasks[0].status, "completed")
        self.assertEqual(result.tasks[1].status, "pending")

    def test_missing_or_wrong_role_assignee_is_rejected(self):
        for assignee in ("missing", "outsider"):
            with self.subTest(assignee=assignee), self.assertRaises(CrossBotError):
                self.service.handoff(self.case_id, self.sales, "Review source", "sales", assignee)

    def test_only_assignee_can_complete_a_personal_task(self):
        result = self.service.handoff(self.case_id, self.sales, "Review source", "sales", "docs-a")
        task_id = result.tasks[-1].task_id
        with self.assertRaises(CrossBotError):
            self.service.complete_task(self.case_id, actor("docs-b", "sales"), task_id, "Checked")
        result = self.service.complete_task(self.case_id, actor("docs-a", "sales"), task_id, "Checked")
        self.assertEqual(result.tasks[-1].status, "completed")
        self.assertEqual(result.audit[-1].action, "task_completed")


if __name__ == "__main__":
    unittest.main()
