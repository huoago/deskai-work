# Architecture — Phase 0 baseline

## Runtime topology

```text
React WebView
   |
   | Tauri invoke: get_engine_bootstrap
   v
Tauri Rust host
   |
   | spawn sidecar on 127.0.0.1:<ephemeral-port>
   | + per-launch X-DeskAI-Token
   v
FastAPI deskai-engine
   |
   +--> SQLAlchemy --> SQLite deskai.db
   +--> Alembic migrations
   +--> SQLite FTS5 virtual table (schema foundation)
```

The desktop process owns engine startup. The engine is never exposed on `0.0.0.0`. The WebView receives only the loopback endpoint and an ephemeral per-launch session token from the Rust host; the token is not persisted to LocalStorage or SQLite.

## Phase boundaries

Phase 0 provides infrastructure, persistence, health/status and CI. The first secure file-ingestion foundation (authorized roots, SHA256 scanner and index-job creation) is also pre-staged because file reading and automatic indexing are the highest V1 priorities. Parsers, vector storage, hybrid retrieval, memory extraction and the Orchestrator Agent remain later phase gates and must not be reported as complete until their tests pass.
