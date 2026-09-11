# Phase 11 — Confirmed source-file editing

Status: implemented on the Phase 11 branch and pending full CI verification.

## Objective

Phase 11 lets DeskAI modify selected existing Workspace files without giving the model unattended overwrite authority.

Supported source types:

- TXT
- Markdown
- DOCX
- XLSX

The Agent can only create a staged proposal. A human must separately confirm each proposal in the desktop Tasks UI before the source file changes.

## Permission model

Source-edit proposal creation requires all of the following:

1. active Workspace ownership;
2. existing readable source file;
3. source inside an authorized Workspace root;
4. that root has `write_allowed=true`;
5. supported file type;
6. source is not a symlink;
7. source SHA-256 still matches DeskAI's recorded version.

The Workspace UI keeps write permission disabled by default. Enabling it is an explicit user action and does not itself authorize any individual edit.

## Agent tool

### propose_source_file_edit

Risk level: L3.

The tool supports three modes:

- `text_replace` for TXT/MD literal replacements;
- `docx_replace` for DOCX paragraph/table text replacements;
- `xlsx_cells` for explicit worksheet-cell value edits.

The tool writes only to DeskAI's private proposal sandbox. Its result is marked:

```text
requires_user_confirmation = true
```

It never reports the source as modified.

## Human confirmation

The desktop Tasks UI shows:

- filename and edit kind;
- proposal summary;
- bounded diff/change preview;
- original SHA-256;
- candidate SHA-256;
- Confirm Apply;
- Reject Proposal;
- Roll Back to Before Edit after a successful apply.

Confirmation is not an Agent tool call. It is a direct user action against the loopback Engine API.

## Apply transaction

Before applying, DeskAI rechecks the current source SHA-256. If it differs from the original proposal hash, apply is blocked.

A successful apply:

1. verifies the staged candidate hash;
2. creates a private original-file backup;
3. atomically replaces the source using a sibling temporary file;
4. verifies the applied file hash;
5. records the edit as applied;
6. writes an L5 audit event;
7. rescans the authorized root and requeues indexing.

## Rollback

Rollback uses the private original backup.

It is allowed only when the source currently matches the exact SHA-256 created by the confirmed edit. If another program or user changed the file after DeskAI applied it, automatic rollback is blocked.

## Format safety

### TXT / Markdown

- UTF-8 only;
- maximum 10 MB for text replacement;
- at most 20 literal replacements per proposal;
- every requested search string must exist.

### DOCX

- python-docx based;
- at most 20 literal replacements;
- paragraphs and table-cell paragraphs are supported;
- affected paragraph run formatting may be simplified.

### XLSX

- openpyxl based;
- at most 100 explicit cell edits;
- text, number, boolean and blank values;
- formula-like text beginning with `=`, `+`, `-`, or `@` is neutralized as literal text;
- formula creation/execution is not enabled in Phase 11.

## Verification

Phase 11 tests cover:

- write permission required before proposal creation;
- proposal does not modify the source;
- explicit confirmation applies the candidate;
- backup creation;
- rollback restores the original;
- stale/external source changes block confirmation;
- DOCX proposal/apply behavior;
- XLSX typed cell updates and formula-injection neutralization;
- Agent can create only a pending proposal;
- proposal ToolCall is L3;
- task detail exposes proposals for desktop confirmation.

Required CI gates:

- Ruff;
- full Engine Pytest suite;
- TypeScript typecheck;
- Vite production build;
- Windows PyInstaller sidecar;
- packaged Engine 0.11.0 smoke test;
- packaged Source Edit API smoke;
- Tauri NSIS/MSI;
- Windows Artifact upload.

## Phase boundary

Phase 11 does not add file deletion, move/rename, arbitrary destination writes, browser/computer control, shell commands, or unrestricted Python execution.
