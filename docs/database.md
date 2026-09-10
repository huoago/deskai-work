# Database schema — V1 foundation

SQLite is the authoritative local metadata store. Alembic owns schema changes.

Phase 0 creates the V1 tables defined in the product specification:

`workspaces`, `workspace_roots`, `files`, `file_versions`, `chunks`, `entities`, `entity_aliases`, `relations`, `memories`, `memory_versions`, `conversations`, `messages`, `tasks`, `agent_runs`, `tool_calls`, `citations`, `index_jobs`, `permissions`, `settings`, `audit_logs`.

It also creates `chunks_fts` using SQLite FTS5. FTS synchronization triggers are deferred to Phase 4 when chunk indexing is implemented, so Phase 0 does not pretend that retrieval is already functional.
