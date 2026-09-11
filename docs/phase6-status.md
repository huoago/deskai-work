# Phase 6 — Durable memory and autonomous learning gate

Status: implemented on the Phase 6 branch and pending final CI verification.

## Objective

Phase 6 adds a durable learning layer that is separate from the Phase 4 document knowledge base.

The distinction is intentional:

- Knowledge Base stores evidence from files and preserves page/sheet/slide/section locators.
- Memory stores durable user-provided preferences, decisions, constraints, corrections, project state, workflows, and person/role relationships.

Document contents are not copied into Memory merely because they were retrieved or shown to the model.

## Learning lifecycle

A successful Phase 5 chat may enqueue one persistent `memory_learning_jobs` record for the user message.

The job survives application restart because it is stored in SQLite.

The Memory Worker:

1. claims a queued job;
2. loads the current user message;
3. loads a bounded amount of prior conversation only for reference resolution;
4. checks privacy mode and provider availability;
5. asks the configured model for strict Structured Output;
6. validates candidate type, scope, confidence, importance, size, and sensitivity;
7. writes or updates durable Memory records;
8. preserves the previous value in MemoryVersion when a value changes;
9. marks the learning job completed, skipped, blocked, or failed.

The assistant answer itself is never treated as the source of a new memory.

## Structured extraction

Automatic extraction is constrained to these categories:

- preference
- decision
- constraint
- correction
- project_state
- workflow
- person_role

Each extracted candidate contains:

- global or Workspace scope
- type
- subject
- predicate
- string value
- confidence
- importance

Structured Output is validated against a JSON Schema before DeskAI accepts the result.

## Scope

Global persistence is intentionally conservative.

Only durable cross-project preferences, constraints, and workflows can be automatically stored globally.

Project decisions, corrections, project state, and person/role relationships are kept in the current Workspace even if the model proposes global scope.

## Sensitive-information boundary

The model is instructed not to extract sensitive personal information or credentials, and the Engine independently filters candidate memories.

Automatic and manual Memory rejects obvious:

- passwords
- API keys and tokens
- payment-card credentials
- government identifiers
- health/medical information
- race/ethnicity
- religion
- political affiliation
- sexual orientation/sex-life information
- trade-union membership

This is defense in depth. Secret storage remains the responsibility of the dedicated OS-backed SecretStore, not Memory.

## Correction and versioning

Memory does not silently overwrite history.

When the same Workspace/scope + subject + predicate receives a materially different value:

1. the prior value is copied to `memory_versions`;
2. the active Memory receives the new value;
3. source message, confidence, importance, and validity metadata are updated.

Example:

- previous: 324 water-meter total = 4475
- user correction: 324 water-meter total = 4447
- active Memory becomes 4447
- 4475 remains in MemoryVersion with the update reason

## Retrieval

Before every Phase 5 cloud-backed chat, DeskAI retrieves relevant active Memory records from:

- the current Workspace; and
- global Memory.

Ranking combines the existing local deterministic vector representation with confidence, importance, scope, and durable-type boosts.

Selected memories are injected inside a separate `<memory_context>` block.

Rules:

- the current user message overrides conflicting Memory;
- Memory does not override system safety rules;
- Memory is not cited as a local-file source;
- file-grounded factual claims continue using Phase 4/5 citations.

## Settings

Phase 6 adds:

- `memory_auto_learn` — enable/disable autonomous conversation learning;
- `memory_min_confidence` — minimum model confidence for automatic persistence.

Disabling autonomous learning does not delete existing memories and does not disable memory retrieval.

## APIs

- `GET /memory/status`
- `POST /memory/process`
- `POST /memory/retry`
- `GET /memories`
- `GET /memories/search`
- `POST /memories`
- `PATCH /memories/{memory_id}`
- `DELETE /memories/{memory_id}` — soft-deactivates
- `GET /memories/{memory_id}/versions`

## Desktop UX

The new “记忆” page provides:

- active/global/current-Workspace counts;
- persistent learning-queue state;
- Memory Worker status;
- search/filter;
- inactive-memory visibility;
- manual memory creation;
- manual value correction;
- soft deactivation;
- reactivation;
- source type, confidence, importance, and update time.

The Settings page controls automatic learning and minimum confidence.

## Failure behavior

Learning is asynchronous relative to the chat answer.

A memory-learning failure never invalidates an already successful answer.

Jobs may be:

- queued
- processing
- completed
- skipped
- blocked
- failed

Blocked/failed jobs remain visible in persistent status and can be retried from the desktop.

## Verification

Phase 6 tests verify:

- a global preference is learned from a user message;
- the learned preference is injected into a later chat;
- corrections replace the active value while preserving MemoryVersion history;
- sensitive candidates are rejected even if the model returns them;
- autonomous learning can be disabled;
- manual create/edit/deactivate/version behavior;
- Workspace-scoped memory does not cross projects;
- Phase 5 RAG/citation behavior remains intact.

CI never calls a real model. Memory extraction is tested with deterministic fake providers.

## Phase boundary

Phase 6 adds durable memory only.

It does not yet execute tools, modify arbitrary user files, browse GUIs, send messages, perform payments, or autonomously plan destructive actions. Those remain later Agent phases with explicit permission and audit controls.
