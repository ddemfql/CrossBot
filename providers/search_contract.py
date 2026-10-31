"""Authorized search invocation and citation persistence; B supplies providers."""
from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlparse

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.identity import Permission
from core.models import from_dict
from core.v3 import SearchCitation, SearchReport, SourceKind
from storage.v3_store import encode


class SearchService:
    def __init__(self, auth):
        self.auth = auth
        self.repository = auth.repository

    def execute(self, token, *, case_id, query, purpose, source_kind, provider):
        source_kind = SourceKind(source_kind)
        if not query.strip() or not purpose.strip():
            raise ValidationError("搜索词和用途不能为空")
        with self.repository.transaction() as conn:
            actor = self.auth._require(conn, token, Permission.EXTERNAL_SEARCH, case_id)
            audit = self.repository.audit(conn, "search_authorized", actor.user_id, case_id=case_id, after={"query": query, "purpose": purpose, "source_kind": source_kind.value})
        # Provider callbacks are server-owned adapters, never caller-supplied code.
        try:
            citations = list(provider(query))
        except Exception as error:
            with self.repository.transaction() as conn:
                self.repository.audit(conn, "search_failed", actor.user_id, case_id=case_id, entity_id=audit.audit_id)
            raise CrossBotError(ErrorCode.PROVIDER_UNAVAILABLE, "搜索服务不可用；如使用固定样例，需另建 demo 搜索记录", retryable=True) from error
        for citation in citations:
            if not isinstance(citation, SearchCitation) or not isinstance(citation.retrieved_at, datetime) or citation.retrieved_at.tzinfo is None:
                raise ValidationError("搜索结果必须提供带时区的抓取时间")
            if not citation.title.strip():
                raise ValidationError("搜索结果缺少标题")
            if citation.confidence is not None and not 0 <= citation.confidence <= 1:
                raise ValidationError("搜索置信度必须在 0 和 1 之间")
            if source_kind in {SourceKind.EXTERNAL_REFERENCE, SourceKind.AUTHORIZED_API}:
                url = urlparse(citation.url or "")
                if url.scheme not in {"http", "https"} or not url.netloc:
                    raise ValidationError("真实搜索必须返回实际来源 URL")
            citation.review_status = "pending_review"
        report = SearchReport(case_id, actor.user_id, query, purpose, source_kind, citations, authorization_audit_id=audit.audit_id)
        with self.repository.transaction() as conn:
            self.auth._require(conn, token, Permission.EXTERNAL_SEARCH, case_id)
            conn.execute("INSERT INTO search_reports VALUES (?, ?, ?)", (report.search_id, case_id, encode(report)))
            self.repository.audit(conn, "search_saved", actor.user_id, case_id=case_id, entity_id=report.search_id, after={"source_kind": source_kind.value, "count": len(citations)})
        return report

    def read(self, token, search_id):
        with self.repository.transaction() as conn:
            row = conn.execute("SELECT case_id, payload FROM search_reports WHERE id=?", (search_id,)).fetchone()
            if not row:
                raise CrossBotError(ErrorCode.NOT_FOUND, "搜索记录不存在")
            self.auth._require(conn, token, Permission.CASE_READ, row["case_id"])
            return from_dict(SearchReport, json.loads(row["payload"]))

    def list_for_case(self, token, case_id):
        with self.repository.transaction() as conn:
            self.auth._require(conn, token, Permission.CASE_READ, case_id)
            return [from_dict(SearchReport, json.loads(row["payload"])) for row in conn.execute("SELECT payload FROM search_reports WHERE case_id=? ORDER BY rowid", (case_id,))]
