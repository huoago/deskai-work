# Phase 4 — Local knowledge retrieval gate

Status: implemented on the Phase 4 branch and pending final CI verification.

## Objective

Phase 4 turns the stable parsed-document contract from Phase 3 into a local retrieval system that can answer: “Which current project files contain information relevant to this query, and where exactly is that information located?”

This phase remains model-independent. It does not call OpenAI or any other cloud model.

## Pipeline

1. Phase 3 marks a current file version as `parsed`.
2. `KnowledgeIndexer` loads the SHA256-addressed parsed cache.
3. Structured parsed units are converted into bounded, overlapping Chunks.
4. Each Chunk is stored in SQLite with source locators.
5. FTS5 receives the current Chunk text and file metadata.
6. A deterministic local vector representation is written to LanceDB.
7. The FileVersion receives `indexed_at`.
8. The file transitions from `parsed` to `indexed`.

## Chunking

Chunks preserve the strongest available locator from the parser:

- PDF page
- XLSX sheet and row range
- PPTX slide
- DOCX paragraph or heading-derived section
- TXT/MD line range
- CSV row range
- image metadata section

Default text chunk size is bounded to approximately 1,600 characters with a small overlap. Phase 4 stores a content SHA256 for every Chunk.

## Keyword retrieval

Phase 4 creates `chunks_fts_v2`.

Modern SQLite builds use the FTS5 `trigram` tokenizer, which is useful for Chinese text, engineering identifiers, and substring-oriented retrieval. If the runtime does not provide trigram FTS5, migration 0002 falls back to `unicode61`, while the search layer retains a safe LIKE fallback for queries that produce no FTS rows.

## Local vector retrieval

LanceDB stores the vector copy of current chunks under:

`<DeskAI data>/vectors/lancedb`

The Phase 4 provider is:

`local-hash-384-v1`

It is a deterministic local feature vector based on multilingual word and character n-grams. Its purpose is to provide offline vector similarity and validate the complete local hybrid-retrieval pipeline before a model embedding provider is introduced. It must not be described as a neural semantic embedding model.

Phase 5 may optionally replace or complement this vector provider with model-generated embeddings.

## Hybrid ranking

Search retrieves candidates independently from FTS5 and LanceDB, then fuses the rankings using reciprocal-rank-style scoring. Exact query occurrences receive a small deterministic boost.

Before returning a result, SQLite is treated as authoritative. A result is accepted only if:

- its Chunk is active;
- the File is in the same Workspace;
- the File status is `indexed`;
- the Chunk belongs to the File's current version.

This prevents stale or cross-project vector rows from becoming user-visible search results.

## Version replacement

When a file changes:

- Phase 2 detects a new SHA256;
- Phase 3 creates a new FileVersion;
- Phase 4 deactivates the old Chunks;
- old FTS rows are removed;
- the file's LanceDB rows are replaced;
- only the current version can be returned by search.

## APIs

- `GET /knowledge/status?workspace_id=...`
- `POST /knowledge/process?limit=...`
- `POST /knowledge/files/{file_id}/retry`
- `POST /search`

Search results include:

- file and chunk IDs
- filename
- fused score
- content/snippet
- source locator
- human-readable citation label
- lexical rank
- vector rank
- vector provider

## Desktop UX

The desktop adds a dedicated “资料检索” page with:

- indexed-file count
- active Chunk count
- pending knowledge-index count
- Knowledge Worker state
- manual knowledge-index action
- local query input
- fused search results
- page / sheet / slide / section citation labels
- FTS and vector rank diagnostics

## Security and privacy

- all parsing, chunking, FTS and vector generation is local;
- no source content is sent to a cloud API in Phase 4;
- Workspace boundaries are enforced in both lexical and vector retrieval;
- old versions are filtered even if a stale external vector row remains.

## Verification

Phase 4 tests verify:

- Chinese project text and engineering-code retrieval;
- PDF page citations;
- XLSX sheet and row citations;
- file-version replacement and stale-result removal;
- cross-Workspace isolation;
- knowledge status counts and embedding-provider reporting.

The normal repository gate must pass before merge:

- Ruff
- Pytest
- TypeScript typecheck
- Vite build
- PyInstaller sidecar build
- packaged sidecar health smoke test
- native Tauri NSIS/MSI build
- Windows artifact upload
