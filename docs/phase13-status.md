# Phase 13 — Controlled file organization

Status: complete, verified by full CI, and merged to `main`.

## Objective

Phase 13 lets DeskAI stage and, after explicit desktop confirmation, perform a safe single-file rename or move without breaking the existing File id / version relationships.

The phase deliberately remains narrower than a general file manager.

Supported operations:

- rename one file in its current directory;
- move one file to an existing subdirectory of the same authorized writable Workspace root;
- user-triggered rollback to the original path.

Not supported:

- delete;
- directory operations;
- target overwrite;
- cross-root / cross-Workspace move;
- cross-device move;
- extension-changing rename;
- arbitrary shell/filesystem commands.

## Agent tool

### propose_file_organization

Risk level: L3.

The Agent may only stage a proposal with:

- `file_id`;
- operation: `rename` or `move`;
- summary;
- `new_name` for rename;
- `target_relative_dir` for move.

The model never receives an apply/rollback tool.

## Path safety

Every proposal is Workspace-scoped and requires the containing root to be both readable and writable.

Rename validates Windows-compatible filenames and preserves the source extension.

Move normalizes a relative target directory and requires it to resolve inside the exact same Workspace root.

Target directories must already exist.

Targets must not exist; Phase 13 has no overwrite mode.

Source and target parent must be on the same filesystem/device.

## Confirmation flow

Before apply, DeskAI revalidates:

- source path has not moved since proposal;
- source SHA-256 has not changed;
- Workspace write permission still exists;
- target is still absent;
- target directory is writable;
- source is not in an active processing job;
- source is writable;
- Office lock file is absent for DOCX/XLSX.

The proposal enters `applying` before the filesystem path change.

After success, the same File row is updated in place with its new path/filename while retaining the same File id and content hash.

An L5 audit event records the path change.

## Rollback

Rollback is allowed only when current content still matches the exact applied hash and the original path remains free.

The proposal enters `rolling_back` before the reverse path change.

If the file was edited externally after the move/rename, automatic rollback is blocked.

## Recovery

Engine startup inspects incomplete `applying` and `rolling_back` proposals.

When exactly one path contains the expected file SHA, DeskAI can safely finalize or restore the recorded state.

Ambiguous / corrupted states become `recovery_required` and require human inspection.

## Verification matrix

Phase 13 tests cover:

- rename stages without mutation;
- explicit confirmation applies rename;
- File id and content SHA are preserved;
- rollback restores original path;
- write permission is required;
- extension-changing rename is rejected;
- target collision is rejected;
- stale external source modification blocks confirm;
- move into an existing same-root directory succeeds;
- path traversal / cross-root intent is rejected;
- Microsoft Office lock blocks confirm;
- occupied original path blocks rollback;
- interrupted apply is completed by startup recovery;
- Agent can only create a pending proposal.

### Merge verification

Feature PR #18 passed the complete CI pipeline on Run #68 before merge:

- TypeScript typecheck — passed;
- Vite production build — passed;
- Ruff — passed;
- full Engine Pytest suite — **81 passed, 59 warnings**;
- Windows PyInstaller sidecar — passed;
- packaged Engine **0.13.0** health smoke — passed;
- packaged `/file-operations` API smoke — passed;
- Tauri Windows NSIS/MSI build — passed;
- Windows Artifact upload — passed.

Feature PR #18 was squash merged to `main` as commit:

`55de643b0a8681e2ff3c07a78fef4029c06310e2`

Verified Windows Artifact:

- name: `DeskAI-Work-Windows`;
- artifact id: `10276531241`;
- size: `394223506` bytes;
- SHA-256: `54693384f81dc914f2d6af33cd7637f388da84dbbf24a9d3da76be9497d4908a`.
