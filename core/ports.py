"""Provider boundaries for business branches; pass server-owned implementations."""
from typing import Protocol, Sequence

from .schemas import GenerationResponse, ListingRequest, SalesRequest
from .v3 import AttachmentContent, AttachmentRef, SearchCitation


class ModelProvider(Protocol):
    def __call__(self, request: SalesRequest | ListingRequest) -> GenerationResponse: ...


class SearchProvider(Protocol):
    def __call__(self, query: str) -> Sequence[SearchCitation]: ...


class AttachmentReader(Protocol):
    def read(self, token: str, reference: AttachmentRef, *, require_ready: bool = False) -> AttachmentContent: ...
