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


## Controlled file organization boundary

Phase 13 adds the first controlled source-file path changes.

The Agent receives only `propose_file_organization`, an L3 staging tool. It can propose one of two operations for one existing Workspace file:

- `rename` within the file's current directory;
- `move` into an already-existing subdirectory of the **same** authorized writable Workspace root.

The tool only records a proposal. It cannot rename or move the file itself.

Proposal creation requires:

- the File belongs to the active Workspace;
- the File is inside a readable root with `write_allowed=true`;
- the source is a regular non-symlink file;
- the on-disk SHA-256 still matches DeskAI's File record;
- no other active organization proposal exists for that File;
- the File is not currently in a parser/indexer `processing` job.

Rename proposals must preserve the current file extension and reject Windows-invalid or reserved names.

Move proposals accept only a relative directory under the same Workspace root. The directory must already exist. Phase 13 does not create directories.

The target path must not already exist. DeskAI never uses Phase 13 as an overwrite path.

### Desktop-confirmed path change

Actual path mutation is available only through the trusted loopback desktop confirmation API.

Before the path changes, DeskAI revalidates:

1. task / Workspace / File ownership;
2. current writable root authorization;
3. source path is still the path captured by the proposal;
4. current source SHA-256 still equals the proposal hash;
5. target is still absent;
6. target directory still exists and is writable;
7. source and target directory are on the same filesystem/device;
8. source is writable/not busy;
9. DOCX/XLSX is not accompanied by a Microsoft Office `~$` lock file.

On Windows, DeskAI uses no-overwrite rename semantics. On POSIX CI/dev hosts, the same-device implementation uses hard-link creation followed by source unlink so destination creation fails when the target already exists.

After a successful path change, DeskAI updates the existing File record in place. The `File.id`, content SHA-256, and existing FileVersion relationships are preserved.

The root is rescanned so path metadata stays synchronized, but unchanged file content is not treated as a new document.

### Rollback

A separately confirmed rollback can restore the original path only when:

- the current file still has the exact SHA-256 applied by DeskAI;
- the original path is still unoccupied;
- both paths remain inside the same writable root;
- the source is not busy/locked.

If the file content changed after the rename/move, rollback is blocked instead of moving the newer external/user version.

### Interrupted-process recovery

Path changes persist `applying` and `rolling_back` states before filesystem mutation.

At Engine startup, Phase 13 inspects incomplete operations. Automatic recovery proceeds only when exactly one of the original or target paths contains the expected SHA-256.

If DeskAI cannot unambiguously identify a safe state, the proposal enters `recovery_required` and no further automatic path mutation is attempted.

Phase 13 still does **not** provide:

- file deletion;
- directory rename/move/delete;
- target overwrite;
- cross-Workspace or cross-root moves;
- cross-device moves;
- arbitrary destination paths;
- extension-changing renames;
- unattended Agent path mutation;
- shell, unrestricted Python, or GUI/browser control.


## Transactional file organization boundary

Phase 14 extends Phase 13 path changes into a coordinated batch of 2–10 independent file operations.

The Agent receives `propose_file_organization_batch`, an L3 staging tool. It creates only a batch record plus Phase 13 member proposals. It cannot change paths.

Every member independently inherits the Phase 13 boundary:

- active Workspace ownership;
- readable and writable authorized root;
- regular non-symlink source file;
- current SHA-256 matches DeskAI's File record;
- rename preserves the extension and uses a Windows-compatible filename;
- move stays inside an existing directory of the same writable root;
- no target overwrite;
- same-device path movement only.

The batch additionally requires:

- 2–10 unique File ids;
- unique target paths;
- no target may equal another batch member's current source path;
- no swap/cycle semantics;
- no duplicate members.

Before the first path mutation, confirmation preflights **every** member. If one member is stale, locked, being processed, unauthorized, unwritable, or has a target collision, no member is moved.

After preflight, the batch enters `applying` and every member enters `applying`.

