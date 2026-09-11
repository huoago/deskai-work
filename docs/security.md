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


## Controlled computation boundary

Phase 9 does not expose general Python execution.

The local calculation tool parses a single expression with Python AST and validates every node against a numeric-only allow-list before evaluation. Imports, attributes, subscripts, comprehensions, lambdas, statements, file access, process APIs, networking, and environment access are unavailable.

Expression size, AST complexity, numeric constants, and exponent magnitude are bounded before evaluation.

Tabular tools never receive a local path. They receive a DeskAI File id, validate that the File belongs to the active Workspace, require a current parsed/indexed FileVersion, restrict file types to CSV/XLSX, and read only the local parsed-document cache.

Phase 9 therefore does not weaken the existing Workspace filesystem authorization model and must not be treated as permission to run arbitrary code.


## Controlled web research boundary

Phase 10 adds read-only public web research through the configured OpenAI Responses provider.

The Agent receives a single registered `search_web` function tool. The local WebResearchService validates and bounds the search query, refuses credential-like secret material, requires a configured provider credential, and refuses to run when Privacy Mode is `local`.

The provider call exposes only OpenAI's hosted `web_search` tool. DeskAI requests `web_search_call.action.sources` and returns a bounded source list with title and URL so the Agent can ground its final answer.

Phase 10 does **not** add:

- arbitrary URL fetch/open capabilities;
- file downloads;
- browser or GUI control;
- webpage form submission;
- code, shell, or subprocess execution;
- access to local files through the web tool;
- permission changes based on webpage instructions.

Web pages and search results remain untrusted data. Retrieved web content cannot override DeskAI system policy, Tool Registry policy, Workspace permissions, or confirmation requirements.


## Confirmed source-file write boundary

Phase 11 introduces the first controlled edits to existing Workspace source files.

The Agent receives only `propose_source_file_edit`, an L3 staging tool. It can build a candidate change for TXT/MD/DOCX/XLSX files, but it cannot overwrite the source. Proposal creation requires:

- the File belongs to the active Workspace;
- the File is inside a readable Workspace root;
- that same root has `write_allowed=true`;
- the source is not a symlink;
- the source type and size are inside Phase 11 limits;
- the on-disk SHA-256 still matches DeskAI's current File record.

The candidate is stored under DeskAI's private `edit_proposals/<edit_id>/` area. A proposal records the original SHA-256, candidate SHA-256, bounded diff/preview, task, file and status.

Actual source mutation is not model-callable. It is exposed only through a trusted desktop confirmation action. On confirmation the Engine:

1. revalidates Workspace write permission;
2. recomputes the current source SHA-256 and requires it to match the proposal's original hash;
3. verifies the staged candidate hash;
4. creates a private backup under `edit_backups/<edit_id>/`;
5. uses same-volume temporary-file replacement for the source;
6. verifies the applied SHA-256;
7. records an L5 `source_edit_applied` AuditLog event;
8. rescans and requeues the updated source for parsing/indexing.

If the source changed externally after the proposal, confirmation is blocked instead of overwriting the newer file.

Rollback is separately user-triggered. It is permitted only if the current source SHA-256 still equals the exact hash DeskAI applied. This prevents rollback from overwriting subsequent user or external edits.

Phase 11 still does not provide source-file delete, move, rename, arbitrary path writes, shell access, unrestricted Python, browser control, or unattended source overwrites.

For DOCX replacement proposals, affected paragraphs are rewritten through python-docx; inline run formatting inside changed paragraphs may be simplified. XLSX edit proposals modify explicit cells only and neutralize formula-like text values instead of introducing formulas.


## Transactional multi-file write boundary

Phase 12 extends confirmed source editing from one file to a coordinated batch of 2–10 supported source files.

The Agent may call `propose_source_file_edit_batch`, an L3 staging tool. This tool can only create private candidate files and one batch record. It cannot write, rename, move, or delete Workspace source files.

Every member must independently satisfy the Phase 11 source-edit boundary:

- active Workspace ownership;
- readable source file;
- containing Workspace root has `write_allowed=true`;
- source is not a symlink;
- supported TXT/MD/DOCX/XLSX type and size;
- current SHA-256 matches DeskAI's recorded file version.

A batch rejects duplicate file ids and contains at most ten members.

Before the first source write, desktop-confirmed batch apply performs a full preflight over **all** members:

1. revalidate Workspace write permission;
2. require each current source SHA-256 to match the staged original SHA;
3. verify each candidate SHA-256;
4. check that every source can be opened for writing;
5. refuse DOCX/XLSX members when a Microsoft Office `~$` lock file is present;
6. create and verify an original backup for every member.

Only after every preflight and backup succeeds does the Engine enter the `applying` state.

Batch apply is all-or-nothing at the DeskAI transaction layer. Members are replaced one by one using the existing same-directory temporary-file + `os.replace` mechanism. If any member write or hash verification fails, every member already written in that attempt is restored from its verified backup in reverse order.

A successfully applied batch records all members as applied together and writes an L6 batch audit event.

Batch members cannot be confirmed, rejected, or rolled back through the single-file API. They must be acted on as one batch, preventing UI/API paths from breaking transaction consistency.

Batch rollback also preflights the entire batch. Every current source must still match the exact SHA that DeskAI applied. If rollback fails part way through, DeskAI attempts to reapply the staged candidates for already-restored members so the batch returns to the previously applied state.

### Interrupted-process recovery

Before Agent workers start, the Engine inspects batches left in `applying` or `rolling_back`.

For each member, automatic recovery acts only when the current file hash equals either the staged original hash or the staged candidate hash and the verified original backup is available. Candidate-state files are restored to the original version.

If any member has an unknown hash, missing/corrupt backup, or cannot be safely restored, the whole batch enters `recovery_required`. DeskAI then performs no further automatic writes for that batch.

Phase 12 still does not add arbitrary destination writes, source deletion, rename/move, unrestricted Python, shell/subprocess execution, browser control, or unattended source-file mutation.
