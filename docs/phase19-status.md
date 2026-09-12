# Phase 19 — Recovery snapshot & safe recheck

Status: complete, fully verified, and merged to `main`.

## Objective

Phase 18 made `recovery_required` understandable from persisted transaction evidence. Phase 19 adds an operator-triggered recheck of the current disk state so the operator can compare the stored diagnosis with what actually exists now.

The snapshot is deliberately ephemeral and read-only. It does not update the transaction, repair files, clear `recovery_required`, or authorize the historical rollback/restore action.

## Read-only snapshot API

### GET /recovery/{entity_type}/{transaction_id}/snapshot

The endpoint is available only for persisted transactions whose authoritative status is `recovery_required`.

Supported transaction families:

- single source edits;
- source-edit batches;
- single file-organization transactions;
- file-organization batches;
- single recycle transactions;
- recycle batches.

The endpoint performs no database mutation and exposes no POST/PATCH/DELETE recovery-snapshot surface.

## Snapshot evidence

For the transaction's already-known paths, the snapshot records:

- path role;
- current existence;
- regular-file status;
- symlink/link-component status;
- current file size;
- current SHA-256 when safe to calculate;
- which persisted transaction hashes the current file matches;
- read/OS errors without changing the file.

Source edits recheck:

- current Workspace source;
- staged candidate;
- automatic backup;
- original / candidate / applied hashes.

File organization rechecks:

- original path;
- target path;
- original/applied content hash.

Recycle rechecks:

- Workspace original path;
- private quarantine copy;
- original hash and size.

## Linked-path boundary

Before hashing a transaction-known path, Phase 19 checks the path chain from its parents to the final component.

If any component is a symbolic link or Windows junction, the snapshot reports the linked component and stops. It does not continue through the linked directory and does not hash the eventual target.

This prevents the diagnostic surface from becoming an arbitrary path-reading mechanism after an external filesystem change.

## Safe recheck assessment

Each member is classified into a transaction-specific disk state.

Examples include:

- original;
- applied;
- recycled;
- restored;
- conflict;
- missing;
- unknown;
- original-without-quarantine.

The transaction is then classified as:

- consistent original;
- consistent applied;
- consistent recycled;
- consistent restored;
- mixed transaction;
- ambiguous.

The response also distinguishes:

- `safe_state_detected`;
- whether the historical action's technical artifacts are present;
- the historical action that could become relevant after controlled state reconciliation;
- `safe_to_retry_existing_action: false`.

The last value intentionally remains false because the persisted `recovery_required` status is still authoritative.

## Audit timeline

The snapshot includes up to 25 recent AuditLog records for the originating Task so current disk evidence can be read alongside the transaction timeline.

## Desktop UI

Each `recovery_required` card exposes **重新检测磁盘状态**.

The result shows:

- snapshot capture time;
- overall disk-state assessment;
- safe/inconsistent badge;
- technical historical-action preconditions;
- each member's observed state;
- each known path with current file/hash evidence;
- persisted-hash matches;
- audit timeline;
- an explicit warning that the snapshot does not clear the recovery freeze.

## Safety boundary

Phase 19 does not add:

- force overwrite;
- ignore SHA;
- automatic restore/rollback;
- transaction-state reconciliation;
- backup deletion;
- quarantine purge;
- target overwrite;
- batch-member-only repair;
- arbitrary path input;
- symlink/junction following;
- snapshot persistence.

Only the original Phase 11–16 transaction services may mutate files.

## Verification matrix

Phase 19 tests cover:

- source-edit recovery snapshot in a consistent applied state;
- backup + candidate evidence required for a technically rollbackable historical state;
- recycle recheck changing from consistent recycled to consistent restored after an external/manual disk change while persisted status stays frozen;
- file-organization dual-path conflict detection;
- mixed-state transactional batch detection;
- refusal to snapshot non-recovery transactions;
- read-only GET-only API surface;
- parent symlink non-following;
- packaged Engine route exposure.

### Feature verification

Feature PR #33 passed the complete CI pipeline on Run #96 before merge:

- TypeScript typecheck — passed;
- Vite production build — passed;
- Ruff — passed;
- full Engine Pytest suite — **131 passed, 59 warnings**;
- Windows PyInstaller sidecar — passed;
- packaged Engine **0.19.0** health smoke — passed;
- packaged `/recovery` API smoke — passed;
- packaged Recovery Snapshot OpenAPI route smoke — passed;
- Tauri Windows NSIS build — passed;
- Tauri Windows MSI build — passed;
- Windows Artifact upload — passed.

Verified Windows installers:

- `DeskAI Work_0.1.0_x64-setup.exe`;
- `DeskAI Work_0.1.0_x64_en-US.msi`.

Verified Windows Artifact:

- name: `DeskAI-Work-Windows`;
- artifact id: `10287573018`;
- size: `394399005` bytes;
- SHA-256: `541da4838c97de6c55155ce260a4c9b9067f370f1fef22e5b821405fc4261b54`.

Feature PR #33 was squash merged to `main` as:

`158834a7ff6b1f0eaf7dc52e9965939f5e0de684`

CI Run #96:

`34661769005`