DeskAI then applies member path changes sequentially using Phase 13 no-overwrite semantics. If any operation or verification fails, all already-moved members are restored to their original paths in reverse order.

A completely restored failure returns the entire batch to `pending`. If automatic restoration is incomplete, the batch and in-flight members become `recovery_required`.

Single-file confirm/reject/rollback APIs reject Phase 14 batch members; they can only be acted on through the batch API.

### Batch rollback

Rollback preflights the complete batch before moving any member.

Every currently applied file must still have the exact SHA-256 recorded by DeskAI and every original path must be unoccupied.

If a rollback fails after some files have been restored, DeskAI attempts to reapply those members to the Phase 14 target paths so the batch returns to its previous applied state.

### Interrupted-process recovery

Engine startup recovers Phase 14 batches before Phase 13 individual operations.

For an interrupted `applying` batch, any members already at target paths are moved back to their original paths. The fully restored batch returns to `pending`.

For an interrupted `rolling_back` batch, remaining target-path members are restored to originals and the batch is finalized as `rolled_back`.

Automatic recovery proceeds only when every member is unambiguous: exactly one of its original/target paths contains the expected SHA-256.

If any member is missing, duplicated, altered, unauthorized, or otherwise ambiguous, the batch enters `recovery_required` and DeskAI stops automatic path mutation.

Phase 14 still does not provide file deletion, directory operations, cross-root/cross-Workspace moves, target overwrite, rename swaps/cycles, cross-device moves, unrestricted Python, shell execution, or browser/GUI control.


## Controlled recycle-bin boundary

Phase 15 introduces recoverable removal of one Workspace file without introducing a permanent-delete channel.

The Agent receives only `propose_file_recycle`, an L4 staging tool. It can create a proposal only when the user explicitly asks to delete, remove, or recycle one existing Workspace file.

The Agent cannot move, unlink, purge, or permanently delete the source.

Proposal creation requires:

- active Workspace ownership;
- the source is inside a readable root with `write_allowed=true`;
- the source is a regular non-symlink file;
- current on-disk SHA-256 still matches DeskAI's File record;
- the file is not in an active parser/indexer `processing` job;
- the file has no active source-edit, organization, or recycle proposal.

A proposal records the original path, original SHA-256, size, previous File status, and a unique private quarantine path under DeskAI's data directory.

### Confirmed recycle

Actual removal from the Workspace is available only through the trusted desktop confirmation API.

Before removal, DeskAI revalidates the source path, Workspace write permission, current SHA-256, parser state, source writeability, and Microsoft Office `~$` lock state.

The recycle sequence is deliberately ordered:

1. copy the source to a unique private quarantine directory;
2. SHA-256 verify the temporary copy;
3. atomically finalize the quarantine copy;
4. verify the finalized quarantine SHA-256;
5. **recompute the Workspace source SHA-256 again** to detect changes during copying;
6. only then unlink the exact authorized Workspace source path;
7. verify the original path is absent and the quarantine copy still matches the original SHA;
8. mark the existing File row as `recycled`.

The File row keeps its original path, File id, content SHA-256, current FileVersion, chunks, and parsed cache relationships. Hybrid search already requires `File.status == "indexed"`, so a recycled File immediately disappears from retrieval without destroying its version history.

Queued/parser jobs are cancelled when recycle finalizes.

Workspace scanning explicitly preserves the `recycled` state instead of converting the missing original path to generic `deleted`. Authorization reconciliation also preserves recycled metadata so temporarily revoking a root does not destroy restore state.

### Restore

Restore requires the original Workspace root to be readable and writable again.

Before restore:

- the quarantine copy must exist and match the original SHA-256;
- the original parent directory must exist and be writable;
- the original path must be absent.

DeskAI copies the quarantine file to a temporary file in the original directory, verifies SHA-256, and then creates the original path using no-overwrite semantics. A concurrently recreated original file is never overwritten.

