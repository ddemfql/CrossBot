# W3 Branch Agent Instructions

## Scope

These instructions apply to work on `feature/w3-document-workflow` and the W3 modules:
`case_workflow/`, `documents/`, and `review/`. They supplement `AI开发约束.md` and do not replace its safety, contract, or testing requirements.

## Work Priority

- Optimize for completing correct, reviewable work efficiently. Do not treat token, runtime, or agent-call consumption as a limiting budget.
- Use multiple subagents whenever independent work can be completed in parallel and delegation is likely to reduce elapsed time or improve coverage. Split work by module or deliverable, state ownership and expected outputs, and integrate the results in the main task.
- Keep concurrent edits on separate files or clearly separated modules. Before assigning work, identify shared-contract or shared-file dependencies; one owner should make each shared-file change.
- Use a review/test subagent when it can independently check integration, requirements coverage, or edge cases without duplicating implementation work.
- Do not parallelize dependent steps. Resolve public contract questions first, then dispatch the independent work that depends on them.

## Product Direction

- Treat W3 as a transaction-level trade document workspace, not only a PDF/Excel parser. The longer-term workflow covers inquiry/quotation, order confirmation, product and compliance materials, production/QC, shipment documents, transport, and receivables.
- Drive required documents and milestones from destination, product, transport mode, Incoterm, and payment terms. Do not assume every trade has the same stages or required documents.
- Separate files the service may draft from documents issued or signed by a buyer, carrier, broker, laboratory, regulator, or authorized person. The service may prepare seller-controlled drafts; it must not use AI to fabricate official documents, signatures, certificates, carrier records, or customs filing confirmations.
- Preserve user-supplied reference files as confidential source material. Never commit the provided ZIP or its real transaction contents. Use synthetic or properly anonymized fixtures, and do not inspect secrets such as `.env` in reference projects.
- Borrow architectural principles from reference projects (immutable originals, structured business data, versioned templates, review gates, generated-artifact hashes, and audit trails); do not copy their unrelated domain model or stack wholesale.

## W3 Boundaries

- W3 implementation belongs in `case_workflow/`, `documents/`, and `review/`.
- Use public contracts from `core.models`, `core.events`, and `core.errors`. Do not create competing copies of shared domain models. In particular, do not overload the current `CaseStatus.PASSED` to mean shipped, delivered, or paid; coordinate a distinct trade milestone/receivable contract with `core/`.
- Coordinate persistence interfaces with the `storage/` owner and cross-document rule inputs/results with the `validation/` owner. W3 may define its orchestration around these boundaries, but must not duplicate validation rules or create an incompatible private persistence API.
- Case approval requires both human confirmation and deterministic rule results. An AI model must never approve a Case or document.
- Preserve original files, versions, machine candidates, human changes, provenance, generation inputs, output hashes, and audit history. Regeneration creates a new artifact version; it never overwrites an original or prior output.
- Distinguish generic file management from parsing support. A file may be stored, categorized, linked, previewed/downloaded, and versioned even when no parser exists for its format.

## Completion

- Keep changes within the assigned W3 scope unless a shared-contract change is agreed and tested.
- Add focused tests for state transitions, parser failures, missing data, human edits, generated-file reproducibility/traceability, and integration outcomes as applicable.
- Run relevant tests and syntax checks, inspect `git status`, and report changed files, test results, interface dependencies, and known limitations.
