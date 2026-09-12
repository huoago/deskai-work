# DeskAI Work

DeskAI Work is a Windows-first, local-first AI work assistant. It is designed to read only user-authorized local files, maintain persistent workspace data, and progressively add retrieval, memory, and auditable agent tools without weakening the local security boundary.

## Current implementation status

**Phase 0 through Phase 21 are complete, fully verified, and merged to `main`.**

Implemented foundations:

- Tauri 2 + React Windows desktop client
- Python FastAPI packaged sidecar engine
- SQLite + Alembic schema foundation and FTS5 schema groundwork
- per-launch loopback API session token between Tauri and FastAPI
- Workspace persistence and authorized local-folder boundaries
- persisted conversations and messages
- SSE streaming chat transport
- persisted desktop settings
- functional Chat / Workspace / Files / Settings navigation
- Folder Picker, recursive Scanner and SHA256 hashing
- background authorized-root Watcher
- new / changed / deleted / revoked file reconciliation
- persistent Index Queue and queue status UI
- manual rescan, watch pause/resume and authorization revoke controls
- background Parser Worker and local parsed-document cache
- TXT/MD, CSV, PDF, DOCX, XLSX, PPTX, JPG/PNG parser paths
- FileVersion rotation and parsed-text/locator preview UI
- structured Chunk generation with source locators
- SQLite FTS5 keyword retrieval and local LanceDB vector storage
- current-version-only hybrid retrieval with citation labels
- desktop local knowledge search page
- OpenAI Responses API streaming provider
- Hybrid local-RAG chat with persisted real citations
- OS-backed API credential storage and provider connection testing
- Local Only privacy enforcement with no cloud fallback
- persistent long-term Memory separated from document knowledge
- asynchronous Memory Learning Queue and Worker
- structured automatic extraction with confidence/sensitivity gates
- Memory correction/version history and Workspace isolation
- desktop Memory review/edit/deactivate/reactivate UI
- persistent Agent Task queue and restart recovery
- Responses function-calling Orchestrator with store=false
- read-only Tool Registry + Permission Gate
- ToolCall + AuditLog execution history
- desktop Tasks and Activity audit pages
- sandboxed generated-artifact registry
- Agent-created DOCX via python-docx
- Agent-created XLSX via openpyxl with formula-injection neutralization
- non-overwriting per-Task generated output directories
- desktop generated-artifact cards with local path, size and SHA-256
- bounded numeric expression evaluator with AST allow-list
- deterministic CSV/XLSX preview, summary statistics and grouped aggregation
- Workspace-scoped parsed-cache analysis without arbitrary Python execution
- provider-backed read-only public web research with preserved source URLs
- secret-like web query blocking and Local Only enforcement
- staged single-file TXT/MD/DOCX/XLSX source edits with explicit confirmation
- 2–10 file transactional edit batches with all-or-nothing confirmation
- batch-wide SHA/write/lock preflight, backups, failure rollback, and startup recovery
- staged single-file rename/move proposals inside the same authorized writable Workspace root
- no-overwrite target checks, SHA revalidation, Office lock checks, path rollback, and startup recovery for file organization
- 2–10 file transactional organization batches with one-shot confirmation, all-member preflight, failure rollback, and startup recovery
- recoverable single-file recycle proposals with private quarantine, SHA verification, restore, and startup recovery
- 2–10 file transactional recycle batches with all-member quarantine staging, all-or-nothing removal, rollback, and startup recovery
- no permanent recycle purge/delete endpoint in Phase 15/16
- unified read-only recovery aggregation across Phase 11–16 transactions with desktop rollback/restore controls that reuse the original safety APIs
- structured recovery diagnostics for recovery_required transactions with evidence, confidence, guided manual checks, and explicit prohibited actions
- on-demand read-only recovery snapshots with current path/SHA evidence, backup/quarantine verification, batch consistency assessment, and audit timeline recheck
- Windows CI that verifies engine tests, frontend type/build, packaged sidecar health, and native NSIS/MSI output

