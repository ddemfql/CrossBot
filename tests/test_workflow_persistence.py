import tempfile
import unittest
from pathlib import Path

from auth import AuthService
from auth.cases import CaseAccessService
from core.identity import Role
from core.models import Case
from storage.sqlite_store import SQLiteStore
from workflows.persistence import bind_case_state, persist_case_workflow


class WorkflowPersistenceTests(unittest.TestCase):
    def test_case_isolation_and_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteStore(Path(folder) / "case.db")
            auth = AuthService(store)
            auth.bootstrap_admin("owner", "secure-password-123")
            token = auth.login("owner", "secure-password-123")
            access = CaseAccessService(auth)
            cases = [Case(case_number=f"C-{i}", customer_name="Demo", destination_country="DE") for i in range(2)]
            for case in cases:
                access.create_case(token, case)
            state = {"sales_inquiry": "First inquiry", "sales_product": "Lamp", "sales_language": "English"}
            self.assertTrue(persist_case_workflow(store, access, token, state, cases[0].case_id, "sales"))
            next_state = {}
            bind_case_state(access, token, next_state, cases[0].case_id, "sales")
            self.assertEqual(next_state["sales_inquiry"], "First inquiry")
            bind_case_state(access, token, next_state, cases[1].case_id, "sales")
            self.assertNotIn("sales_inquiry", next_state)

    def test_legacy_history_is_not_treated_as_new_workflow_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteStore(Path(folder) / "case.db")
            auth = AuthService(store)
            auth.bootstrap_admin("owner", "secure-password-123")
            token = auth.login("owner", "secure-password-123")
            access = CaseAccessService(auth)
            case = Case(case_number="C-LEGACY", customer_name="Demo", destination_country="DE")
            access.create_case(token, case)
            from core.schemas import WorkflowConversation
            store.save_conversation(WorkflowConversation(case.case_id, "sales", [{"role": "user", "content": "old"}]))
            state = {}
            bind_case_state(access, token, state, case.case_id, "sales")
            self.assertEqual(state["legacy_sales_messages"][0]["content"], "old")
            self.assertNotIn("sales_conversation", state)


if __name__ == "__main__":
    unittest.main()
