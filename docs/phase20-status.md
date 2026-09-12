# Phase 20 — Controlled recovery state reconciliation

Status: complete, fully verified, and merged to `main`.

## Objective

Phase 19 can prove what currently exists on disk, but intentionally never clears `recovery_required`.

Phase 20 adds a narrowly-scoped metadata reconciliation path for the two states that can safely reconnect to an existing historical action:

- verified `consistent_applied` → persisted transaction status `applied`, reopening the original rollback API;
- verified `consistent_recycled` → persisted transaction status `recycled`, reopening the original restore API.

Phase 20 does **not** reconcile original/restored/mixed/ambiguous states.

## API surface

Proposal creation uses the disambiguated static route:

`POST /recovery-reconciliations/transactions/{entity_type}/{transaction_id}`

Confirmation/rejection remain proposal-id routes:

- `POST /recovery-reconciliations/{proposal_id}/confirm`
- `POST /recovery-reconciliations/{proposal_id}/reject`

This prevents dynamic transaction-route matching from intercepting proposal confirmation paths.

## Two-step proposal flow

Reconciliation is never a one-click state override.

1. The operator first creates a reconciliation proposal from a current Phase 19 snapshot.
2. DeskAI verifies that the snapshot is one consistent actionable state and that all historical rollback/restore artifacts are present.
3. DeskAI persists only a stable snapshot fingerprint, snapshot state, target status, and historical action.
4. The operator sees the target metadata state and must explicitly confirm a second time.
5. On confirmation, DeskAI captures a new Phase 19 snapshot and recomputes the fingerprint.
6. Any change in path existence, size, SHA-256, member state, linked-path state, backup/candidate/quarantine evidence, or transaction assessment makes the proposal stale.
7. Only an identical fingerprint may proceed to metadata reconciliation.

## Stable fingerprint

The fingerprint intentionally excludes volatile fields such as capture time and AuditLog display order.

It includes:

- transaction identity and family;
- Workspace id;
- persisted `recovery_required` status;
- transaction/single-vs-batch scope;
- every member id and tracked metadata state;
- every observed transaction-known path;
- existence / regular-file / symlink flags;
- file size and SHA-256;
- persisted-hash matches;
- observation errors;
- member disk state;
- transaction assessment and historical action readiness.

The fingerprint is SHA-256 over a canonical sorted JSON representation.

## Allowed reconciliation targets

### Source edit / source-edit batch

Only `consistent_applied` plus complete rollback artifacts may reconcile to:

`applied`

The reconciliation restores transaction/member status and the recorded applied SHA metadata. It does not modify the source file.

### File organization / organization batch

Only `consistent_applied` may reconcile to:

`applied`

The reconciliation restores proposal/batch status and aligns the DeskAI File record with the already-existing verified target path:

- path;
- filename;
- SHA-256;
- size.

No filesystem rename or move occurs during reconciliation.

### File recycle / recycle batch

Only `consistent_recycled` plus a verified quarantine copy may reconcile to:

`recycled`

The reconciliation restores proposal/batch status and DeskAI File status to `recycled`.

No file is copied, restored, removed, or purged during reconciliation.

## Batch atomicity

Batch reconciliation operates on the parent transaction only.

Confirmation verifies that:

- batch membership count is unchanged;
- snapshot member ids exactly match persisted batch members;
- the entire batch is in one verified actionable disk state.

All batch member statuses are reconciled inside one database transaction.

Single-member reconciliation remains blocked for batch members.

## Stale proposal behavior

If the recovery state changes after proposal creation:

- confirmation is rejected;
- the proposal becomes `stale`;
- an AuditLog record is created;
- the original transaction remains `recovery_required`;
- a new Phase 19 snapshot and a new Phase 20 proposal are required.

## Desktop UI

For an eligible Phase 19 snapshot, Recovery Center shows:

**Phase 20 · 受控状态核对**

The operator can:

- generate a state-reconciliation proposal;
- inspect target status;
- inspect the full snapshot fingerprint;
- see which original action will be reopened;
- explicitly confirm the metadata reconciliation;
- reject the proposal.

The confirmation text states that no user file is changed and that only the original rollback/restore path becomes available again.

## Audit trail

Phase 20 records:

- `recovery_reconciliation_proposed`;
- `recovery_reconciliation_confirmed`;
- `recovery_reconciliation_rejected`;
- `recovery_reconciliation_stale`.

Confirmed audit entries explicitly include:

`filesystem_mutation=false`

## Safety boundary

Phase 20 does not add:

- automatic reconciliation;
- automatic rollback or restore;
- force overwrite;
- ignore SHA;
- arbitrary transaction status selection;
- reconciliation to pending/rolled_back/restored;
- arbitrary path input;
- filesystem rename/move/copy/unlink;
- quarantine purge;
- backup deletion;
- member-only batch repair;
- Agent reconciliation tools.

All later file mutation remains exclusively on the original Phase 11–16 rollback/restore services and their existing SHA/no-overwrite/write-access gates.

## Verification targets

Phase 20 tests cover:

- two-step source-edit reconciliation;
- no file mutation during proposal or confirmation;
- original rollback availability after reconciliation;
- stale proposal rejection after disk state changes;
- file-organization File metadata reconciliation and subsequent original rollback;
- recycle File metadata reconciliation and subsequent original restore;
- atomic batch reconciliation;
- refusal to reconcile non-actionable consistent-original state;
- proposal rejection preserving `recovery_required`;
- duplicate proposal reuse for an unchanged snapshot;
- packaged Engine 0.20.0 exposure of reconciliation routes.

## Verification results

The first feature CI run (#100) intentionally exposed an API route ambiguity between proposal creation and proposal confirmation/rejection. The create route was changed to the static-prefixed transaction route, the eligibility check ordering was corrected, and the full verification suite was rerun.

Final feature PR #35 verification passed on CI Run #102:

- TypeScript typecheck — passed;
- Vite production build — passed;
- Ruff — passed;
- full Engine Pytest suite — **139 passed, 59 warnings**;
- Windows PyInstaller sidecar — passed;
- packaged Engine **0.20.0** health smoke — passed;
- packaged Recovery Snapshot route smoke — passed;
- packaged Recovery Reconciliation list/API smoke — passed;
- Tauri Windows NSIS build — passed;
- Tauri Windows MSI build — passed;
- Windows Artifact upload — passed.

Verified Windows installers:

- `DeskAI Work_0.1.0_x64-setup.exe`;
- `DeskAI Work_0.1.0_x64_en-US.msi`.

Verified Windows Artifact:

- name: `DeskAI-Work-Windows`;
- artifact id: `10289817242`;
- size: `394428600` bytes;
- SHA-256: `3dbfee745d0430ef60e1c32cd12f4711976e577711d80268ad2df7b97be6a582`.

Feature PR #35 was squash merged to `main` as:

`2b3908b121f0f9a8976c47f969a42de9bd3a27b1`

Successful CI Run #102:

`34668549449`

