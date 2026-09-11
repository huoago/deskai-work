# Phase 16 — Transactional batch recycle

Status: complete, fully verified, hardened, and merged to `main`.

## Objective

Phase 16 extends Phase 15 from one recoverable file to a 2–10 file all-or-nothing recycle transaction.

The safety objective is stronger than simple batch deletion:

**DeskAI must prepare and SHA-verify quarantine copies for every member before it removes the first Workspace original.**

## Agent tool

### propose_file_recycle_batch

Risk level: L5.

The Agent can stage a 2–10 file proposal only.

It cannot remove, purge, or permanently delete any file.

Each item contains:

- File id;
- short recycle summary.

The batch rejects duplicate File ids.

## Confirmation sequence

Confirmed batch recycle performs:

1. all-member path / write / processing / lock / SHA preflight;
2. persistent `recycling` state;
3. private quarantine copy for every member;
4. SHA-256 verification for every quarantine copy;
5. second all-member source SHA / processing / writeability check;
6. sequential source removal;
7. quarantine verification after each removal;
8. one database finalization for the complete batch.

If any removal fails, already-removed sources are restored from quarantine in reverse order.

A successful rollback returns the entire batch to `pending`.

## Batch restore

The complete batch must be restored as one unit.

All quarantine copies and original destinations are preflighted before the first restore.

If a later restore fails, earlier restored originals are removed again after SHA verification so the complete transaction remains `recycled`.

Quarantine copies remain after successful restore.

## Startup recovery

Interrupted `recycling` converges back to a complete original-path state and `pending`.

Interrupted `restoring` completes restoration and finalizes `restored`.

Ambiguous or unexpected hashes freeze the transaction as `recovery_required`.

## Verification matrix

Phase 16 tests cover:

- stage/confirm/restore for a two-file transaction;
- same File ids and SHA preservation;
- batch members cannot use single-file confirm/restore APIs;
- one stale member blocks the whole transaction before quarantine or removal;
- every quarantine copy exists and verifies before the first source removal;
- simulated second source-removal failure restores the first member;
- duplicate File ids are rejected;
- one occupied original path blocks the entire restore before any member changes;
- simulated second restore failure returns the first member to recycled state;
- startup recovery restores a partially recycled batch to pending;
- startup recovery completes a partially restored batch;
- ambiguous startup state freezes as recovery_required;
- Agent can only stage a pending transactional recycle batch;
- permanent-delete batch endpoint is absent.

### Merge verification

Phase 16 feature PR #24 passed the complete CI pipeline on Run #82 before merge:

- TypeScript typecheck — passed;
- Vite production build — passed;
- Ruff — passed;
- full Engine Pytest suite — **114 passed, 59 warnings**;
- Windows PyInstaller sidecar — passed;
- packaged Engine **0.16.0** health smoke — passed;
- packaged `/recycle-proposals` API smoke — passed;
- packaged `/recycle-batches` API smoke — passed;
- Tauri Windows NSIS/MSI build — passed;
- Windows Artifact upload — passed.

Feature PR #24 was squash merged to `main` as:

`30d422e71c78be7de2d7fbb87b37daac73c688a1`

After merge, a restore-race hardening review found one additional edge case: if the currently failing restore member left unexpected content at its original path, rollback needed to validate the **complete batch snapshot**, not only members whose restore calls had already returned success.

Focused hardening PR #26 added that protection, a dedicated regression test, and loaded the recycle workflow desktop stylesheet.

PR #26 passed full CI on Run #84:

- TypeScript typecheck — passed;
- Vite production build — passed;
- Ruff — passed;
- full Engine Pytest suite — **115 passed, 59 warnings**;
- Windows PyInstaller sidecar — passed;
- packaged Engine **0.16.0** health smoke — passed;
- packaged `/recycle-proposals` API smoke — passed;
- packaged `/recycle-batches` API smoke — passed;
- Tauri Windows NSIS/MSI build — passed;
- Windows Artifact upload — passed.

The hardening PR was squash merged to `main` as:

`7ab7be0217ab4d0eb9c20bcb5006299ad8b7eddc`

Final verified Windows installers:

- `DeskAI Work_0.1.0_x64-setup.exe`;
- `DeskAI Work_0.1.0_x64_en-US.msi`.

Final Windows Artifact from Run #84:

- name: `DeskAI-Work-Windows`;
- artifact id: `10282043965`;
- size: `394358241` bytes;
- SHA-256: `bdce10a646614bd9f808ac1f49b40f6cdebe040c79cf205184fa074db80cd243`.

The final regression matrix additionally verifies that unexpected concurrent content at a failing restore member is never auto-deleted and forces the batch into `recovery_required` instead of falsely reporting a complete recycled state.
