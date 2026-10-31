"""Authorized, append-only resource versions and publication history."""
from __future__ import annotations

import copy
import json
from urllib.parse import urlparse

from core.errors import CrossBotError, ErrorCode, ValidationError
from core.identity import Permission, Role
from core.models import from_dict, utc_now
from core.v3 import LifecycleStatus as Status, Publication, ResourceKind, ResourceVersion, SourceKind
from storage.v3_store import encode


class KnowledgeService:
    def __init__(self, auth):
        self.auth = auth
        self.repository = auth.repository

    @staticmethod
    def _version(conn, version_id):
        row = conn.execute("SELECT payload FROM resource_versions WHERE id=?", (version_id,)).fetchone()
        if row is None:
            raise CrossBotError(ErrorCode.NOT_FOUND, "知识版本不存在")
        return from_dict(ResourceVersion, json.loads(row["payload"]))

    @staticmethod
    def _save(conn, version):
        conn.execute("UPDATE resource_versions SET payload=? WHERE id=?", (encode(version), version.version_id))

    def create_draft(self, token, *, resource_id, kind, content, source, parent_version_id=None,
                     source_url=None, source_kind=SourceKind.LOCAL, effective_date=None,
                     market=None, platform=None, product_scope=None, language=None):
        kind, source_kind = ResourceKind(kind), SourceKind(source_kind)
        if not resource_id.strip() or not source.strip() or not content:
            raise ValidationError("知识标识、来源和内容不能为空")
        if source_url and urlparse(source_url).scheme not in {"http", "https"}:
            raise ValidationError("来源 URL 必须是 http/https")
        if kind == ResourceKind.PROHIBITED_TERM:
            if not content.get("term") or content.get("pattern_type", "exact") not in {"exact", "regex", "synonym"}:
                raise ValidationError("违禁词必须包含 term 和有效 pattern_type")
            if content.get("pattern_type") == "regex":
                import re
                try:
                    re.compile(content["term"])
                except re.error as error:
                    raise ValidationError("无效正则表达式") from error
        with self.repository.transaction() as conn:
            actor = self.auth._require(conn, token, Permission.KNOWLEDGE_EDIT)
            rows = conn.execute("SELECT payload FROM resource_versions WHERE resource_id=? ORDER BY version_number DESC", (resource_id,)).fetchall()
            if rows:
                latest = from_dict(ResourceVersion, json.loads(rows[0]["payload"]))
                if latest.kind != kind:
                    raise ValidationError("同一 resource_id 的类别不可更改")
                if not parent_version_id:
                    raise ValidationError("已有资源的新版本必须指定 parent_version_id")
            if parent_version_id:
                parent = self._version(conn, parent_version_id)
                if parent.resource_id != resource_id:
                    raise ValidationError("父版本不属于该资源")
            number = len(rows) + 1
            version = ResourceVersion(
                resource_id=resource_id, kind=kind, content=copy.deepcopy(content), source=source,
                version_number=number, parent_version_id=parent_version_id, source_url=source_url,
                source_kind=source_kind, effective_date=effective_date, market=market,
                platform=platform, product_scope=product_scope, language=language, created_by=actor.user_id,
            )
            conn.execute("INSERT INTO resource_versions VALUES (?, ?, ?, ?)", (version.version_id, resource_id, number, encode(version)))
            self.repository.audit(conn, "resource_draft_created", actor.user_id, entity_type=kind.value, entity_id=version.version_id)
            return version

    def submit(self, token, version_id):
        with self.repository.transaction() as conn:
            actor = self.auth._require(conn, token, Permission.KNOWLEDGE_EDIT)
            version = self._version(conn, version_id)
            if actor.user_id != version.created_by and actor.active_role != Role.ADMIN:
                raise CrossBotError(ErrorCode.PERMISSION_DENIED, "只能提交自己的草稿")
            if version.status != Status.DRAFT:
                raise ValidationError("只有草稿可提交")
            version.status = Status.IN_REVIEW
            self._save(conn, version)
            self.repository.audit(conn, "resource_submitted", actor.user_id, entity_id=version_id)
            return version

    def review(self, token, version_id, *, approve, reason):
        if not reason.strip():
            raise ValidationError("审核必须填写原因")
        with self.repository.transaction() as conn:
            actor = self.auth._require(conn, token, Permission.KNOWLEDGE_REVIEW)
            version = self._version(conn, version_id)
            if version.created_by == actor.user_id:
                raise CrossBotError(ErrorCode.PERMISSION_DENIED, "提交人不能自审")
            if version.status != Status.IN_REVIEW:
                raise ValidationError("只有待审核版本可审批")
            version.status = Status.APPROVED if approve else Status.REJECTED
            version.reviewed_by = actor.user_id
            self._save(conn, version)
            self.repository.audit(conn, "resource_reviewed", actor.user_id, entity_id=version_id, after={"status": version.status.value}, reason=reason)
            return version

    def _publish(self, conn, actor, version, reason, rollback):
        previous = conn.execute("SELECT id, version_id FROM resource_publications WHERE resource_id=? ORDER BY sequence DESC LIMIT 1", (version.resource_id,)).fetchone()
        publication = Publication(version.resource_id, version.version_id, actor.user_id,
                                  previous_publication_id=previous["id"] if previous else None,
                                  is_rollback=rollback, reason=reason)
        conn.execute("INSERT INTO resource_publications(id,resource_id,version_id,payload) VALUES (?,?,?,?)",
                     (publication.publication_id, version.resource_id, version.version_id, encode(publication)))
        if previous and previous["version_id"] != version.version_id:
            archived = self._version(conn, previous["version_id"])
            archived.status = Status.ARCHIVED
            archived.archived_at = publication.published_at
            self._save(conn, archived)
        version.status = Status.PUBLISHED
        version.published_at = version.published_at or publication.published_at
        version.archived_at = None
        self._save(conn, version)
        self.repository.audit(conn, "resource_rolled_back" if rollback else "resource_published", actor.user_id, entity_id=version.version_id, after={"publication_id": publication.publication_id}, reason=reason)
        return publication

    def publish(self, token, version_id, *, reason="Publish reviewed version"):
        with self.repository.transaction() as conn:
            actor = self.auth._require(conn, token, Permission.KNOWLEDGE_PUBLISH)
            version = self._version(conn, version_id)
            if version.status != Status.APPROVED or not version.reviewed_by or version.reviewed_by == version.created_by:
                raise ValidationError("版本必须先经过独立审核")
            if version.source_kind in {SourceKind.DEMO, SourceKind.EXTERNAL_REFERENCE}:
                raise ValidationError("演示/外部参考不能直接发布为正式知识；需另建有依据的版本")
            return self._publish(conn, actor, version, reason, False)

    def rollback(self, token, resource_id, version_id, *, reason):
        if not reason.strip():
            raise ValidationError("回滚必须填写原因")
        with self.repository.transaction() as conn:
            actor = self.auth._require(conn, token, Permission.KNOWLEDGE_ROLLBACK)
            version = self._version(conn, version_id)
            if version.resource_id != resource_id or version.status not in {Status.PUBLISHED, Status.ARCHIVED}:
                raise ValidationError("只能回滚到同一资源的已发布历史版本")
            return self._publish(conn, actor, version, reason, True)

    def get_published(self, token, resource_id):
        with self.repository.transaction() as conn:
            self.auth._require(conn, token, Permission.KNOWLEDGE_READ)
            row = conn.execute("SELECT version_id FROM resource_publications WHERE resource_id=? ORDER BY sequence DESC LIMIT 1", (resource_id,)).fetchone()
            return self._version(conn, row["version_id"]) if row else None

    def list_published(self, token, *, kind=None):
        with self.repository.transaction() as conn:
            self.auth._require(conn, token, Permission.KNOWLEDGE_READ)
            rows = conn.execute("SELECT p.version_id FROM resource_publications p WHERE p.sequence=(SELECT MAX(q.sequence) FROM resource_publications q WHERE q.resource_id=p.resource_id)").fetchall()
            versions = [self._version(conn, row["version_id"]) for row in rows]
            return [v for v in versions if kind is None or v.kind == ResourceKind(kind)]

    def get_version(self, token, version_id):
        with self.repository.transaction() as conn:
            actor = self.auth._require(conn, token, Permission.KNOWLEDGE_READ)
            version = self._version(conn, version_id)
            if version.status not in {Status.PUBLISHED, Status.ARCHIVED} and actor.user_id != version.created_by and actor.active_role not in {Role.ADMIN, Role.REVIEWER}:
                raise CrossBotError(ErrorCode.PERMISSION_DENIED, "无权读取未发布版本")
            return version

    def publications(self, token, resource_id):
        with self.repository.transaction() as conn:
            self.auth._require(conn, token, Permission.KNOWLEDGE_READ)
            return [from_dict(Publication, json.loads(row["payload"])) for row in conn.execute("SELECT payload FROM resource_publications WHERE resource_id=? ORDER BY sequence", (resource_id,))]

    def list_versions(self, token, *, resource_id=None, status=None, kind=None):
        """Draft/review queues without exposing another author's private drafts."""
        with self.repository.transaction() as conn:
            actor = self.auth._require(conn, token, Permission.KNOWLEDGE_READ)
            rows = conn.execute("SELECT payload FROM resource_versions ORDER BY rowid DESC").fetchall()
            versions = [from_dict(ResourceVersion, json.loads(row["payload"])) for row in rows]
            return [v for v in versions
                    if (v.status in {Status.PUBLISHED, Status.ARCHIVED} or v.created_by == actor.user_id or actor.active_role in {Role.ADMIN, Role.REVIEWER})
                    and (resource_id is None or v.resource_id == resource_id)
                    and (status is None or v.status == Status(status))
                    and (kind is None or v.kind == ResourceKind(kind))]
