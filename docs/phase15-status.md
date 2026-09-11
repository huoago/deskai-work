# Phase 15 — Controlled recycle bin

Status: complete, verified by full CI, and merged to `main`.

## Objective

Phase 15 introduces a recoverable meaning of “delete” for one Workspace file.

The user can ask DeskAI to remove a file from the active Workspace, but DeskAI does not permanently destroy it.

Instead:

1. Agent stages a recycle proposal;
2. user explicitly confirms in the desktop UI;
3. DeskAI creates and SHA-verifies a private quarantine copy;
4. DeskAI revalidates the source SHA;
5. only then is the Workspace original removed;
6. the file can later be restored to the original path.

Permanent quarantine purge is intentionally unavailable.

## Agent tool

### propose_file_recycle

Risk level: L4.

The tool creates only a proposal. It cannot remove the source file.

The Agent must not claim permanent deletion.

## Metadata model

A recycle proposal stores:

- task / Workspace / File ids;
- original path;
- private quarantine path;
- original SHA-256;
- original size;
- previous File status;
- lifecycle timestamps and error state.

The existing File row is preserved.

After confirmed recycle, `File.status` becomes `recycled`. The original path remains stored so restore does not create a new File identity.

## Recycle safety

Confirmed recycle requires:

- containing root remains writable;
- current path still matches the proposal;
- source is not a symlink;
- source SHA-256 has not changed;
- source is not being parsed/indexed;
- source is writable/not Office-locked.

DeskAI then copies to private quarantine and verifies SHA-256.

The source SHA is recomputed **after quarantine preparation and immediately before source removal** to reduce time-of-check/time-of-use risk.

If quarantine preparation or verification fails, the source remains.

The private recycle sandbox is also containment-checked: the recycle root itself may not be a symlink, resolved quarantine paths must remain inside DeskAI's private data directory, and quarantine files may not be symlinks.

## Restore safety

Restore requires:

- verified quarantine copy;
- original root has write permission;
- original parent exists and is writable;
- original path is unoccupied.

Restore uses a temporary copy in the original directory and no-overwrite final creation.

The quarantine copy remains after restore.

## Scanner / retrieval behavior

`recycled` files are preserved as a distinct scanner state and are not converted to generic `deleted`.

Authorization revoke/reconcile does not erase recycled state.

Hybrid retrieval requires `File.status == "indexed"`, so recycled files are excluded from local RAG immediately while their FileVersion/Chunk history remains intact.

## Startup recovery

Engine startup inspects `recycling` and `restoring` proposals.

Safe exact-hash states are finalized or returned to the previous recoverable state.

Ambiguous states enter `recovery_required`.

## Verification matrix

Phase 15 tests cover:

- proposal creates zero filesystem mutation;
- explicit confirm creates verified quarantine then removes source;
- restore preserves File id and SHA;
- quarantine copy remains after restore;
- write permission required;
- stale source blocks recycle;
- quarantine verification failure never removes source;
- source is revalidated after quarantine copy;
- Microsoft Office lock blocks recycle;
- occupied original path blocks restore;
- Workspace scan preserves recycled state;
- interrupted recycle after source removal finalizes recycled;
- interrupted early recycle returns to pending with original intact;
- Agent can only stage a recoverable proposal;
- permanent-delete API is absent.

### Merge verification

Feature PR #22 passed the complete CI pipeline on Run #78 before merge:

- TypeScript typecheck — passed;
- Vite production build — passed;
- Ruff — passed;
- full Engine Pytest suite — **102 passed, 59 warnings**;
- Windows PyInstaller sidecar — passed;
- packaged Engine **0.15.0** health smoke — passed;
- packaged `/recycle-proposals` API smoke — passed;
- Tauri NSIS build — passed;
- Tauri MSI build — passed;
- Windows Artifact upload — passed.

Verified Windows installers:

- `DeskAI Work_0.1.0_x64-setup.exe`;
- `DeskAI Work_0.1.0_x64_en-US.msi`.

Feature PR #22 was squash merged to `main` as commit:

`66a0edeeb022e32fa9847bfeef38c32e0f6667c5`

Verified Windows Artifact:

- name: `DeskAI-Work-Windows`;
- artifact id: `10279545058`;
- size: `394311515` bytes;
- SHA-256: `16e8714bc104c13157b3fda96581fcc7afea5a6931b8cc4efae6d3d766b8643e`.
