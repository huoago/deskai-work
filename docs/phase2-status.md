# Phase 2 — File system gate

Status: implemented and pending final native Windows CI verification on PR #4.

## Scope

Phase 2 establishes the complete local file-system ingestion boundary before any document parsing begins. The implemented scope is intentionally limited to:

- user-authorized workspace folders selected from the desktop File Picker
- persisted workspace root permissions
- recursive Scanner with exclusion rules
- SHA256 file hashing
- background Watcher for authorized roots
- new / changed / deleted / re-authorized file reconciliation
- persistent Index Queue creation and queue-state reporting
- manual rescan controls
- per-root watch pause/resume
- root authorization revocation without deleting local files

Document content parsing is not part of this phase. Parser work starts in Phase 3.

## Accepted V1 file extensions

The Phase 2 scanner recognizes only the V1 acceptance formats:

- PDF
- DOCX
- XLSX
- PPTX
- TXT
- MD
- CSV
- JPG
- PNG

Other formats are recorded as unsupported instead of being silently treated as indexable. Automatic indexing is capped at 500 MiB per file.

## Security boundary

- Scanner and Watcher operate only under roots explicitly authorized for the current Workspace.
- Symbolic-link escapes are skipped.
- Common generated/system directories such as `.git`, `node_modules`, virtual environments, build output and caches are excluded.
- Executables and system binaries are not accepted for indexing.
- Revoking a root changes DeskAI metadata and cancels active index jobs; it never deletes or modifies the user's local file.
- Files no longer covered by any readable authorized root are marked `revoked`.
- Files removed from disk are marked `deleted`.

## Watcher design

`WorkspaceWatcher` is a cross-platform polling watcher owned by the FastAPI engine lifespan. It is intentionally conservative for V1:

1. load roots where `read_allowed=true` and `watch_enabled=true`;
2. use size + modified-time metadata to avoid unnecessary hashing when a file is clearly unchanged;
3. compute SHA256 whenever a file may have changed;
4. reconcile file state in SQLite;
5. enqueue changed files only when desktop `auto_index` is enabled.

A manual scan always performs the stronger full-hash reconciliation and can enqueue a previously discovered pending file even when automatic enqueueing was disabled.

## Index Queue behavior

- queued jobs are deduplicated per file;
- an existing queued job is reused rather than duplicated;
- if a previous version is already processing and the file changes, a new queued job may be created for the latest file state;
- deletion, unsupported transitions and authorization revocation cancel active jobs;
- `/index-jobs/summary` exposes queue counts for the desktop UI.

The queue only represents work to be processed. Phase 3 adds document parsers and the actual queue consumer.

## Desktop UI

The Workspace and Files pages now expose:

- authorized roots
- Watcher running/paused state
- root watch controls
- root authorization revocation
- manual rescan
- discovered file count
- pending/index queue count
- file status
- active queue status
- abbreviated SHA256 fingerprint

Settings retain `auto_index`, now wired to the Watcher enqueue policy.

## API additions

- `PATCH /workspaces/{workspace_id}/roots/{root_id}`
- `DELETE /workspaces/{workspace_id}/roots/{root_id}`
- `POST /workspaces/{workspace_id}/scan`
- `GET /workspaces/{workspace_id}/watcher`
- `GET /index-jobs/summary?workspace_id=...`

Existing `/files` and `/index-jobs` responses now include queue and file metadata needed by the Phase 2 UI.

## Verification

Phase 2 adds backend coverage for:

- Watcher discovery and SHA256 hashing
- automatic enqueueing
- `auto_index=false`
- manual scan enqueue of pending files
- watch pause/resume
- authorization revocation
- active-job cancellation
- V1 file-format boundary

The repository CI gate remains unchanged: Ruff, Pytest, TypeScript typecheck, Vite web build, packaged PyInstaller sidecar smoke test, and native Tauri Windows NSIS/MSI build must all succeed before Phase 2 is merged.
