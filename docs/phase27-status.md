# Phase 27 — Semantic Retrieval V2

Status: Phase 27A is verified and merged to `main` at `ff7639b6c30c0ea85e484c0bdd67d3193ff97b21`. Phase 27B is in verification on PR #50.

## Why Phase 27 exists

The V1.0 design requires true semantic embeddings plus FTS5/LanceDB hybrid retrieval. Through Phase 26, DeskAI had real FTS5, LanceDB and rank fusion, but vector generation still used the deterministic `local-hash-384-v1` compatibility embedding. That fallback remains useful offline, but it is not a semantic model.

Phase 27 closes that gap without weakening Local Only, mixing incompatible vector dimensions, or silently downloading large models.

## Phase 27A — merged

### Embedding provider boundary

`EmbeddingService` selects a provider from Settings and keeps credentials inside the Engine.

Phase 27A providers:

- `local_hash` — deterministic 384-dimensional offline compatibility fallback;
- `openai` — real semantic embeddings using `text-embedding-3-small` (1536) or `text-embedding-3-large` (3072).

The existing OS-backed OpenAI credential store is reused; API keys are not exposed to React or SQLite.

Local Only blocks cloud embedding execution. Retrieval fails soft to local FTS if a configured cloud embedding provider is unavailable.

### Provider-versioned LanceDB indexes

Embedding dimensions cannot safely share a LanceDB vector schema. Phase 27 derives a stable provider/model/dimension signature and keeps vector tables isolated by that signature.

The legacy hash provider continues using the compatibility table so current indexes remain usable. New semantic providers use separate provider-versioned tables.

### Batched indexing and controlled rebuild

Document chunks are embedded in provider batches rather than one request per chunk.

`POST /knowledge/embeddings/rebuild` rebuilds only vector rows for the selected provider from authoritative active chunks. It does not reparse files, replace chunks, change FTS, modify source files, or expand filesystem authority.

### Hybrid Search V2

Retrieval keeps:

- FTS candidates;
- provider-specific vector candidates;
- reciprocal-rank fusion;
- cosine similarity when a provider vector is available;
- exact content/filename/section boosts;
- deterministic query-term coverage reranking;
- authoritative checks against active chunks and current file versions;
- citation locators for page/sheet/range/slide/section.

The vector stored in LanceDB is reused for similarity scoring rather than re-embedding every candidate chunk during search.

### Phase 27A verification

PR #49 was squash merged after CI #148 completed successfully.

Windows Artifact `10305428306` was independently inspected. It contained exactly the NSIS installer, MSI installer and `SHA256SUMS.txt`; independently recomputed installer hashes matched the manifest.

## Phase 27B — local BGE-M3

Phase 27B adds `local_bge_m3` as a true local semantic provider with a 1024-dimensional vector boundary.

Design constraints:

- the model is **not bundled** into NSIS/MSI;
- startup and provider selection **never download a model automatically**;
- download occurs only from an explicit user action;
- the quantized ONNX model and tokenizer are pinned by SHA-256 and verified before activation;
- model files live only under DeskAI's private `models/bge-m3` directory;
- deleting the model is an explicit user action and does not silently delete existing vector indexes;
- inference uses ONNX Runtime + tokenizers rather than adding Torch/FlagEmbedding to the Windows installer;
- PyInstaller explicitly collects ONNX Runtime and tokenizers so the packaged sidecar can execute local inference after the user downloads the model.

### Local model lifecycle API

- `GET /knowledge/embeddings/local-model` — status only; never downloads;
- `POST /knowledge/embeddings/local-model/download` — explicit download + pinned SHA-256 verification;
- `DELETE /knowledge/embeddings/local-model` — explicit removal;
- `POST /knowledge/embeddings/rebuild` — rebuild provider-specific vectors from authoritative active chunks.

### Desktop controls

A temporary Phase 27B `Embedding Control Center` module exposes:

- provider selection (`local_hash`, `local_bge_m3`, `openai`);
- OpenAI embedding model selection;
- local BGE-M3 install state;
- explicit download and explicit removal;
- Workspace selection;
- explicit vector rebuild under the currently selected provider.

This module is intentionally isolated from the oversized legacy `App.tsx`. Phase 32 will fold it into the refactored Settings architecture instead of increasing the current App.tsx engineering debt.

## Safety invariants

- no new Workspace filesystem authority;
- no new shell/browser/computer-use authority;
- no automatic upload of Workspace content;
- no automatic local-model download or installation;
- cloud embedding runs only when explicitly configured and not in Local Only mode;
- OpenAI credentials remain inside the existing OS credential boundary;
- provider failure does not fabricate semantic results and falls back to FTS;
- switching providers cannot mix incompatible dimensions;
- the compatibility hash provider remains available for deterministic fallback;
- model assets are checksum-verified before activation;
- vector rebuild does not modify source files.

## Phase 27 completion gates

Phase 27 is not complete until all of the following are green on the final Phase 27B head:

- full Engine pytest suite;
- Ruff;
- desktop TypeScript typecheck;
- desktop production build;
- packaged Engine build and smoke;
- Windows NSIS/MSI build;
- installer checksum generation;
- workflow Artifact upload;
- independent Artifact contents/checksum verification;
- confirmation that the large BGE-M3 model itself is not embedded in the Windows installers;
- PR #50 squash merge and final Phase 27 closeout verification.

After Phase 27 closes, work moves directly to Phase 28: the real 100-question Engineering RAG acceptance benchmark.
