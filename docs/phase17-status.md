# Phase 17 — Unified recovery center

Status: complete, fully verified, and merged to `main`.

## Objective

Phase 17 turns the recovery capabilities accumulated in Phase 11–16 into one operator-facing Workspace view.

The goal is not to add a new recovery algorithm.

The goal is to make existing, already verified rollback/restore paths discoverable and auditable from one place.

## Read-only aggregation API

### GET /recovery

The endpoint aggregates six persisted transaction families:

- single source edits;
- source-edit batches;
- single file-organization transactions;
- file-organization batches;
- single recycle proposals;
- recycle batches.

The endpoint is Workspace-scoped and globally sorted by the latest meaningful transaction timestamp.

Batch member rows are suppressed when the batch itself is present, so the UI cannot accidentally offer a single-member action against an atomic transaction.

Ordinary pending/rejected proposals are excluded.

## Recovery entry contract

Each entry contains:

- transaction id and type;
- single/batch scope;
- task / Workspace ids;
- status;
- summary;
- created / latest-state timestamps;
- persisted error message;
- file count;
- filenames;
- relevant original/target paths where available;
- recoverable action: `rollback`, `restore`, or none;
- `recovery_required` flag;
- transactional flag.

## Desktop Recovery Center

The desktop navigation adds **恢复中心**.

The page provides four views:

- 可恢复 — currently eligible for rollback/restore;
- 需人工处理 — `recovery_required` only;
- 历史 — already rolled back/restored or otherwise non-actionable recovery history;
- 全部.

Recoverable entries expose exactly one context-appropriate action.

Content/path transactions call their original rollback API.

Recycle transactions call their original restore API.

No Phase 17-specific mutation endpoint exists.

## Safety boundary

Phase 17 does not weaken prior phase gates.

A recovery request must still pass the original transaction service's:

- Workspace write permission;
- current SHA checks;
- no-overwrite checks;
- backup/quarantine validation;
- transactional membership validation;
- Office-lock checks where applicable;
- rollback/recovery state validation.

A `recovery_required` item never receives a force-action button.

The UI only links back to its original Task for investigation.

## Verification matrix

Phase 17 tests cover:

- aggregation of single + batch transaction families;
- batch-member deduplication;
- rollback mapping for content/path transactions;
- restore mapping for recycle transactions;
- recovery_required entries are non-actionable;
- pending/rejected proposals are excluded;
- Workspace scope is forwarded to all six underlying services;
- global sort + limit across transaction families;
- `/recovery` has no POST or DELETE mutation surface.

### Merge verification

Feature PR #29 passed the complete CI pipeline on Run #88 before merge:

- TypeScript typecheck — passed;
- Vite production build — passed;
- Ruff — passed;
- full Engine Pytest suite — **120 passed, 59 warnings**;
- Windows PyInstaller sidecar — passed;
- packaged Engine **0.17.0** health smoke — passed;
- packaged `/recycle-batches` API smoke — passed;
- packaged `/recovery` API smoke — passed;
- Tauri Windows NSIS build — passed;
- Tauri Windows MSI build — passed;
- Windows Artifact upload — passed.

Verified Windows installers:

- `DeskAI Work_0.1.0_x64-setup.exe`;
- `DeskAI Work_0.1.0_x64_en-US.msi`.

Feature PR #29 was squash merged to `main` as:

`bf6b9f75a8a9256fc90fb231ec1341793a91901c`

Verified Windows Artifact:

- name: `DeskAI-Work-Windows`;
- artifact id: `10286965190`;
- size: `394369343` bytes;
- SHA-256: `be1d550728eb8c8714ccf355f6e7c851ecb38dfb4b54cf40015445eb71deaddb`.
