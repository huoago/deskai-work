# Phase 3 — Parser gate

Status: implemented on PR #5 and pending final CI verification.

## Objective

Phase 3 converts Phase 2 file records into stable, local, structured parsed documents. It does not build retrieval indexes. The output of this phase is the input contract for Phase 4.

## Supported parsers

- TXT / MD — decoded local text with line-range locators.
- CSV — delimiter-aware rows with row-range locators.
- PDF — PyMuPDF page text with page numbers and page dimensions.
- DOCX — paragraphs, styles, and table rows.
- XLSX — worksheets and non-empty rows; formulas are preserved instead of silently replacing them with stale cached values.
- PPTX — slide text and table content with slide locators.
- JPG / PNG — image format, dimensions, mode and EXIF metadata.

Images are intentionally marked `needs_vision=true`. Phase 3 does not pretend that metadata extraction is semantic image understanding and does not introduce OCR as a substitute for the later vision-capable model path.

## Parsed document contract

Each successful parse is written atomically under:

`<DeskAI data>/cache/document/<sha256>.json`

The cache contains:

- parser and parser version
- source file type
- title
- normalized text
- structured units
- unit locators such as page, slide, sheet, row, paragraph or line range
- source metadata
- file id and SHA256
- parse timestamp

The cache is local only.

## File version lifecycle

A successful parser run:

1. verifies that the local file is still under a readable authorized Workspace root;
2. claims one queued IndexJob;
3. parses the file;
4. recalculates SHA256 after parsing to detect concurrent edits;
5. writes the parsed cache atomically;
6. creates or reactivates the matching FileVersion;
7. deactivates previous versions;
8. assigns `files.current_version_id`;
9. marks the file `parsed`;
10. marks the parser job `completed`.

If the file changes during parsing, the result is rejected and a fresh job is queued. Deleted, revoked or unsupported files cannot be parsed.

## Parser Worker

The Engine owns one background ParserWorker. Claims are serialized so an explicit desktop “立即解析” request cannot race the background worker and parse the same job twice.

Development controls:

`DESKAI_PARSER_WORKER_ENABLED=1`

`DESKAI_PARSER_WORKER_INTERVAL=0.75`

Tests disable the background worker and trigger it explicitly for deterministic results.

## APIs

- `GET /parser/status`
- `POST /parser/process?limit=...`
- `GET /files/{file_id}/parsed`

The file listing also exposes `current_version_id` and `parser_version`.

## Desktop UX

The Files page now exposes:

- pending and parsed counts
- parser worker state
- immediate scan and immediate parse actions
- per-file parse state
- parser version
- clickable parsed files
- extracted text preview
- structured locator and metadata preview

## Phase boundary

Phase 3 deliberately does **not** insert rows into `chunks`, synchronize FTS5, create embeddings, write LanceDB, perform hybrid retrieval, or generate citations. Those are Phase 4 responsibilities.

## Verification

Generated test fixtures validate TXT/MD/CSV, multi-page PDF, DOCX paragraphs/tables, multi-sheet XLSX/formulas, multi-slide PPTX and PNG metadata. Tests also verify FileVersion rotation and assert that Phase 3 creates no Chunk rows.

Before merge, the normal repository gate must pass:

- Ruff
- Pytest
- TypeScript typecheck
- Vite build
- PyInstaller Windows sidecar build
- packaged sidecar health smoke test
- native Tauri NSIS/MSI build