DeskAI can now generate grounded model answers, maintain durable long-term memory, execute audited Agent tasks, create new Word/Excel work artifacts, perform deterministic local calculations and CSV/XLSX analysis, conduct source-preserving public web research, stage confirmed edits to existing TXT/MD/DOCX/XLSX files, stage coordinated 2–10 file all-or-nothing edit transactions, stage confirmed single-file rename/move operations inside one authorized writable Workspace root, stage 2–10 file all-or-nothing organization transactions, stage recoverable file recycle proposals, stage 2–10 file all-or-nothing recycle transactions, centrally review/recover previously applied transactions from one Recovery Center, diagnose recovery_required states without weakening the original transaction safety gates, and recheck their current disk evidence through an on-demand read-only recovery snapshot. Source content or paths are changed only after explicit desktop confirmation with write permission, SHA-256 revalidation, lock checks, no-overwrite rules, rollback safeguards, and audit. It still does **not** claim neural semantic embeddings, local LLM execution, arbitrary Python/shell execution, arbitrary URL fetching/downloading, GUI/browser control, permanent file deletion/purge, directory operations, cross-root moves, target overwrite, or unattended destructive file operations.

## V1 phase boundaries

The implementation deliberately keeps major capabilities behind verified phase gates:

- **Phase 2 — File system:** Folder authorization, File Picker, Scanner, Watcher, SHA256, Index Queue.
- **Phase 3 — Parser:** implemented for TXT/MD, CSV, PDF, DOCX, XLSX, PPTX and JPG/PNG metadata; gated by CI before merge.
- **Phase 4 — Knowledge base:** complete with Chunks, SQLite FTS5, deterministic local vectors, LanceDB, hybrid retrieval, and source citation labels.
- **Phase 5 — AI chat:** complete with secure provider configuration, Responses API streaming, local RAG grounding, conversation history, and persisted citations.
- **Phase 6 — Durable memory:** complete with persistent learning jobs, Structured Output extraction, sensitivity gates, correction/version history, retrieval injection, and Memory UI.
- **Phase 7 — Agent execution:** complete with persistent Tasks, restart recovery, Responses function calling, a read-only Tool Registry, Permission Gate, ToolCall/AuditLog persistence, and Tasks/Activity UI.
- **Phase 8 — Safe artifacts:** complete with a generated-artifact registry, sandboxed DOCX/XLSX creation, path traversal protection, no-overwrite semantics, spreadsheet formula-injection neutralization, and artifact UI.
- **Phase 9 — Controlled analysis:** complete with a bounded AST numeric evaluator plus Workspace-scoped CSV/XLSX inspect, summary, and grouped aggregation tools.
- **Phase 10 — Controlled web research:** complete with provider-backed public web search, source URL preservation, secret-query filtering, Local Only enforcement, and L2 audit gating.
- **Phase 11 — Confirmed source edits:** complete with staged TXT/MD/DOCX/XLSX source edits, write authorization, explicit confirmation, SHA protection, backup, reindexing, and rollback.
- **Phase 12 — Transactional multi-file edits:** complete with 2–10 file all-or-nothing edit batches, batch-wide preflight, backup, failure rollback, startup recovery, and Windows verification.
- **Phase 13 — Controlled file organization:** complete with proposal-only single-file rename/move, same-root enforcement, extension preservation, no-overwrite semantics, SHA/lock revalidation, same-File-id path updates, rollback, startup recovery, 81 passing Engine tests, packaged Engine 0.13.0 smoke verification, and verified Windows installers.
- **Phase 14 — Transactional file organization:** complete with 2–10 independent rename/move proposals, unique absent targets, all-member preflight, single-confirm all-or-nothing execution, failure rollback, batch startup recovery, batch rollback, 90 passing Engine tests, packaged Engine 0.14.0 smoke verification, and verified Windows installers.
- **Phase 15 — Controlled recycle bin:** complete with proposal-only single-file recycle, verified private quarantine copy before source removal, post-copy source SHA revalidation, quarantine sandbox containment, recycled metadata state, restore-to-original with no-overwrite semantics, startup recovery, cross-tool mutation exclusivity, no permanent purge capability, 102 passing Engine tests, packaged Engine 0.15.0 smoke verification, and verified Windows installers.
- **Phase 16 — Transactional batch recycle:** complete with 2–10 unique files, all-member preflight, all quarantine copies verified before the first Workspace original is removed, post-staging SHA revalidation, all-or-nothing recycle/restore rollback, startup recovery, unexpected-content recovery freeze, no permanent purge capability, 115 passing Engine tests, packaged Engine 0.16.0 smoke verification, and verified Windows installers.
- **Phase 17 — Unified recovery center:** complete with read-only Workspace-scoped aggregation of Phase 11–16 applied/recycled/recovery-required transactions, desktop recoverable/attention/history filters, rollback/restore buttons that reuse the original transaction APIs, 120 passing Engine tests, packaged Engine 0.17.0 recovery smoke verification, and verified Windows installers.
- **Phase 18 — Recovery diagnostics & guided repair:** complete with deterministic classification of recovery_required states, structured evidence/confidence, guided manual verification steps, prohibited-action guardrails, no new mutation endpoint, no automatic force repair, 125 passing Engine tests, packaged Engine 0.18.0 recovery smoke verification, and verified Windows NSIS/MSI installers.
- **Phase 19 — Recovery snapshot & safe recheck:** complete with an on-demand read-only snapshot endpoint for recovery_required transactions, current path existence/size/SHA inspection, source-edit backup/candidate verification, recycle quarantine verification, organization source/target reconciliation, batch consistency assessment, audit timeline evidence, symlink/junction-component non-following, no automatic transaction-state mutation, 131 passing Engine tests, packaged Engine 0.19.0 snapshot-route smoke verification, and verified Windows NSIS/MSI installers.
- **Phase 20 — Controlled recovery state reconciliation:** complete with persisted two-step reconciliation proposals, canonical recovery-snapshot fingerprints, confirmation-time rechecks, stale-proposal invalidation, metadata-only unfreezing to applied/recycled, atomic batch reconciliation, audit records, no user-file mutation, 139 passing Engine tests, packaged Engine 0.20.0 reconciliation smoke verification, and verified Windows NSIS/MSI installers.
- **Phase 21 — Recovery evidence export & incident package:** complete with metadata-only ZIP evidence bundles, Phase 18 diagnostics, live Phase 19 snapshots for frozen transactions, Phase 20 reconciliation history, Task audit timelines, per-file package integrity hashes, GeneratedArtifact sandbox output, no Workspace file bytes, no user-file mutation, 143 passing Engine tests, packaged Engine 0.21.0 evidence-route smoke verification, and verified Windows NSIS/MSI installers.

