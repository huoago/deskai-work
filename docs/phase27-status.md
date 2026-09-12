# Phase 27 — Semantic Retrieval V2

Status: complete, verified, and merged to `main` through Phase 27B. Phase 27A merge SHA: `ff7639b6c30c0ea85e484c0bdd67d3193ff97b21`. Phase 27B merge SHA: `a6aa8dccd5e616644cd2cc108acf8c257ee1e69e`.

## Why Phase 27 exists

The V1.0 design requires true semantic embeddings plus FTS5/LanceDB hybrid retrieval. Through Phase 26, DeskAI had real FTS5, LanceDB and rank fusion, but vector generation still used the deterministic `local-hash-384-v1` compatibility embedding. That fallback remains useful offline, but it is not a semantic model.

Phase 27 closes that gap without weakening Local Only, mixing incompatible vector dimensions, or silently downloading large models.

## Phase 27A — Semantic Retrieval V2

`EmbeddingService` selects a provider from Settings and keeps credentials inside the Engine.

Supported providers after Phase 27:

- `local_hash` — deterministic 384-dimensional offline compatibility fallback;
- `local_bge_m3` — local 1024-dimensional semantic embeddings using the optional BGE-M3 ONNX model;
- `openai` — semantic embeddings using `text-embedding-3-small` (1536) or `text-embedding-3-large` (3072).

The existing OS-backed OpenAI credential store is reused; API keys are not exposed to React or SQLite. Local Only blocks cloud embedding execution. Retrieval fails soft to local FTS if a configured semantic provider is temporarily unavailable.

### Provider-versioned LanceDB indexes

Embedding dimensions cannot safely share a LanceDB vector schema. Phase 27 derives a stable provider/model/dimension signature and keeps vector tables isolated by that signature.

The legacy hash provider continues using the compatibility table so current indexes remain usable. New semantic providers use separate provider-versioned tables.

### Batched indexing and controlled rebuild

Document chunks are embedded in provider batches rather than one request per chunk.

`POST /knowledge/embeddings/rebuild` rebuilds only vector rows for the selected provider from authoritative active chunks. It does not reparse files, replace chunks, change FTS, modify source files, or expand filesystem authority.

### Hybrid Search V2

Retrieval keeps FTS candidates, provider-specific vector candidates, reciprocal-rank fusion, cosine similarity, filename/section/content boosts, deterministic query-term coverage reranking, authoritative current-file checks, and citation locators for page/sheet/range/slide/section.

## Phase 27B — Local BGE-M3

Phase 27B adds `local_bge_m3` as a true local semantic provider with a 1024-dimensional vector boundary.

Design constraints:

- the model is not bundled into NSIS/MSI;
- startup and provider selection never download a model automatically;
- download occurs only from an explicit user action;
- the quantized ONNX model and tokenizer are pinned by SHA-256 and verified before activation;
- model files live only under DeskAI's private `models/bge-m3` directory;
- deleting the model is an explicit user action and does not silently delete existing vector indexes;
- inference uses ONNX Runtime + tokenizers instead of Torch/FlagEmbedding;
- PyInstaller explicitly collects ONNX Runtime and tokenizers so the packaged sidecar can execute local inference after the user downloads the model.

### Local model lifecycle API

- `GET /knowledge/embeddings/local-model` — status only; never downloads;
- `POST /knowledge/embeddings/local-model/download` — explicit download plus pinned SHA-256 verification;
- `DELETE /knowledge/embeddings/local-model` — explicit removal;
- `POST /knowledge/embeddings/rebuild` — rebuild provider-specific vectors from authoritative active chunks.

### Desktop controls

The Phase 27B `Embedding Control Center` exposes provider selection, OpenAI embedding model selection, local BGE-M3 install state, explicit model download/removal, Workspace selection, and explicit vector rebuild under the currently selected provider.

The module is intentionally isolated from the oversized legacy `App.tsx`; Phase 32 can fold it into the refactored Settings architecture without increasing current front-end coupling.

## Safety invariants

- no new Workspace filesystem authority;
- no new shell/browser/computer-use authority;
- no automatic upload of Workspace content;
- no automatic local-model download or installation;
- cloud embedding runs only when explicitly configured and not in Local Only mode;
- OpenAI credentials remain inside the existing OS credential boundary;
- provider failure does not fabricate semantic results and falls back to FTS;
- switching providers cannot mix incompatible dimensions;
- model assets are checksum-verified before activation;
- vector rebuild does not modify source files.

## Verification evidence

### Phase 27A

PR #49 was squash merged after CI #148 completed successfully.

Windows Artifact `10305428306` contained exactly the NSIS installer, MSI installer and `SHA256SUMS.txt`; independently recomputed installer hashes matched the manifest.

### Phase 27B

PR #50 final verified head: `f7240b0c8a6f1155314f1d53e67af903da258f9b`.

CI #154 / workflow run `34722248774` completed successfully on that exact head:

- Ruff: success;
- Engine tests: **183 passed / 65 warnings**;
- desktop TypeScript typecheck: success;
- desktop production build: success;
- packaged Engine build: success;
- packaged Engine smoke: success;
- Windows NSIS/MSI build: success;
- installer checksum generation: success;
- workflow Artifact upload: success.

Windows Artifact `10306224323`:

- Artifact ZIP size: `442223244` bytes;
- Artifact digest: `sha256:7a4b933dbd924ec7008f7f6a8ba8d030f12b033b8888ee5d2a2256357e606973`;
- NSIS: `DeskAI Work_0.1.0_x64-setup.exe`, `220938283` bytes, SHA-256 `f1649e260399f335a4b1f7ad460572a18ecaed76ac442ebe72a6cdd028b5ecb9`;
- MSI: `DeskAI Work_0.1.0_x64_en-US.msi`, `221413376` bytes, SHA-256 `711218995dcadb169622ca6c362557fee1c13af136c6c16571289f1e1c841947`.

The Artifact was independently downloaded and inspected. It contained only the NSIS installer, MSI installer, and `SHA256SUMS.txt`; both installer hashes were recomputed independently and matched the manifest.

Compared with the Phase 27A Artifact (~394.7 MB), the Phase 27B Artifact increased by only ~47.6 MB, consistent with adding ONNX Runtime/tokenizers and incompatible with accidentally embedding the approximately 570 MB BGE-M3 model in each installer. The model remains an explicit first-use download.

PR #50 was then squash merged using the CI-verified head. Phase 27B merge SHA: `a6aa8dccd5e616644cd2cc108acf8c257ee1e69e`.

## Outcome

Phase 27 closes the semantic-retrieval gap with both cloud and true local semantic embedding paths while preserving deterministic fallback, provider-isolated vector indexes, Local Only boundaries, explicit large-model consent, and Windows packaging integrity.

Next phase: **Phase 28 — Engineering RAG Acceptance Benchmark**, centered on a real 100-question engineering retrieval and answer-quality benchmark rather than additional retrieval architecture changes.
