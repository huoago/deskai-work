# Phase 14 — Transactional file organization

Status: complete, verified by full CI, and merged to `main`.

## Objective

Phase 14 lets one Agent task stage 2–10 independent file rename/move operations and lets the user review and confirm the entire set as one all-or-nothing transaction.

It builds directly on the Phase 13 path-safety boundary.

## Agent tool

### propose_file_organization_batch

Risk level: L3.

The tool only stages a batch. It never changes file paths.

Each operation contains:

- File id;
- `rename` or `move`;
- summary;
- new filename for rename;
- target relative directory for move.

The batch requires 2–10 unique File ids.

## Batch constraints

Every member must independently satisfy Phase 13.

Additionally:

- all target paths must be unique;
- targets must be absent;
- a target cannot be another member's source path;
- swap/cycle rename semantics are intentionally unsupported;
- batch members cannot be confirmed individually.

## Confirmation transaction

Before any path mutation, DeskAI preflights every member:

- Workspace and write permission;
- current source path;
- SHA-256 freshness;
- parser/indexer processing state;
- source writeability / Office lock state;
- target absence;
- target directory writeability;
- same-device constraint.

Only after all checks pass does the batch enter `applying`.

Members are then moved sequentially with no-overwrite semantics.

If any operation fails, already-moved members are restored in reverse order.

A fully restored failure returns to `pending`; an incomplete restoration becomes `recovery_required`.

## Rollback transaction

Rollback is also all-or-nothing at the DeskAI transaction layer.

Every current target file must still match its applied SHA-256 and every original path must be free before the first reverse move.

If rollback fails after some members were restored, DeskAI attempts to reapply those members to the batch target paths.

## Startup recovery

Phase 14 recovery runs before Phase 13 individual path recovery.

For interrupted apply, DeskAI restores any target-path members to originals and returns the whole batch to `pending`.

For interrupted rollback, DeskAI restores all remaining target-path members to originals and finalizes the batch as `rolled_back`.

Ambiguous member states freeze the batch as `recovery_required`.

## Verification matrix

Phase 14 tests cover:

- two-file stage/confirm/rollback;
- File id and content SHA preservation;
- batch members cannot be individually confirmed;
- one stale member blocks every path change;
- simulated second operation failure restores the first member;
- duplicate target paths are rejected;
- occupied original path blocks whole-batch rollback;
- interrupted partial apply is restored to pending at startup;
- ambiguous startup member freezes the batch;
- duplicate File ids are rejected;
- Agent can only stage a pending organization batch.

### Merge verification

Feature PR #20 passed the complete CI pipeline on Run #72 before merge:

- TypeScript typecheck — passed;
- Vite production build — passed;
- Ruff — passed;
- full Engine Pytest suite — **90 passed, 59 warnings**;
- Windows PyInstaller sidecar — passed;
- packaged Engine **0.14.0** health smoke — passed;
- packaged `/file-operation-batches` API smoke — passed;
- Tauri Windows NSIS/MSI build — passed;
- Windows Artifact upload — passed.

Feature PR #20 was squash merged to `main` as commit:

`b90d62a7afd61a9b3dab0d4026afe0f937d89d24`

Verified Windows Artifact:

- name: `DeskAI-Work-Windows`;
- artifact id: `10277531358`;
- size: `394265837` bytes;
- SHA-256: `4ad64a8720980061d680c642c1739219d0b060af177ba9fe4455343b9d585b6d`.
