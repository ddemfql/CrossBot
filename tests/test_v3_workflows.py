"""V3 regressions: exact fixture, candidate provenance, advisory checks, isolated simulation."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from core.errors import CrossBotError
from core.models import Case, ExtractedField, FieldSource, FieldValueStatus
from core.schemas import ListingRequest, SalesRequest
from knowledge.retrieval import retrieve_context
from providers.customer_mock import MockCustomerClient
from workflows.content_review import review_content
from workflows.customer_simulator import simulate_customer
from workflows.demo_case import FIXTURE, demo_case, demo_listing_request, demo_sales_request, load_fixture
from workflows.animal_lamp_demo import FIXTURE as ANIMAL_FIXTURE, animal_lamp_case, animal_listing_request, animal_sales_request, load_animal_fixture
from workflows.inquiry_analysis import analyze_inquiry, extract_inquiry_candidates
from providers.inquiry_mock import MockInquiryClient
from workflows.sales import build_sales_messages, generate_sales
from workflows.listing import generate_listings


class V3Workflows(unittest.TestCase):
    def test_exact_demo_amounts_units_and_no_fabricated_customer(self):
        d = load_fixture()
        case = demo_case()
        self.assertIsInstance(case, Case)
        self.assertEqual(case.case_id, "CASE-DE-LAMP-001")
        self.assertEqual(case.invoice_total, Decimal("9250.00"))
        self.assertEqual(case.customer_name, "")
        self.assertIsNone(case.order_no)
        self.assertEqual(d["cartons"] * d["units_per_carton"], 500)
        self.assertEqual(d["color_temperature_modes"], 3)
        w1 = demo_sales_request()
        self.assertEqual(w1.requested_quantity, 500)
        self.assertEqual(w1.customer_country, "Germany")
        self.assertIn("18.50 EUR", w1.product_info)
        w2 = demo_listing_request()
        self.assertNotIn("18.50", w2.product_description)  # Case price is not product master data.
        self.assertNotIn("白色", w2.product_description)
        self.assertEqual(w2.platforms, ["Amazon", "TikTok"])

    def test_bad_demo_arithmetic_refuses_to_load(self):
        original = json.loads(FIXTURE.read_text(encoding="utf-8"))
        for field, value in [("invoice_total", "9249.99"), ("packages", 24), ("gross_weight_kg", "140"),
                             ("unit_price", "NaN"), ("quantity", True)]:
            with self.subTest(field=field), TemporaryDirectory() as folder:
                data = deepcopy(original); data["trade"][field] = value
                path = Path(folder) / "case.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                with patch("workflows.demo_case.FIXTURE", path), self.assertRaises(CrossBotError):
                    load_fixture()

    def test_new_sku_does_not_override_legacy_evidence(self):
        old = retrieve_context("DEMO-LAMP-001")
        new = retrieve_context("DEMO-DE-LAMP-001")
        legacy = next(e for e in old["entries"] if e["type"] == "product")
        fixed = next(e for e in new["entries"] if e["type"] == "product")
        self.assertEqual(legacy["facts"]["color"], "白色")
        self.assertEqual(fixed["facts"]["color_temperature_modes"], 3)
        self.assertIsNone(fixed["facts"]["price"])
        self.assertEqual(retrieve_context("DEMO-LAMP-001 DEMO-DE-LAMP-001")["product_status"], "unknown")

    def test_animal_lamp_fixture_is_consistent_and_separate_from_legacy_case(self):
        data, product = load_animal_fixture()
        case = animal_lamp_case()
        sales = animal_sales_request()
        listing = animal_listing_request()
        self.assertEqual(case.case_id, "CASE-DE-ANIMAL-LAMP-001")
        self.assertNotEqual(case.case_id, demo_case().case_id)
        self.assertEqual(case.invoice_total, Decimal("9250.00"))
        self.assertIn("20 pcs/箱", sales.product_info)
        self.assertIn("25 business days", sales.product_info)
        self.assertNotIn("18.50", listing.product_description)
        self.assertEqual(listing.platforms, ["Amazon", "TikTok"])
        self.assertEqual(data["trade"]["packages"] * product["facts"]["units_per_carton"], 500)
        self.assertEqual(Decimal(product["facts"]["unit_net_weight_kg"]) * 20, Decimal("8"))

    def test_inconsistent_animal_demo_arithmetic_refuses_to_load(self):
        original = json.loads(ANIMAL_FIXTURE.read_text(encoding="utf-8"))
        for path, value in [("invoice_total", "9250.01"), ("packages", 24), ("gross_weight_kg", "249")]:
            with self.subTest(path=path), TemporaryDirectory() as folder:
                data = deepcopy(original)
                data["trade"][path] = value
                fixture = Path(folder) / "case.json"
                fixture.write_text(json.dumps(data), encoding="utf-8")
                with patch("workflows.animal_lamp_demo.FIXTURE", fixture), self.assertRaises(CrossBotError):
                    load_animal_fixture()

    def test_editable_wording_rules_flag_risky_claims_and_leave_supported_specs(self):
        risky = "100% safe, CE certified, improves eyesight, lasts all night, 5000 mAh, 12 W, EUR 9.99"
        codes = {risk.code for risk in review_content(risky, source_text="DEMO-ANIMAL-LAMP-001 1200 mAh 3 W EUR 18.50")}
        self.assertTrue({"absolute_claim", "certification_claim", "health_claim", "unsupported_specification", "unsupported_price"} <= codes)
        self.assertEqual(review_content("1200 mAh, DC 5 V, 3 W, 3000 K, 17 cm, 0.4 kg", source_text="1200 mAh, DC 5 V, 3 W, 3000 K, 17 cm, 0.4 kg"), [])

    def test_candidates_preserve_original_spans_and_do_not_mutate_input(self):
        request = SalesRequest("Need 500 USB lamps for Germany, or 1000 pcs.\nCE certificate, invoice, packing list?", "lamp", "英文")
        before = request.to_dict()
        result = analyze_inquiry(request)
        self.assertTrue(all(isinstance(f, ExtractedField) and f.status == FieldValueStatus.CANDIDATE
                            and f.source == FieldSource.MACHINE and f.confidence is None for f in result))
        self.assertEqual([f.value for f in result if f.name == "requested_quantity"], ["500 USB lamps", "1000 pcs"])
        self.assertTrue(all(str(f.value) in request.inquiry and "字符[" in f.note for f in result))
        self.assertEqual(request.to_dict(), before)
        self.assertIsNone(request.requested_quantity)

    def test_unknown_text_and_country_not_inferred_from_product(self):
        result = analyze_inquiry(SalesRequest("Hello", "Germany 500 pcs", "英文"))
        self.assertEqual(result, [])

    def test_ai_candidates_are_located_and_never_confirmed(self):
        request = demo_sales_request(); before = request.to_dict()
        fields = extract_inquiry_candidates(request, MockInquiryClient())
        self.assertTrue(fields)
        self.assertTrue(all(f.status == FieldValueStatus.CANDIDATE and f.confidence is None for f in fields))
        self.assertEqual(request.to_dict(), before)
        self.assertTrue(all("provider=mock" in f.note for f in fields))

    def test_ai_candidate_fake_source_fields_duplicate_keys_and_extra_status_rejected(self):
        correct = {"name": "customer_country", "value": "Germany", "start": 0, "end": 7}
        invalid = [dict(correct, value="France"), dict(correct, end=True), dict(correct, name="approved"),
                   dict(correct, status="confirmed"), dict(correct, start=-1)]
        outputs = [json.dumps({"fields": [item]}) for item in invalid]
        outputs += [json.dumps({"fields": [correct, correct]}), '{"fields":[],"fields":[]}',
                    json.dumps({"fields": [correct] * 21}), "not-json"]
        for raw in outputs:
            with self.subTest(raw=raw):
                client = Mock(); client.complete.return_value = raw
                with self.assertRaises(CrossBotError):
                    extract_inquiry_candidates(SalesRequest("Germany", "lamp", "en"), client)

    def test_empty_ai_candidate_set_stays_unknown_and_exception_is_static(self):
        self.assertEqual(extract_inquiry_candidates(SalesRequest("Hello", "lamp", "en"), MockInquiryClient()), [])
        client = Mock(); client.complete.side_effect = RuntimeError("private body")
        with self.assertRaises(CrossBotError) as caught:
            extract_inquiry_candidates(demo_sales_request(), client)
        self.assertNotIn("private body", caught.exception.message)
        client.reset_mock()
        with self.assertRaises(CrossBotError):
            extract_inquiry_candidates(SalesRequest(" ", "lamp", "en"), client)
        client.complete.assert_not_called()

    def test_request_has_candidates_but_null_quantity_stays_null(self):
        payload = json.loads(build_sales_messages(SalesRequest("Need 500 pcs", "lamp", "en"))[1]["content"])
        self.assertIsNone(payload["requested_quantity"])
        self.assertEqual(payload["local_inquiry_candidates"][0]["status"], "candidate")

    def test_advisory_checks_cover_original_extra_design_and_new_claims(self):
        for text, expected in [("Compact design", "design_inference"), ("100% safe", "absolute_claim"),
                               ("cures illness", "medical_claim"), ("CE-certified", "certification_claim"),
                               ("risk-free", "absolute_claim"), ("eye protection", "evidence_claim"),
                               ("medical benefit", "medical_claim"), ("护眼", "evidence_claim")]:
            with self.subTest(text=text):
                self.assertIn(expected, [r.code for r in review_content(text)])
        self.assertEqual(review_content("Please confirm specifications."), [])

    def test_numbers_normalize_decimal_ordering_but_not_currency_or_unit(self):
        source = "单价 18.50 EUR，500 pcs；重量185 kg"
        self.assertEqual(review_content("EUR 18.5, 500 pieces, 185 kg", source_text=source), [])
        risks = review_content("USD 18.50, 480 pcs, 185 W, 7 days", source_text=source)
        self.assertEqual(len(risks), 4)
        self.assertTrue(all(r.evidence and r.source for r in risks))

    def test_both_workflows_review_model_output_instead_of_trusting_prompt(self):
        client = Mock(); client.provider = "real"; client.model = "test-only"
        client.complete.return_value = "CE certified, 50 W, EUR 9.99, delivered in 3 days."
        response = generate_sales(SalesRequest("quote?", "USB ABS lamp", "en"), client)
        self.assertTrue(any(r.code == "unsupported_price" for r in response.risks))
        client.complete.return_value = json.dumps({"listings": {"Amazon": "Compact design, 50 W"}})
        response = generate_listings(ListingRequest("USB ABS lamp", ["Amazon"]), client)["Amazon"]
        self.assertEqual({r.code for r in response.risks}, {"design_inference", "unsupported_specification"})

    def test_customer_simulation_is_explicit_separate_and_never_confirms_case(self):
        request = demo_sales_request(); before = request.to_dict()
        response = simulate_customer(request, MockCustomerClient())
        self.assertEqual(response.provider, "mock")
        self.assertTrue(response.content.endswith("?"))
        self.assertIn("模拟客户", response.pending_confirmations[0])
        self.assertEqual(request.to_dict(), before)
        request.target_language = "德文"
        self.assertIn("Könnten", simulate_customer(request, MockCustomerClient()).content)

    def test_customer_failure_does_not_surface_provider_exception_text(self):
        for output in [None, " "]:
            client = Mock(); client.complete.return_value = output
            with self.assertRaises(CrossBotError): simulate_customer(demo_sales_request(), client)
        client = Mock(); client.complete.side_effect = RuntimeError("private payload")
        with self.assertRaises(CrossBotError) as caught: simulate_customer(demo_sales_request(), client)
        self.assertNotIn("private payload", caught.exception.message)