After successful restore, the same File id is returned to its previous File status and the Workspace is rescanned. The quarantine copy is intentionally retained.

### Interrupted-process recovery

Recycle/restore operations persist `recycling` and `restoring` states before filesystem mutation.

At Engine startup:

- interrupted recycle with the original still intact returns to `pending`; a verified quarantine copy may be retained for retry;
- interrupted recycle with the original absent and verified quarantine present finalizes as `recycled`;
- interrupted restore with both a verified original and verified quarantine finalizes as `restored`;
- interrupted restore before original creation returns to `recycled`.

Unknown/ambiguous hashes or path states enter `recovery_required` and DeskAI stops automatic mutation.

### No permanent deletion in Phase 15

Phase 15 intentionally exposes **no** API or Agent tool that deletes a quarantine copy.

There is no recycle purge endpoint, no retention timer, no scheduled cleanup, and no permanent-delete tool.

A later phase may introduce separately confirmed retention/purge policy, but it must be designed as an independent higher-risk capability rather than reusing the recoverable recycle action.


## Transactional recycle-batch boundary

Phase 16 extends the Phase 15 recoverable recycle model to one coordinated transaction containing 2–10 Workspace files.

The Agent receives only `propose_file_recycle_batch`, an L5 staging tool. It can create a batch proposal but cannot remove any Workspace file.

Each member independently inherits every Phase 15 constraint:

- active Workspace ownership;
- containing root is readable and `write_allowed=true`;
- regular non-symlink source;
- current on-disk SHA-256 matches DeskAI's File record;
- no parser/indexer processing job;
- no active edit, organization, or recycle proposal;
- private quarantine path is contained inside DeskAI's recycle sandbox.

A batch additionally requires:

- 2–10 unique File ids;
- unique source paths;
- every member belongs to the same task / Workspace;
- members cannot be confirmed, rejected, or restored through the single-file API.

### Confirmed batch recycle

Before the first Workspace source is removed, DeskAI performs the following complete sequence:

1. preflight every member for path, Workspace write permission, SHA freshness, processing state, source writeability, and Office lock state;
2. persist the batch and all members as `recycling`;
3. create and SHA-verify a private quarantine copy for **every** member;
4. after all quarantine copies exist, re-check processing/writeability and recompute every source SHA-256;
5. only then begin removing Workspace originals one by one.

This ordering is the key Phase 16 invariant: **no Workspace original may be removed until every batch member already has a verified quarantine copy.**

After each source removal, DeskAI again verifies that member's quarantine copy.

If a later source removal or verification fails, every already-removed member is recreated from its verified quarantine copy in reverse order.

When rollback restores every original, the entire batch returns to `pending`; quarantine copies may remain for a safe retry.

If automatic rollback cannot prove a complete original-path state, the batch enters `recovery_required` and no further automatic mutation occurs.

Database finalization is also guarded. If filesystem removal completed but metadata commit fails, DeskAI attempts to restore every original before returning the batch to `pending`.

### Transactional batch restore

A recycled batch can only be restored as one unit.

Before the first original is recreated, DeskAI verifies:

- every quarantine copy exists and matches its recorded SHA-256;
- every original parent still exists and is writable;
- every original path is unoccupied.

DeskAI then restores members with no-overwrite semantics while retaining all quarantine copies.

If a later restore fails, already-restored originals are SHA-checked and removed again so the entire batch returns to its previous `recycled` state.

If rollback to the recycled state is incomplete or ambiguous, the batch enters `recovery_required`.

### Interrupted-process recovery

Phase 16 batch recovery runs before Phase 15 individual recycle recovery.

For an interrupted `recycling` batch, DeskAI converges to the safe pre-commit state: any members whose Workspace originals were already removed are restored from quarantine, and the entire batch returns to `pending`.

For an interrupted `restoring` batch, DeskAI completes restoration of any missing originals and finalizes the batch as `restored`.

