"""Provider boundaries for business branches; pass server-owned implementations."""
from dataclasses import dataclass
from typing import Protocol, Sequence

from .models import Actor, CrossCheckResult, DocumentType, TradeData, Workspace, UserWorkbenchDefaults

from .schemas import GenerationResponse, ListingRequest, SalesRequest
from .v3 import AttachmentContent, AttachmentRef, SearchCitation


class ModelProvider(Protocol):
    def __call__(self, request: SalesRequest | ListingRequest) -> GenerationResponse: ...


class SearchProvider(Protocol):
    def __call__(self, query: str) -> Sequence[SearchCitation]: ...


class AttachmentReader(Protocol):
    def read(self, token: str, reference: AttachmentRef, *, require_ready: bool = False) -> AttachmentContent: ...


class UserDefaultsStore(Protocol):
    def get(self, user_id: str) -> UserWorkbenchDefaults | None: ...
    def save(self, defaults: UserWorkbenchDefaults, expected_revision: int) -> None: ...

class WorkspaceRepository(Protocol):
    def get(self, case_id: str) -> Workspace: ...
    def list(self) -> list[Workspace]: ...
    def save(self, workspace: Workspace, expected_revision: int) -> None:
        """Atomically append a snapshot; CAS revision then increment workspace.revision."""
        ...

class BlobStore(Protocol):
    def put(self, content: bytes) -> str: ...
    def get(self, key: str) -> bytes: ...

class RulesEngine(Protocol):
    def evaluate(self, workspace: Workspace) -> list[CrossCheckResult]: ...

class IdentityStore(Protocol):
    def add_user(self, user_id: str, password: str, roles: list[str]) -> None: ...
    def authenticate(self, user_id: str, password: str) -> Actor | None: ...

@dataclass(frozen=True)
class RenderedOutput:
    content: bytes
    media_type: str
    extension: str
    template_id: str
    template_version: str
    template_sha256: str

class DocumentRenderer(Protocol):
    def render(self, document_type: DocumentType, trade: TradeData) -> RenderedOutput: ...
