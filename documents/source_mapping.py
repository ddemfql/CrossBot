"""Explicit source roles and field meanings used by both intake paths."""
from core.models import DocumentType as D

IDENTITY_FIELDS = frozenset({"document_no", "document_date", "invoice_no", "invoice_date",
    "packing_list_no", "packing_date", "contract_no", "inspection_date", "inspector",
    "issuer", "external_reference", "issued_on", "expires_on", "source_party",
    "recorded_conclusion", "evidence_reference", "responsible_person", "declaration_text", "notes"})
PROCUREMENT_FIELDS = frozenset({"buyer", "seller", "customer_name", "supplier", "currency",
    "payment_terms", "total_amount", "amount_due", "bank_name", "bank_address", "beneficiary",
    "bank_account", "swift", "unit_price", "amount", "incoterm", "order_no"})


def map_field(source_type, target_type, name):
    """Return the target name only when the source has the same business meaning."""
    source_type, target_type = D(source_type), D(target_type)
    if source_type != target_type:
        if name in IDENTITY_FIELDS:
            return None
        if source_type is D.PURCHASE_CONTRACT and name in PROCUREMENT_FIELDS:
            return None
    return "buyer" if name == "customer_name" else name

