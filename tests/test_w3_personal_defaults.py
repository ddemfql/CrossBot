"""Personal choice snapshots never copy transaction values or another user."""
from copy import deepcopy
import json
import unittest

from core.errors import CrossBotError, ValidationError
from core.models import (DocumentType as D, TradeData, UserWorkbenchDefaults, GeneratedArtifact,
                         to_dict)
from storage.preferences import SQLiteUserDefaultsStore
from case_workflow.service import CaseService
from case_workflow.preferences import UserDefaultsService
from documents.forms import (FormService, get_form, effective_values, input_values,
    form_fields, input_form_fields, form_context_fields, input_context_fields)
from documents.provenance import form_value_origins
from tests.test_w3_workflow import MemoryRepository, PendingRules, actor


class PersonalDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.store = SQLiteUserDefaultsStore(":memory:")
        self.defaults = UserDefaultsService(self.repo, self.store)
        self.cases = CaseService(self.repo, PendingRules(), defaults_store=self.store)
        self.a, self.b, self.admin = actor("synthetic-A", "sales"), actor("synthetic-B", "sales"), actor("synthetic-admin", "admin")
        self.ws = self.cases.create(self.a, TradeData(currency=""))

    def tearDown(self):
        self.store.close()

    def setup_profile(self):
        self.ws = self.cases.set_document_checklist(self.ws.case.case_id, self.a,
            [D.PURCHASE_ORDER], [D.COMMERCIAL_INVOICE])
        self.ws = FormService(self.repo).configure_fields(self.ws.case.case_id, self.a,
            D.COMMERCIAL_INVOICE, ["notes"], [], "Personal file choices", context_fields=[],
            required_input_fields=["bank_account"], required_input_context_fields=["transaction_place"])
        return self.defaults.save_from_case(self.ws.case.case_id, self.a, expected_revision=0)

    def test_three_actors_isolated_and_create_uses_only_creator_profile(self):
        profile = self.setup_profile()
        self.assertEqual(profile.revision, 1)
        new_a = self.cases.create(self.a, TradeData(currency=""))
        self.assertEqual(new_a.required_source_types, [D.PURCHASE_ORDER])
        self.assertEqual(new_a.required_delivery_types, [D.COMMERCIAL_INVOICE])
        self.assertEqual((new_a.defaults_user_id, new_a.defaults_revision), (self.a.user_id, 1))
        self.assertEqual(get_form(new_a, D.COMMERCIAL_INVOICE).required_input_fields, ["bank_account"])
        for other in (self.b, self.admin):
            self.assertEqual(self.defaults.get(other).revision, 0)
            created = self.cases.create(other, TradeData(currency=""))
            self.assertEqual(set(created.required_document_types), {D.COMMERCIAL_INVOICE, D.PACKING_LIST})
            self.assertFalse(get_form(created, D.COMMERCIAL_INVOICE).required_input_fields)
            self.assertFalse(created.defaults_user_id)

    def test_profile_contains_choices_only_not_business_or_source_values(self):
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.a, D.COMMERCIAL_INVOICE,
            {"buyer": "CONFIDENTIAL BUYER VALUE", "notes": "CONFIDENTIAL TRANSACTION NOTE"},
            [{"product_code": "CONFIDENTIALSKU", "description": "CONFIDENTIAL ITEM", "quantity": "2"}],
            "Synthetic source record")
        profile = self.defaults.save_from_case(self.ws.case.case_id, self.a)
        payload = json.dumps(to_dict(profile), ensure_ascii=False)
        self.assertNotIn("CONFIDENTIAL", payload)
        self.assertNotIn("case_id", payload)
        self.assertNotIn("source_document_ids", payload)
        self.assertNotIn("confirmed_by", payload)
        self.assertEqual(len(self.store.history(self.a.user_id)), 1)

    def test_old_and_shared_cases_do_not_change_until_explicit_application(self):
        old = self.cases.create(self.a, TradeData(order_no="OLD-SYN", currency=""))
        original = to_dict(self.repo.get(old.case.case_id))
        self.setup_profile()
        self.assertEqual(to_dict(self.repo.get(old.case.case_id)), original)
        self.assertEqual(to_dict(self.cases.get(old.case.case_id, self.b)), original)
        changed = self.defaults.apply_to_case(old.case.case_id, self.a, expected_workspace_revision=old.revision)
        self.assertEqual(changed.required_delivery_types, [D.COMMERCIAL_INVOICE])
        self.assertEqual(changed.trade.order_no, "OLD-SYN")
        self.assertEqual(changed.defaults_user_id, self.a.user_id)
        self.assertTrue(any(event.action == "personal_defaults_applied" for event in changed.audit))

    def test_applying_preferences_preserves_values_and_marks_outputs_stale(self):
        self.setup_profile()
        case = self.cases.create(self.b, TradeData(currency=""))
        case = FormService(self.repo).save(case.case.case_id, self.b, D.COMMERCIAL_INVOICE,
            {"buyer": "Preserved buyer", "bank_account": "Known account"}, [], "Human typed source")
        case.artifacts.append(GeneratedArtifact(document_type=D.COMMERCIAL_INVOICE, status="draft"))
        self.repo.save(case, expected_revision=case.revision)
        before = deepcopy(get_form(case, D.COMMERCIAL_INVOICE).values)
        changed = self.defaults.apply_to_case(case.case.case_id, self.a)
        self.assertEqual(get_form(changed, D.COMMERCIAL_INVOICE).values, before)
        self.assertEqual(changed.artifacts[0].status, "stale")
        self.assertFalse(changed.approvals)

    def test_explicit_case_file_list_overrides_profile_but_matching_choices_apply(self):
        self.setup_profile()
        case = self.cases.create(self.a, TradeData(currency=""), required_types=[D.COMMERCIAL_INVOICE, D.PACKING_LIST])
        self.assertEqual(case.required_document_types, [D.COMMERCIAL_INVOICE, D.PACKING_LIST])
        self.assertEqual(get_form(case, D.COMMERCIAL_INVOICE).required_input_fields, ["bank_account"])
        self.assertNotIn(D.PURCHASE_ORDER, case.required_document_types)

    def test_compare_and_swap_for_both_profile_and_workspace(self):
        profile = self.setup_profile()
        stale = deepcopy(profile)
        saved = deepcopy(profile); saved.source_types = []
        self.store.save(saved, expected_revision=1)
        self.assertEqual(saved.revision, 2)
        with self.assertRaises(CrossBotError):
            self.store.save(stale, expected_revision=1)
        with self.assertRaises(CrossBotError):
            self.defaults.save_from_case(self.ws.case.case_id, self.a, expected_revision=1)
        with self.assertRaises(CrossBotError):
            self.defaults.apply_to_case(self.ws.case.case_id, self.a, expected_workspace_revision=0)
        self.assertEqual([item.revision for item in self.store.history(self.a.user_id)], [1, 2])

    def test_hidden_required_input_is_retained_but_not_rendered_and_has_real_origin(self):
        self.setup_profile()
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.a, D.COMMERCIAL_INVOICE,
            {"bank_account": "KNOWN-ACCOUNT", "buyer": "Buyer"}, [], "Human supplies private input")
        form = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.assertNotIn("bank_account", form_fields(form))
        self.assertIn("bank_account", input_form_fields(form))
        self.assertNotIn("bank_account", effective_values(self.ws.trade, form))
        self.assertEqual(input_values(self.ws.trade, form)["bank_account"], "KNOWN-ACCOUNT")
        self.assertNotIn("transaction_place", form_context_fields(form))
        self.assertIn("transaction_place", input_context_fields(form))
        self.assertFalse(any(origin.field_name == "bank_account" for origin in form_value_origins(self.ws, D.COMMERCIAL_INVOICE)))
        origin = next(origin for origin in form_value_origins(self.ws, D.COMMERCIAL_INVOICE, include_inputs=True)
                      if origin.field_name == "bank_account")
        self.assertEqual((origin.origin_kind, origin.actor_id), ("manual", self.a.user_id))
        self.ws = FormService(self.repo).save(self.ws.case.case_id, self.a, D.COMMERCIAL_INVOICE,
            {"buyer": "Edited buyer"}, [], "Edit only public input")
        self.assertEqual(get_form(self.ws, D.COMMERCIAL_INVOICE).values["bank_account"], "KNOWN-ACCOUNT")

    def test_unauthenticated_actors_and_unsupported_choices_rejected(self):
        with self.assertRaises(CrossBotError):
            self.defaults.get(actor("invalid", "unknown"))
        self.ws.document_forms[0].required_input_fields = ["unsupported_business_field"]
        self.repo.save(self.ws, expected_revision=self.ws.revision)
        with self.assertRaises(ValidationError):
            self.defaults.save_from_case(self.ws.case.case_id, self.a)
        self.assertIsNone(self.store.get(self.a.user_id))

    def test_conditional_input_requirements_do_not_force_external_output_fields(self):
        self.ws = self.cases.create(self.a, TradeData(destination_country="US", transport_mode="sea", currency=""))
        self.ws = FormService(self.repo).configure_fields(self.ws.case.case_id, self.a,
            D.COMMERCIAL_INVOICE, [], [], "Keep private checks separate from output", context_fields=[])
        form = get_form(self.ws, D.COMMERCIAL_INVOICE)
        self.assertEqual(form_context_fields(form, self.ws), set())
        self.assertTrue({"transaction_date", "port_of_entry", "line_items", "packages"}
                        <= input_context_fields(form, self.ws))
        self.assertEqual(form.required_input_context_fields, [])
        profile = self.defaults.save_from_case(self.ws.case.case_id, self.a)
        ci = next(document for document in profile.documents if document.document_type is D.COMMERCIAL_INVOICE)
        self.assertEqual(ci.included_context_fields, [])
        self.assertEqual(ci.required_input_context_fields, [])
