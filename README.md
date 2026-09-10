# DeskAI Work

DeskAI Work is a Windows-first, local-first AI work assistant. It is designed to read only user-authorized local files, maintain persistent workspace data, and progressively add retrieval, memory, and auditable agent tools without weakening the local security boundary.

## Current implementation status

**Phase 0 and Phase 1 are complete. Phase 2 file-system work is implemented and is gated by CI before merge.**

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
- Windows CI that verifies engine tests, frontend type/build, packaged sidecar health, and native NSIS/MSI output

The current chat responder is still a local transport responder. DeskAI does **not** claim document understanding, RAG, OpenAI model responses, memory extraction, or agent execution yet.

## V1 phase boundaries

The implementation deliberately keeps major capabilities behind verified phase gates:

- **Phase 2 — File system:** Folder authorization, File Picker, Scanner, Watcher, SHA256, Index Queue.
- **Phase 3 — Parser:** TXT/MD, PDF, DOCX, XLSX, PPTX, CSV, then image parsing.
- **Phase 4 — Knowledge base:** Chunks, SQLite FTS5, embeddings, LanceDB, hybrid retrieval, citations.
- **Phase 5 — AI chat:** model provider and OpenAI Responses API integration.
- Later phases add durable memory and auditable agent execution.

See `docs/phase2-status.md` for the current file-system implementation.

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
- React does not receive cloud API secrets.
- Destructive file actions are not part of the current phase.
- Future document content will be treated as untrusted data, never as system instructions.

See `docs/security.md`, `docs/phase0-status.md`, and `docs/phase2-status.md`.
