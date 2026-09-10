# Architecture — Phase 0 baseline

## Runtime topology

```text
React WebView
   |
   | Tauri invoke: get_engine_endpoint
   v
Tauri Rust host
   |
   | spawn sidecar on 127.0.0.1:<ephemeral-port>
   v
FastAPI deskai-engine
   |
   +--> SQLAlchemy --> SQLite deskai.db
   +--> Alembic migrations
   +--> SQLite FTS5 virtual table (schema foundation)
```

The desktop process owns engine startup. The engine is never exposed on `0.0.0.0`.

## Phase boundaries

Phase 0 intentionally provides infrastructure, persistence, health/status and CI. File scanning, parsers, vector storage, retrieval, memory extraction and the Orchestrator Agent are implemented in later phases, but their database tables are created now so migrations remain explicit and auditable.
