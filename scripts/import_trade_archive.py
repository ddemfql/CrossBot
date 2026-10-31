"""Import an authorized local transaction ZIP without logging source contents."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import os
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from case_workflow.importer import ArchiveImporter
from core.models import Actor
from storage.blobs import LocalBlobStore
from storage.identity import SQLiteIdentityStore
from storage.sqlite import SQLiteWorkspaceRepository


def main():
    parser = argparse.ArgumentParser(description="Import a transaction archive into the local workbench")
    parser.add_argument("archive", type=Path)
    parser.add_argument("--admin", required=True, help="Existing local administrator receiving this import")
    parser.add_argument("--data-dir", type=Path,
        default=Path(os.environ.get("CROSSBOT_W3_DATA", Path.home() / ".crossbot" / "w3")))
    args = parser.parse_args()
    identity = SQLiteIdentityStore(args.data_dir / "workspace.sqlite3")
    users = identity.list_users()
    identity.close()
    if not any(u["user_id"] == args.admin and "admin" in u["roles"] for u in users):
        parser.error("The named administrator must already exist in this workspace")
    # Local CLI authorization comes from the operator's filesystem access;
    # this is not an internet authentication endpoint and never approves data.
    actor = Actor(args.admin, ["admin"], authenticated=True)
    backup_dir = args.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    with sqlite3.connect(args.data_dir / "workspace.sqlite3") as source, \
            sqlite3.connect(backup_dir / f"before-import-{stamp}.sqlite3") as destination:
        source.backup(destination)
    repository = SQLiteWorkspaceRepository(args.data_dir / "workspace.sqlite3")
    try:
        workspace = ArchiveImporter(repository, LocalBlobStore(args.data_dir / "files")).import_zip(
            args.archive.read_bytes(), args.archive.name, actor)
        print(f"Imported case: {workspace.case.case_id}")
        print(f"Original files: {len(workspace.documents)}; forms: {len(workspace.document_forms)}")
        print(f"Human approvals: {len(workspace.approvals)}; data directory: {args.data_dir}")
    finally:
        repository.close()


if __name__ == "__main__":
    main()

