# Phase 18 — Recovery diagnostics & guided repair

Status: complete, fully verified, and merged to `main`.

## Objective

Phase 18 closes the main usability gap left by Phase 17: a transaction can correctly enter `recovery_required`, but a raw persisted error alone is not enough for an operator to understand the freeze safely.

Phase 18 makes those frozen states diagnosable without creating a second recovery engine and without weakening any Phase 11–17 transaction gate.

## Structured diagnostic contract

The existing read-only `GET /recovery` response now includes a nullable `diagnostic` object.

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

A Phase 17 edge case was also hardened: persisted `status == "recovery_required"` is now authoritative even if an underlying single-file service omits the convenience boolean. Such records cannot be misclassified as ordinary history.

## Guided repair boundary

“Guided repair” in Phase 18 means guided human verification, not automatic file mutation.

The UI instructs the operator to:

1. open the original Task and audit timeline;
2. record current path existence, file size, and SHA-256 before manual changes;
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

Existing recoverable transactions continue to use the original rollback/restore APIs from Phase 11–16.

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

## Verification matrix

Phase 18 adds tests for:

- partial transactional recovery classification;
- ambiguous recycle-batch state classification;
- SHA mismatch classification;
- status-only recovery freeze recognition;
- no diagnostic for normal recoverable entries;
- safe manual-review fallback for unknown failures;
- no automatic repair capability in the diagnostic contract.

### Feature verification

Feature PR #31 passed the complete CI pipeline on Run #92 before merge:

- TypeScript typecheck — passed;
- Vite production build — passed;
- Ruff — passed;
- full Engine Pytest suite — **125 passed, 59 warnings**;
- Windows PyInstaller sidecar — passed;
- packaged Engine **0.18.0** health smoke — passed;
- packaged `/recovery` API smoke — passed;
- Tauri Windows NSIS build — passed;
- Tauri Windows MSI build — passed;
- Windows Artifact upload — passed.

Verified Windows installers:

- `DeskAI Work_0.1.0_x64-setup.exe`;
- `DeskAI Work_0.1.0_x64_en-US.msi`.

Verified Windows Artifact:

- name: `DeskAI-Work-Windows`;
- artifact id: `10287381464`;
- size: `394376169` bytes;
- SHA-256: `c76b7eb5baa30b5c26451d260fd1a4932b96581ecd34ad3a9ae64274c2760b69`.

Feature PR #31 was squash merged to `main` as:

`e43ede5af6810bde1408fc31de3318a43568e583`

CI Run #92:

`34660006257`
