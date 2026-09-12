# Phase 18 — Recovery diagnostics & guided repair

Status: implementation complete on the Phase 18 branch; full CI and Windows artifact verification pending.

## Objective

Phase 18 addresses the remaining usability gap in Phase 17: a transaction can correctly enter `recovery_required`, but the operator previously saw only the raw persisted error and a link back to the Task.

The Phase 18 objective is to make those frozen states understandable without creating a second recovery engine or weakening any Phase 11–17 safety gate.

## Structured diagnostic contract

The existing read-only `GET /recovery` response now adds a nullable `diagnostic` object.

Only `recovery_required` entries receive a diagnostic. Normal recoverable/history entries return `diagnostic: null`.

The diagnostic contains:

- deterministic diagnostic code;
- blocked severity;
- high / medium / low confidence;
- operator-facing title and summary;
- persisted evidence;
- ordered manual verification steps;
- explicit prohibited actions;
- `automatic_repair_available: false`.

Current diagnostic categories include:

- partial transaction;
- ambiguous disk state;
- SHA/hash mismatch;
- occupied target path;
- quarantine inconsistency;
- backup inconsistency;
- write-access failure;
- file lock;
- database finalization mismatch;
- startup recovery failure;
- manual-review fallback.

## Guided repair boundary

“Guided repair” in Phase 18 means guided human verification, not automatic file mutation.

The UI instructs the operator to:

1. open the original Task and audit timeline;
2. record the current path existence, size, and SHA-256 before manual changes;
3. inspect the transaction-specific source/target/backup/quarantine state;
4. determine one consistent desired state before any manual intervention.

Transactional batches explicitly warn against repairing only one member.

## Prohibited actions

Phase 18 intentionally does not add:

- force overwrite;
- ignore SHA / hash bypass;
- overwrite occupied targets;
- backup deletion;
- quarantine purge;
- single-member repair of an atomic batch;
- a POST/PATCH/DELETE recovery-diagnostic endpoint;
- a second rollback/restore state machine.

Existing recoverable transactions still use the original rollback/restore APIs from Phase 11–16.

## Desktop UI

The Recovery Center now renders, for each `recovery_required` entry:

- diagnostic title;
- code and confidence;
- explanation;
- guided verification checklist;
- prohibited actions;
- expandable evidence;
- an explicit notice that automatic repair is disabled;
- the existing link back to the originating Task.

## Verification targets

Phase 18 adds tests for:

- partial transactional recovery classification;
- ambiguous recycle-batch state classification;
- SHA mismatch classification;
- no diagnostic for normal recoverable entries;
- safe manual-review fallback for unknown failures;
- no automatic repair capability in the diagnostic contract.

The complete repository test count, packaged Engine 0.18.0 verification, Windows installer artifact id/hash, PR number, CI run and final merge SHA are recorded here after CI completes.
