"""Transactional V3 persistence. UI/providers use services, not these tables."""
from __future__ import annotations

import json
from contextlib import closing, contextmanager

from core.models import AuditEvent, to_dict


def encode(value):
    return json.dumps(to_dict(value), ensure_ascii=False, allow_nan=False)


def initialize_v3_schema(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
            display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
            roles TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
            failed_attempts INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS auth_sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            active_role TEXT NOT NULL, expires_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS case_assignments (
            case_id TEXT NOT NULL REFERENCES cases(id), user_id TEXT NOT NULL REFERENCES users(id),
            PRIMARY KEY(case_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS resource_versions (
            id TEXT PRIMARY KEY, resource_id TEXT NOT NULL, version_number INTEGER NOT NULL,
            payload TEXT NOT NULL, UNIQUE(resource_id, version_number)
        );
        CREATE TABLE IF NOT EXISTS resource_publications (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
            resource_id TEXT NOT NULL, version_id TEXT NOT NULL REFERENCES resource_versions(id),
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS attachment_contents (
            version_id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
            case_id TEXT NOT NULL REFERENCES cases(id), payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS search_reports (
            id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS generation_artifacts (
            id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_resource_versions ON resource_versions(resource_id);
        CREATE INDEX IF NOT EXISTS idx_publication_resource ON resource_publications(resource_id, sequence);
        CREATE INDEX IF NOT EXISTS idx_assignment_user ON case_assignments(user_id);
        PRAGMA user_version = 2;
    """)


class V3Repository:
    def __init__(self, store):
        self.store = store

    @contextmanager
    def transaction(self):
        with closing(self.store._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            yield connection

    def audit(self, connection, action, actor_id, *, case_id="", entity_type="", entity_id="", before=None, after=None, reason=None):
        event = AuditEvent(
            case_id=case_id, actor_id=actor_id, action=action,
            entity_type=entity_type, entity_id=entity_id,
            before=before, after=after, reason=reason,
        )
        connection.execute(
            "INSERT INTO audit_events(id, case_id, payload) VALUES (?, ?, ?)",
            (event.audit_id, case_id, encode(event)),
        )
        return event
