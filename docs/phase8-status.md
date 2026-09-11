# Phase 8 — Sandboxed Word and Excel artifact tools

Status: implemented on the Phase 8 branch and pending final CI verification.

## Objective

Phase 8 lets DeskAI move from read-only Agent work to producing real work artifacts without granting arbitrary filesystem write access.

The Agent can create new DOCX and XLSX files only inside DeskAI's private generated-artifact sandbox.

It still cannot edit or overwrite source Workspace files.

## Output boundary

All generated files are written under:

`<DeskAI data dir>/generated/<task_id>/`

The model does not choose an absolute destination path.

For every requested filename, ArtifactService:

1. discards directory components and keeps only the basename;
2. sanitizes unsafe characters;
3. forces the expected extension;
4. resolves the final task directory and verifies it remains under the DeskAI generated root;
5. never overwrites an existing file;
6. creates a unique filename when a collision occurs.

A request such as:

`../../Desktop/report.docx`

therefore becomes a file under the current DeskAI task sandbox, not the user's Desktop.

## New tools

### create_word_document

Risk level: L3.

Creates a new DOCX using python-docx.

The model may provide:

- filename
- document title
- up to 30 sections
- section heading/body text

The tool never opens or modifies an existing source document.

### create_spreadsheet

Risk level: L3.

Creates a new XLSX using openpyxl.

Limits:

- up to 10 worksheets
- up to 50 columns per sheet
- up to 2,000 rows per sheet
- cell text capped before persistence

Spreadsheet values are treated as data. Text beginning with `=`, `+`, `-`, or `@` is prefixed with an apostrophe before writing, preventing model-supplied formula injection from becoming an executable spreadsheet formula.

## Artifact registry

Every generated file is registered in SQLite table `generated_artifacts` with:

- artifact id
- task id
- Workspace id
- kind
- filename
- local path
- MIME type
- SHA-256
- size
- created timestamp

Task detail includes its artifacts, and the desktop Tasks page shows each generated file.

## Privacy

The absolute local artifact path remains local.

The Agent tool result returned to the cloud model includes only:

- artifact id
- kind
- filename
- MIME type
- SHA-256
- size

The Windows local path is available only through the local Artifact API / desktop UI.

## Audit

Artifact tool attempts use the same Phase 7 ToolCall and AuditLog path.

A generated file is treated as successfully created only after:

- local file creation succeeds;
- SHA-256 and size are computed;
- GeneratedArtifact is persisted;
- ToolCall completes.

If registry persistence fails, the newly created file is removed.

## Explicitly unavailable

Phase 8 still does not expose:

- arbitrary destination paths
- source-file overwrite
- source-file modification
- delete
- move/rename of source files
- arbitrary Python
- shell/PowerShell
- browser/computer control
- email or messaging
- payment actions
- credential reading

## Desktop UX

Task detail shows:

- Agent result
- generated artifact count
- artifact filename/type
- local size
- creation time
- local save path
- SHA-256
- tool-call audit records

The UI explicitly labels these as newly generated files rather than edits to original Workspace content.

## Verification

Phase 8 tests create real DOCX/XLSX files and reopen them using python-docx/openpyxl.

Required tests verify:

- DOCX content is physically present;
- `../` and absolute-style destination attempts cannot escape the generated task directory;
- duplicate names do not overwrite;
- XLSX workbook structure is valid;
- formula-like cell strings remain plain text;
- model-visible tool output does not contain the local absolute path;
- artifact listing is Workspace-scoped;
- task detail contains generated artifacts.

## Phase boundary

The next tool phase may introduce sandboxed Python and web research.

Writing back into an original user document remains a separate permission class and must not be enabled merely because Phase 8 can create new artifacts.
