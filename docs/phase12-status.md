# Phase 12 — Transactional multi-file source editing

Status: complete, verified by full CI, and merged to `main`.

## Objective

Phase 12 lets one Agent task propose a coordinated change to 2–10 existing Workspace files and lets the user review and confirm that change as a single all-or-nothing transaction.

Supported member types remain:

- TXT
- Markdown
- DOCX
- XLSX

The phase deliberately does not add delete, move, rename, arbitrary-path writes, shell execution, unrestricted Python, or browser control.

## Agent tool

### propose_source_file_edit_batch

Risk level: L3.

The model may stage one batch containing 2–10 unique file ids. Each member uses the Phase 11 edit modes:

- `text_replace`;
- `docx_replace`;
- `xlsx_cells`.

The tool only creates private candidates and a batch record. It never modifies source files.

## Confirmation model

A batch is reviewed as one card in the desktop Tasks UI.

The UI exposes only whole-batch actions:

- Confirm entire batch;
- Reject entire batch;
- Roll back entire batch after apply.

Batch members are hidden from the single-file confirmation workflow and the Engine rejects attempts to act on them through the single-file API.

## Apply transaction

Before any source mutation, DeskAI validates every member:

- Workspace ownership and current write permission;
- source path remains inside the authorized root;
- source is not a symlink;
- original SHA-256 is still current;
- candidate SHA-256 is intact;
- source can be opened for writing;
- DOCX/XLSX has no Office `~$` lock file;
- verified original backup exists for every member.

After all checks pass, the batch enters `applying`.

Members are replaced with the existing sibling-temp + `os.replace` mechanism.

If any write or verification fails, already-written members are restored from backups in reverse order. A completely restored failed attempt returns to `pending`; an incomplete recovery enters `recovery_required`.

## Rollback transaction

Rollback requires every current source to still equal the exact SHA-256 applied by DeskAI and every original backup/candidate to remain valid.

If rollback fails after restoring some members, DeskAI attempts to reapply their candidates so the batch returns to its previous applied state.

## Startup recovery

Engine startup calls batch recovery before Agent workers start.

Batches left in `applying` or `rolling_back` are inspected member by member.

Automatic recovery only acts when a member's current SHA is exactly the original or candidate SHA. Unknown external changes freeze the batch as `recovery_required` rather than being overwritten.

## Auditing

Batch lifecycle events are recorded in AuditLog with higher risk levels for source mutation:

- `source_edit_batch_applied` — L6;
- `source_edit_batch_rolled_back` — L6;
- `source_edit_batch_apply_failed_restored` — L6;
- `source_edit_batch_rollback_failed_reapplied` — L6;
- `source_edit_batch_startup_recovered` — L6;
- `source_edit_batch_recovery_required` — L7.

## Verification

Phase 12 tests cover:

- two-file stage/confirm/rollback;
- batch members cannot be individually confirmed;
- one stale member blocks the whole batch before any write;
- simulated second-file write failure restores the already-written first file;
- Microsoft Office lock-file detection blocks the whole batch;
- startup recovery restores an interrupted applying transaction;
- Agent can only create a pending transactional batch;
- duplicate file ids are rejected.

### Merge verification

Feature PR #16 passed the complete CI pipeline on Run #63 before merge:

- TypeScript typecheck — passed;
- Vite production build — passed;
- Ruff — passed;
- full Engine Pytest suite — **71 passed, 59 warnings**;
- Windows PyInstaller sidecar — passed;
- packaged Engine **0.12.0** health smoke — passed;
- packaged `/file-edit-batches` API smoke — passed;
- Tauri Windows NSIS/MSI build — passed;
- Windows Artifact upload — passed.

Feature PR #16 was squash merged to `main` as commit:

`3f64282b39329826fb950a87f7dc516e2cdfe2d4`

Verified Windows Artifact:

- name: `DeskAI-Work-Windows`;
- artifact id: `10275455118`;
- size: `394176321` bytes;
- SHA-256: `1b38a732be6170143b260ef33f84eb49466bb2bcd3b2bc709da3dfdc0c103846`.
