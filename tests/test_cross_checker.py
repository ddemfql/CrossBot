"""Preflight tests using existing shared objects and synthetic data only."""
from copy import deepcopy
import unittest

from core.errors import ValidationError
from core.models import (
    Case, Document, DocumentStatus, DocumentType, DocumentVersion,
    ExtractedField, FieldValueStatus, to_dict,
)
from validation.cross_checker import get_confirmed_field, get_current_version


class CrossCheckPreflightTests(unittest.TestCase):
    def setUp(self):
        self.case = Case(case_id="case-synthetic")
        self.field = ExtractedField(
            name="fixture_quantity", value=100, normalized_value="100",
            status=FieldValueStatus.CONFIRMED, page_number=1,
        )
        self.version = DocumentVersion(
            version_id="version-synthetic-1", document_id="doc-synthetic",
            sha256="a" * 64, status=DocumentStatus.PARSED, fields=[self.field],
        )
        self.document = Document(
            document_id="doc-synthetic", case_id=self.case.case_id,
            document_type=DocumentType.COMMERCIAL_INVOICE,
            status=DocumentStatus.PARSED, current_version_id=self.version.version_id,
            versions=[self.version],
        )

    def test_current_version_uses_id_instead_of_list_order(self):
        old = deepcopy(self.version)
        old.version_id = "version-synthetic-old"
        self.document.versions.append(old)
        self.assertIs(get_current_version(self.case, self.document), self.version)

    def test_foreign_case_and_missing_identity_are_rejected(self):
        for case_id in ("other-case", ""):
            with self.subTest(case_id=case_id):
                self.document.case_id = case_id
                with self.assertRaises(ValidationError):
                    get_current_version(self.case, self.document)
        self.document.case_id = self.case.case_id
        self.document.document_id = ""
        with self.assertRaises(ValidationError):
            get_current_version(self.case, self.document)

    def test_missing_unknown_and_duplicate_current_version_are_rejected(self):
        for version_id in (None, "unknown"):
            with self.subTest(version_id=version_id):
                self.document.current_version_id = version_id
                with self.assertRaises(ValidationError):
                    get_current_version(self.case, self.document)
        self.document.current_version_id = self.version.version_id
        self.document.versions.append(deepcopy(self.version))
        with self.assertRaises(ValidationError):
            get_current_version(self.case, self.document)

    def test_foreign_version_and_missing_hash_are_rejected(self):
        self.version.document_id = "other-document"
        with self.assertRaises(ValidationError):
            get_current_version(self.case, self.document)
        self.version.document_id = self.document.document_id
        self.version.sha256 = ""
        with self.assertRaises(ValidationError):
            get_current_version(self.case, self.document)

    def test_unparsed_document_or_current_version_are_rejected(self):
        for entity in (self.document, self.version):
            for status in (DocumentStatus.UPLOADED, DocumentStatus.PARSING,
                           DocumentStatus.PARSE_FAILED, DocumentStatus.SUPERSEDED):
                with self.subTest(entity=type(entity).__name__, status=status):
                    entity.status = status
                    with self.assertRaises(ValidationError):
                        get_current_version(self.case, self.document)
            entity.status = DocumentStatus.PARSED

    def test_version_change_is_resolved_again_without_cache(self):
        self.assertIs(get_current_version(self.case, self.document), self.version)
        new = deepcopy(self.version)
        new.version_id = "version-synthetic-2"
        new.version_number = 2
        new.fields[0].status = FieldValueStatus.CANDIDATE
        self.document.versions.append(new)
        self.document.current_version_id = new.version_id
        self.assertIs(get_current_version(self.case, self.document), new)
        with self.assertRaises(ValidationError):
            get_confirmed_field(new, "fixture_quantity")

    def test_field_selection_is_exact_unique_and_requires_confirmation(self):
        self.assertIs(get_confirmed_field(self.version, "fixture_quantity"), self.field)
        for name in ("FIXTURE_QUANTITY", "unknown"):
            with self.subTest(name=name):
                with self.assertRaises(ValidationError):
                    get_confirmed_field(self.version, name)
        self.version.fields.append(deepcopy(self.field))
        with self.assertRaises(ValidationError):
            get_confirmed_field(self.version, "fixture_quantity")

    def test_unreviewed_rejected_and_missing_fields_are_rejected(self):
        for status in (FieldValueStatus.CANDIDATE, FieldValueStatus.REJECTED, FieldValueStatus.MISSING):
            with self.subTest(status=status):
                self.field.status = status
                with self.assertRaises(ValidationError):
                    get_confirmed_field(self.version, "fixture_quantity")

    def test_blank_field_name_and_unparsed_field_version_are_rejected(self):
        for name in (None, "", "  "):
            with self.subTest(name=name):
                with self.assertRaises(ValidationError):
                    get_confirmed_field(self.version, name)
        self.version.status = DocumentStatus.PARSE_FAILED
        with self.assertRaises(ValidationError):
            get_confirmed_field(self.version, "fixture_quantity")

    def test_nonpositive_version_number_is_rejected(self):
        self.version.version_number = 0
        with self.assertRaises(ValidationError):
            get_current_version(self.case, self.document)

    def test_preflight_preserves_input_and_does_not_choose_final_value(self):
        self.field.value = "machine-original"
        self.field.normalized_value = "different-normalized-value"
        before = to_dict(self.document)
        version = get_current_version(self.case, self.document)
        field = get_confirmed_field(version, "fixture_quantity")
        self.assertIs(field, self.field)
        self.assertEqual(to_dict(self.document), before)


if __name__ == "__main__":
    unittest.main()