Automatic recovery acts only on exact expected SHA-256 states. Unexpected content, missing required quarantine copies, symlinks, or ambiguous original/quarantine combinations freeze the batch as `recovery_required`.

Phase 16 still exposes no permanent-delete/purge endpoint, retention timer, scheduled quarantine cleanup, directory deletion, arbitrary unlink, shell execution, unrestricted Python, or browser/GUI control.


## Unified recovery-center boundary

Phase 17 adds an operator-facing Recovery Center without creating a new mutation engine.

The new `GET /recovery` endpoint is **read-only**. It aggregates persisted state from:

- Phase 11 single-file source edits;
- Phase 12 transactional source-edit batches;
- Phase 13 single-file organization proposals;
- Phase 14 transactional organization batches;
- Phase 15 single-file recycle proposals;
- Phase 16 transactional recycle batches.

Batch members are not emitted as independent entries, which prevents duplicate or partial actions against a transaction that must remain atomic.

The Recovery Center intentionally excludes ordinary `pending` and `rejected` proposals. Tasks remain the approval surface for new mutations. Recovery Center focuses on:

- applied content/path transactions that are eligible for rollback;
- recycled files/batches that are eligible for restore;
- completed rollback/restore history;
- `recovery_required` states that need operator review.

### No alternate write path

Phase 17 exposes no generic `POST /recovery`, no force-recover endpoint, and no database-state override.

Desktop recovery buttons dispatch only to the original Phase 11–16 endpoints:

- source-edit rollback;
- source-edit batch rollback;
- file-organization rollback;
- file-organization batch rollback;
- recycle restore;
- recycle-batch restore.

Therefore the original SHA-256 freshness checks, write authorization, backup/quarantine requirements, no-overwrite rules, Office-lock checks, transactional rollback behavior, startup recovery, and audit logging remain authoritative.

### recovery_required behavior

A `recovery_required` entry is visible in Recovery Center, including its persisted error message and associated Task id.

Recovery Center does **not** expose a "force", "ignore hash", "overwrite", or "mark fixed" action.

The operator may open the associated Task and review AuditLog history, but any filesystem repair outside verified DeskAI automation remains an explicit manual action.

Phase 17 does not add permanent deletion, quarantine purge, directory mutation, arbitrary path access, shell execution, unrestricted Python, or browser/GUI automation.


## Phase 18 — Recovery diagnostics and guided repair

Phase 18 does not introduce a mutation API. It derives structured diagnostics only from already-persisted recovery transaction records.

Security invariants:

- only `recovery_required` entries receive guided diagnostics;
- normal rollback/restore remains exclusively on the original Phase 11–16 services;
- diagnostic output never changes files, database transaction status, Workspace permissions, backup data, or quarantine data;
- automatic repair is explicitly unavailable;
- force overwrite and ignore-SHA behavior remain unavailable;
- occupied targets are preserved rather than overwritten;
- backup/quarantine copies must be preserved during manual investigation;
- transactional batches must be investigated as a whole rather than member-by-member;
- unknown failures degrade to a low-confidence manual-review classification rather than guessing a destructive fix.


## Phase 19 — Recovery snapshot and safe recheck

Phase 19 adds an on-demand GET-only recovery snapshot for `recovery_required` records.

Security invariants:

- the transaction id must resolve to one of the six persisted Phase 11–16 transaction families;
- the persisted transaction status must already be `recovery_required`;
- callers cannot provide arbitrary filesystem paths;
- only paths already stored by the transaction or its tracked File record are inspected;
- symbolic links are reported but never followed or hashed;
- missing, conflicting or hash-mismatched paths remain non-actionable;
- snapshots are not stored in SQLite and do not alter AuditLog, transaction status, Workspace File status, backup data, or quarantine data;
- even a consistent snapshot returns `safe_to_retry_existing_action: false`;
- Phase 19 cannot clear `recovery_required` and cannot call rollback/restore itself;
- no overwrite, purge, ignore-SHA, force-repair or single-member batch repair capability is introduced.


