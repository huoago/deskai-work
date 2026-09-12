# Phase 27 — Semantic Retrieval V2

Status: Phase 27A implementation complete on `phase27-semantic-retrieval-v2`; pending CI. Phase 27B (local BGE-M3) follows after 27A is stable.

## Why Phase 27 exists

The original V1.0 design requires true semantic embeddings plus FTS5/LanceDB hybrid retrieval. Through Phase 26, DeskAI had real FTS5, LanceDB and rank fusion, but vector generation still used the deterministic `local-hash-384-v1` compatibility embedding. That fallback is useful offline, but it is not a semantic model.

Phase 27 closes that gap without weakening Local Only or destabilizing the Windows installer.

## Phase 27A changes

### Embedding provider boundary

`EmbeddingService` now selects a provider from Settings and keeps credentials inside the Engine.

Supported Phase 27A providers:

- `local_hash` — deterministic 384-dimensional offline compatibility fallback;
- `openai` — real semantic embeddings using `text-embedding-3-small` (1536) or `text-embedding-3-large` (3072).

The existing OS-backed OpenAI credential store is reused; API keys are not exposed to React or SQLite.

Local Only blocks cloud embedding execution. Retrieval fails soft to local FTS if a configured cloud embedding provider is temporarily unavailable.

### Provider-versioned LanceDB indexes

Embedding dimensions cannot safely share a LanceDB vector schema. Phase 27 derives a stable provider/model/dimension signature and keeps vector tables isolated by that signature.

The legacy hash provider continues using the existing `chunks` table so current indexes remain usable. New semantic providers use separate versioned tables.

### Batched indexing

A document's chunks are embedded in one provider batch instead of issuing one embedding request per chunk.

### Controlled vector rebuild

`POST /knowledge/embeddings/rebuild`

rebuilds only vector rows for the selected provider from authoritative active chunks. It does not reparse files, replace chunks, change FTS, modify source files, or expand filesystem authority.

### Hybrid Search V2

Retrieval now keeps:

- FTS candidates;
- provider-specific vector candidates;
- reciprocal-rank fusion;
- cosine similarity when a provider vector is available;
- exact content/filename/section boosts;
- deterministic query-term coverage reranking;
- authoritative checks against active chunks and current file versions;
- citation locators for page/sheet/range/slide/section.

The vector stored in LanceDB is reused for similarity scoring rather than re-embedding every candidate chunk during search.

## Safety invariants

- no new filesystem authority;
- no new shell/browser/computer-use authority;
- no automatic upload of Workspace content;
- cloud embedding runs only when explicitly configured and not in Local Only mode;
- OpenAI credentials remain in the existing OS credential boundary;
- provider failure does not fabricate semantic results and falls back to FTS;
- switching vector providers cannot mix incompatible dimensions;
- the compatibility hash provider remains available for fully offline operation;
- no automatic model download is added in Phase 27A.

## Phase 27B next

Local BGE-M3 will be introduced as an optional first-use model download with explicit user consent, model metadata/integrity checks, resumable setup and benchmark evidence. It will not be bundled blindly into the NSIS/MSI package.

## Verification target

- existing full Engine pytest suite;
- new provider/configuration tests;
- OpenAI batch embedding behavior with a mocked client;
- provider-aware knowledge status and vector rebuild tests;
- deterministic reranking tests;
- Ruff;
- desktop typecheck/build;
- packaged Engine smoke;
- Windows NSIS/MSI/checksum/artifact.
