"""Local database setup shared by the CLI and Streamlit entry point."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from core.config import load_config
from core.errors import CrossBotError, ErrorCode
from core.models import Case, DocumentType
from .sqlite_store import SQLiteStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_ID = "CASE-DE-LAMP-001"


def database_path() -> Path:
    directory = Path(load_config().storage_dir).expanduser()
    if not directory.is_absolute():
        directory = PROJECT_ROOT / directory
    return directory / "crossbot.db"


def open_store() -> SQLiteStore:
    return SQLiteStore(database_path())


def import_demo_case(store: SQLiteStore) -> Case:
    """Idempotent import; existing Case data is never overwritten."""
    existing = store.get_case(DEMO_ID)
    if existing:
        return existing
    source = PROJECT_ROOT / "docs" / "demo_case" / "case_fixture.json"
    fixture = json.loads(source.read_text(encoding="utf-8-sig"))
    trade = fixture["trade"]
    case = Case(
        case_id=DEMO_ID, case_number=DEMO_ID,
        customer_name="德国台灯演示客户（虚构）",
        destination_country=trade["customer_country"],
        incoterm=trade["incoterm"], currency=trade["currency"],
        invoice_total=Decimal(trade["invoice_total"]),
        required_document_types=[DocumentType(name) for name in fixture["documents_required"]],
        metadata={"is_demo": True, "fixture": fixture, "source": "docs/demo_case/case_fixture.json"},
    )
    try:
        store.create_case(case, actor_id="demo_import")
    except CrossBotError as error:
        # Another browser can import the same sample at the same time.
        if error.code != ErrorCode.CONFLICT or store.get_case(DEMO_ID) is None:
            raise
    return store.get_case(DEMO_ID)
