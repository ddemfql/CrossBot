"""V3 identity snapshots. Only AuthService may resolve privileges."""
from dataclasses import dataclass, field
from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    CUSTOMER_SERVICE = "customer_service"
    PRODUCT = "product"
    OPERATIONS = "operations"
    DOCUMENT = "document"
    REVIEWER = "reviewer"


class Permission(str, Enum):
    CASE_READ = "case.read"
    CASE_CREATE = "case.create"
    CASE_ASSIGN = "case.assign"
    CASE_UPDATE = "case.update"
    CASE_REVIEW = "case.review"
    CASE_CLOSE = "case.close"
    CROSS_CHECK = "cross_check.execute"
    W1_GENERATE = "w1.generate"
    CUSTOMER_SIMULATE = "customer.simulate"
    W2_GENERATE = "w2.generate"
    ATTACHMENT_READ = "attachment.read"
    ATTACHMENT_WRITE = "attachment.write"
    ATTACHMENT_CONFIRM = "attachment.confirm"
    EXTERNAL_SEARCH = "external.search"
    KNOWLEDGE_READ = "knowledge.read"
    KNOWLEDGE_EDIT = "knowledge.edit"
    KNOWLEDGE_REVIEW = "knowledge.review"
    KNOWLEDGE_PUBLISH = "knowledge.publish"
    KNOWLEDGE_ROLLBACK = "knowledge.rollback"
    GENERATION_REVIEW = "generation.review"
    USER_MANAGE = "user.manage"


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    username: str
    display_name: str
    allowed_roles: list[Role]
    active_role: Role
    status: str = "active"


@dataclass(slots=True)
class UserSummary:
    user_id: str
    username: str
    display_name: str
    roles: list[Role] = field(default_factory=list)
    status: str = "active"
