# Security baseline

## Loopback-only engine

The bundled FastAPI engine refuses non-loopback hosts from the CLI. The normal runtime uses `127.0.0.1` and a port selected by the Tauri host.

## Local-file authorization

Phase 0 persists workspace-root permissions (`read_allowed`, `write_allowed`, `watch_enabled`). Phase 2 must resolve and normalize a candidate path and verify the filesystem parent/child relationship against these roots before any read or write operation. String-prefix checks alone are prohibited.

## Secrets

OpenAI credentials are not implemented in Phase 0. When added, secrets must use Windows Credential Manager or an equivalent OS-backed store; they must not be persisted in browser LocalStorage, SQLite plaintext, repository files, or logs.

## Tool risk

Destructive and irreversible actions remain absent in Phase 0. Later tools must record risk level and confirmation state in `tool_calls` and `audit_logs`.
