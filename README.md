# DeskAI Work

DeskAI Work is a Windows-first, local-first AI work assistant. It is designed to read only user-authorized local files, maintain persistent workspace data, and progressively add retrieval, memory, and auditable agent tools without weakening the local security boundary.

## Current implementation status

**Phase 0 through Phase 4 are complete. Phase 5 grounded AI chat is implemented and is gated by CI before merge.**

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
- Windows CI that verifies engine tests, frontend type/build, packaged sidecar health, and native NSIS/MSI output

DeskAI can now generate grounded model answers from locally retrieved Workspace context with persisted source citations. It still does **not** claim neural semantic embeddings, autonomous memory extraction, local LLM execution, or agent/tool execution yet.

## V1 phase boundaries

The implementation deliberately keeps major capabilities behind verified phase gates:

- **Phase 2 — File system:** Folder authorization, File Picker, Scanner, Watcher, SHA256, Index Queue.
- **Phase 3 — Parser:** implemented for TXT/MD, CSV, PDF, DOCX, XLSX, PPTX and JPG/PNG metadata; gated by CI before merge.
- **Phase 4 — Knowledge base:** complete with Chunks, SQLite FTS5, deterministic local vectors, LanceDB, hybrid retrieval, and source citation labels.
- **Phase 5 — AI chat:** implemented with secure provider configuration, Responses API streaming, local RAG grounding, conversation history, and persisted citations; gated by CI before merge.
- Later phases add durable memory and auditable agent execution.

See `docs/phase2-status.md`, `docs/phase3-status.md`, `docs/phase4-status.md`, and `docs/phase5-status.md` for the current implementation.

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
- Destructive file actions are not part of the current phase.

See `docs/security.md`, `docs/phase0-status.md`, `docs/phase2-status.md`, and `docs/phase5-status.md`.