See `docs/phase2-status.md`, `docs/phase3-status.md`, `docs/phase4-status.md`, `docs/phase5-status.md`, `docs/phase6-status.md`, `docs/phase7-status.md`, `docs/phase8-status.md`, `docs/phase9-status.md`, `docs/phase10-status.md`, `docs/phase11-status.md`, `docs/phase12-status.md`, `docs/phase13-status.md`, `docs/phase14-status.md`, `docs/phase15-status.md`, `docs/phase16-status.md`, `docs/phase17-status.md`, `docs/phase18-status.md`, `docs/phase19-status.md`, `docs/phase20-status.md`, and `docs/phase21-status.md` for the current implementation.

## Repository layout

```text
apps/desktop        Tauri 2 + React desktop client
apps/engine         FastAPI local AI engine
packages/prompts    versioned system prompts for later agent phases
packages/shared-types cross-layer contracts
docs                architecture/security/database/phase status docs
scripts             local and CI build helpers
tests               end-to-end fixtures as later phases land
```

## Local backend development

Python 3.12+ is supported.

```powershell
cd apps/engine
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m alembic upgrade head
python main.py --port 8765
```

Then open `http://127.0.0.1:8765/health`.

Phase 2 Watcher behavior can be configured for development with:

```text
DESKAI_WATCHER_ENABLED=1
DESKAI_WATCHER_INTERVAL=2.0
```

