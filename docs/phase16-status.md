# Phase 16 — Transactional batch recycle

Status: implemented on the Phase 16 branch and pending full CI verification.

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

Required CI gates:

- Ruff;
- full Engine Pytest suite;
- TypeScript typecheck;
- Vite production build;
- Windows PyInstaller sidecar;
- packaged Engine 0.16.0 smoke;
- packaged `/recycle-batches` API smoke;
- Tauri NSIS/MSI;
- Windows Artifact upload.
