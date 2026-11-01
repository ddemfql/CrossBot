# Core contracts

This package contains the shared contracts for the parallel CrossBot branches.

- `models.py`: domain objects, statuses, risks, and serialization.
- `schemas.py`: W1/W2 request and response objects.
- `inquiries.py`: account-owned pre-order conversations and candidate order-field names.
- `events.py`: streaming and workflow event objects.
- `errors.py`: stable error codes and structured exceptions.
- `config.py`: environment-backed application configuration.
  Optional `AppConfig.model_api_protocol` selects `auto`, `responses`, or `chat_completions`.
- `validators.py`: required fields and guarded Case state transitions.
- `identity.py`: roles, permissions and authenticated-principal snapshots.
- `v3.py`: versioned Case contexts, attachments, search citations and knowledge lifecycle.
- `ports.py`: provider/attachment integration protocols.

V3 integration services and examples: [B handoff](../docs/B_V3接入说明.md).
Use the `auth`, `documents.access`, `workflows.access`, `knowledge.governance`,
and `providers.search_contract` services for request-facing operations.
Pre-order W1 uses `workflows.inquiry.InquiryService`; its atomic handoff creates
an assigned draft Case, preserving original messages and unconfirmed field evidence.
Raw SQLite storage is an internal persistence adapter, not an authorization boundary.

Workflow, parser, UI, and validation modules should depend on these contracts
instead of passing ad-hoc dictionaries or newline-delimited strings.
