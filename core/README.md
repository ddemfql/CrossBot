# Core contracts

This package contains the shared contracts for the parallel CrossBot branches.

- `models.py`: domain objects, statuses, risks, and serialization.
- `schemas.py`: W1/W2 request and response objects.
- `events.py`: streaming and workflow event objects.
- `errors.py`: stable error codes and structured exceptions.
- `config.py`: environment-backed application configuration.
- `validators.py`: required fields and guarded Case state transitions.

Workflow, parser, UI, and validation modules should depend on these contracts
instead of passing ad-hoc dictionaries or newline-delimited strings.