## Desktop development

Requirements on Windows:

- Node.js 22+
- Rust stable + MSVC build tools
- WebView2
- Python 3.12+

```powershell
npm install
powershell -ExecutionPolicy Bypass -File scripts/build-engine.ps1
npm run dev
```

The Tauri app starts the bundled `deskai-engine` sidecar on a free loopback port. The frontend asks the Rust host for that endpoint and then communicates with the engine using the per-launch session token.

## Security baseline

- Engine listens on `127.0.0.1` only.
- No local folder is trusted until explicitly stored as a readable Workspace root.
- Scanner/Watcher access remains within authorized roots and skips symlink escapes.
- Revoking a folder never deletes the user's local files.
- React never receives a saved cloud API secret back from the Engine.
- User-entered OpenAI credentials use the OS-backed credential store rather than SQLite or repository files.
- Hybrid mode sends only the user request, bounded conversation history, and locally retrieved relevant snippets to the cloud model.
- Responses requests use `store=false`; DeskAI keeps conversation state locally.
- Retrieved document content is treated as untrusted data, never as system instructions.
- Memory candidates are independently filtered for credentials and sensitive personal information.
- Memory corrections preserve version history instead of silently erasing the previous value.
- Agent read tools remain Workspace-scoped.
- Phase 8 adds only DOCX/XLSX creation inside DeskAI's private generated/<task_id> sandbox.
- Generated artifact absolute paths remain local and are not returned to the cloud model.
- Existing generated filenames are never overwritten.
- Formula-like spreadsheet strings are neutralized before writing.
- Phase 9 calculations use an AST allow-list and never expose general Python exec/import/file/network/process access.
- CSV/XLSX analysis accepts only Workspace File ids and reads parsed cache rather than arbitrary paths.
- Phase 10 web research uses the configured AI provider's hosted web-search capability, preserves source URLs, rejects credential-like queries, and remains unavailable in Local Only mode.
- Web research cannot fetch arbitrary URLs, download files, control a browser, execute webpage instructions, or access local files by itself.
- Every Agent tool attempt is persisted as ToolCall/AuditLog before it can be represented as completed work.
- Unknown or destructive tool names are denied by default.
- Phase 13/14 provide confirmed same-root rename/move only. Phase 15 adds recoverable recycle-to-quarantine for one file after explicit confirmation; permanent purge, directory operations, cross-root moves, extension changes, target overwrite, and arbitrary destination writes remain unavailable.
- Phase 18 recovery diagnostics are read-only derivations from persisted transaction state. They never alter files, backups, quarantine copies, hashes, Workspace permissions, or transaction status.
- Phase 19 recovery snapshots are requested manually, inspect only transaction-known paths, refuse to follow symlinks, and never persist the snapshot or clear recovery_required.
- Phase 20 reconciliation requires a persisted proposal plus a second human confirmation, re-captures and fingerprints the current snapshot, marks changed proposals stale, and only updates transaction/File metadata to reconnect a verified applied/recycled state to the original rollback/restore API.
- Phase 21 recovery evidence export writes only metadata/diagnostic/snapshot/audit ZIPs to the generated-artifact sandbox; it never embeds Workspace, candidate, backup, or quarantine file bytes and never changes recovery state.

See `docs/security.md`, `docs/phase0-status.md`, `docs/phase2-status.md`, and `docs/phase5-status.md`.
