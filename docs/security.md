# Security baseline

## Loopback-only engine

The bundled FastAPI engine refuses non-loopback hosts from the CLI. The normal runtime uses `127.0.0.1` and a port selected by the Tauri host.

## Local-file authorization

Phase 0 persists workspace-root permissions (`read_allowed`, `write_allowed`, `watch_enabled`). Phase 2 must resolve and normalize a candidate path and verify the filesystem parent/child relationship against these roots before any read or write operation. String-prefix checks alone are prohibited.

## Secrets

Phase 5 implements cloud-provider secret storage through the operating-system keyring. On Windows, user-entered OpenAI API credentials are stored by the Windows credential backend.

Secrets must not be persisted in browser LocalStorage, SQLite, repository files, exports, citations, or logs. The provider-status API reports only whether a credential is configured and where it is sourced; it never returns the saved credential value.

For development and centrally managed deployments, `OPENAI_API_KEY` may be supplied as an environment variable. Environment-managed credentials take precedence and are read-only from the desktop UI.

## Tool risk

Destructive and irreversible actions remain absent in Phase 0. Later tools must record risk level and confirmation state in `tool_calls` and `audit_logs`.


## Model boundary

Hybrid chat performs document retrieval locally and sends only bounded relevant context, conversation history, and the user's request to the configured provider. Responses API requests use `store=false`; DeskAI does not use provider-hosted response state as the authoritative conversation store.

Local Only mode is a hard policy boundary. Until a local model provider exists, the application returns an error rather than silently sending content to a cloud model.

## Retrieved-content trust

Files inside an authorized Workspace are readable data, not trusted instructions. Retrieved document text is wrapped as untrusted reference material and is subordinate to DeskAI's system instructions. Tool execution remains disabled during Phase 5, reducing the impact of prompt injection before later Agent phases add explicit permissions, confirmations, and audit controls.


## Long-term memory boundary

Phase 6 separates durable Memory from the document Knowledge Base.

Automatic Memory learning uses only user conversation content as the authoritative source. Assistant-generated answers and retrieved file contents are not independently promoted into Memory.

The model-side extractor is constrained to a Structured Output schema, and the Engine independently applies type, scope, confidence, importance, size, and sensitivity checks before persistence.

Automatic and manual Memory rejects obvious credentials and sensitive personal information, including passwords, API keys/tokens, payment-card credentials, government identifiers, health/medical information, race/ethnicity, religion, political affiliation, sexual orientation/sex-life information, and trade-union membership.

Memory changes are reviewable. A changed value is versioned in `memory_versions`; deactivation is soft and reversible. Workspace memories do not cross project boundaries, while global memory is limited to durable cross-project preferences, constraints, and workflows.

During chat, Memory is context below the current user instruction. A current user instruction overrides conflicting stored Memory. Memory is not represented as a file citation.


## Agent execution boundary

Phase 7 introduces model-directed tool selection without granting the model direct operating-system access.

The model receives only tool definitions returned by ToolRegistry. Every attempted function call is independently re-authorized by PermissionGate immediately before local execution.

The Phase 7 default allow-list contains only four read-only, Workspace-scoped tools:

- `search_knowledge`
- `search_memory`
- `list_workspace_files`
- `read_parsed_document`

Unknown tools are treated as L8, confirmation-required operations and are denied. No delete, overwrite, shell, Python, browser/computer control, send, payment, or credential-reading tool is registered in this phase.

Tool results and retrieved content are untrusted data. They cannot redefine Agent instructions, change permissions, or cause an unregistered tool to become available.

All tool attempts are persisted in `tool_calls`, and execution outcomes are mirrored into `audit_logs`. A model statement that an action happened is never treated as evidence of execution without a completed local ToolCall record.

Agent execution uses provider requests with `store=false`. Persistent task state, run state, tool state, and audit history remain in the local DeskAI database.


## Generated artifact write boundary

Phase 8 introduces the first write-capable Agent tools, but the write scope is not a user-selected filesystem path.

`create_word_document` and `create_spreadsheet` may create only new files below DeskAI's private `generated/<task_id>/` directory. ArtifactService strips path components from model-provided filenames, normalizes and resolves the destination, verifies the resolved path remains below the generated root, and refuses overwrite by generating a unique collision-safe name.

These tools are L3 operations because they persist new local files, but they do not gain write permission to authorized Workspace roots. Workspace `write_allowed` is not consulted as an implicit escalation mechanism.

Spreadsheet values beginning with `=`, `+`, `-`, or `@` are persisted as literal text rather than formulas to reduce formula-injection risk.

The local absolute artifact path is not included in model-visible tool output. It is exposed only by the loopback Artifact API to the trusted desktop UI.

Artifact creation is atomic at the application level: if database registry persistence fails after file creation, the new file is deleted.

Source-document edit, overwrite, delete, arbitrary destination writes, shell execution, and unrestricted Python remain unavailable.