## Phase 20 — Controlled recovery state reconciliation

Phase 20 introduces a metadata-only mutation, but does not introduce any new user-file mutation capability.

Security invariants:

- only a transaction already persisted as `recovery_required` may create a proposal;
- Phase 20 only accepts Phase 19 `consistent_applied` with rollback readiness or `consistent_recycled` with restore readiness;
- original/restored/mixed/ambiguous states cannot be reconciled by Phase 20;
- proposal creation stores a stable snapshot fingerprint rather than a filesystem snapshot or user content;
- confirmation requires a new Phase 19 snapshot and an identical canonical fingerprint;
- any changed path/hash/size/member/evidence state marks the proposal stale;
- stale proposals cannot be confirmed;
- every proposal requires explicit second human confirmation;
- reconciliation cannot be invoked by Agent tools;
- source-edit reconciliation changes only edit/batch metadata;
- organization reconciliation may align the DeskAI File record to the already-existing verified target path but performs no filesystem move;
- recycle reconciliation may set the DeskAI File record to `recycled` but performs no filesystem removal or restore;
- batch reconciliation checks exact membership and updates the whole batch in one database transaction;
- batch members cannot be reconciled individually;
- reconciliation never runs the historical rollback/restore action itself;
- after reconciliation, the original Phase 11–16 rollback/restore endpoint remains the only path that can mutate files;
- no force overwrite, ignore-SHA, arbitrary target status, purge, backup deletion, directory mutation, shell, unrestricted Python, or browser/GUI mutation is added.

## Phase 21 — Recovery evidence export and incident packages

Phase 21 adds a support/audit export channel, not a recovery mutation channel.

Security invariants:

- exports are limited to transactions already visible in Recovery Center;
- transactional batch members cannot be exported independently;
- the output path is generated inside DeskAI's existing GeneratedArtifact sandbox and cannot be supplied by the caller;
- ZIP packages contain transaction metadata, diagnostics, filesystem observations, hashes, reconciliation history and audit events only;
- Workspace file bytes, staged candidate bytes, automatic backup bytes and recycle quarantine bytes are never embedded;
- a live Phase 19 snapshot is captured only while the transaction is still recovery_required;
- exporting a package does not change transaction status, reconciliation status, Workspace File metadata, root permissions, backups or quarantine copies;
- no rollback, restore, reconciliation confirmation, force overwrite, ignore-SHA, purge or arbitrary filesystem operation is performed;
- Phase 21 exposes no Agent tool for evidence export;
- the generated ZIP itself is SHA-256 recorded as a GeneratedArtifact and the package manifest hashes every internal evidence member.

## Phase 22 — Controlled multi-step work plans

Phase 22 adds orchestration state, not new filesystem authority.

Security invariants:

- plan-mode Tasks are isolated from the legacy Agent Worker;
- model-drafted steps must select an already registered ToolRegistry tool;
- PermissionGate is evaluated locally and the persisted risk level/execution mode come from local policy, not model output;
- automatic steps are limited to permitted tools with existing non-confirmation policy; high-risk existing-file actions are proposal-only `propose_*` tools;
- any `propose_*` step pauses the plan immediately after the original Phase 11–16 proposal is persisted;
- Resume only observes the original proposal state and continues after `applied` or `recycled` as appropriate;
- the plan exposes no new source-write, rename/move, recycle, rollback, restore, reconciliation, purge, overwrite or ignore-SHA endpoint;
- result references can read only persisted structured results of explicitly declared earlier dependency steps;
- cancellation preserves already-created external proposals instead of mutating them implicitly;
- rejected/ambiguous confirmation gates block the plan and cannot trigger automatic replanning;
- Engine restart freezes a running plan rather than replaying an unproven step;
- interrupted steps require explicit Retry, and steps that already created persistent file proposals cannot be automatically retried;
- all actual tool executions continue to create ToolCall and AuditLog records under the original policy.

