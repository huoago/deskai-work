# DeskAI Work

DeskAI Work is a Windows-first, local-first AI work assistant. It is designed to read only user-authorized local files, maintain a persistent knowledge base and memory layer, and execute auditable agent tools.

## Current implementation status

This repository starts **Phase 0** of the V1.0 specification:

- monorepo layout
- Tauri 2 + React desktop shell
- Python FastAPI sidecar engine
- SQLite + Alembic schema foundation
- SQLite FTS5 migration
- engine `/health` endpoint
- workspace persistence endpoints
- desktop engine-status UI
- Windows CI build pipeline
- backend unit/integration tests
- secure workspace-root authorization + initial SHA256 scanner/index-job foundation (pre-staged for Phase 2)
- per-launch loopback API session token between Tauri and FastAPI

Phase 0 is not considered complete until the Windows workflow produces a successful native Tauri build with the packaged Python sidecar.

## Repository layout

```text
apps/desktop        Tauri 2 + React desktop client
apps/engine         FastAPI local AI engine
packages/prompts    versioned system prompts (Phase 7+)
packages/shared-types cross-layer contracts (Phase 1+)
docs                architecture/security/database docs
scripts             local and CI build helpers
tests               future end-to-end fixtures
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

The Tauri app attempts to start the bundled `deskai-engine` sidecar on a free loopback port. The frontend asks the Rust host for that endpoint and then checks `/health`.

## Security baseline

- Engine listens on `127.0.0.1` only.
- No local file root is trusted until it is explicitly stored as a workspace root.
- React does not receive API secrets.
- Destructive file actions are not implemented in Phase 0.
- Future document content is treated as untrusted data, never as system instructions.

See `docs/security.md` and `docs/phase0-status.md`.
