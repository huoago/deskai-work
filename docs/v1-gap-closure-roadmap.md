# DeskAI Work V1.0 — Gap-Closure Roadmap

This roadmap reorders the post-Phase-26 work around the original V1.0 acceptance criteria rather than adding more Agent authority.

## Phase 27 — Semantic Retrieval V2

Goal: replace the compatibility hash vector as the primary semantic path and improve Hybrid Retrieval quality without weakening offline behavior.

### Phase 27A

- introduce a real `EmbeddingProvider` boundary;
- keep `local-hash-384-v1` only as deterministic offline/test fallback;
- add OpenAI `text-embedding-3-small` / `text-embedding-3-large` providers using the existing OS-backed credential store;
- isolate LanceDB tables by provider/model/dimension so vector schemas cannot collide;
- batch document embeddings during indexing;
- add controlled vector-only rebuild after provider changes;
- preserve FTS fallback if a cloud embedding call is unavailable;
- improve RRF fusion with filename/section/content and deterministic term-coverage reranking.

### Phase 27B

- add local BGE-M3 provider as an optional first-use model download, not a bundled multi-GB installer dependency;
- store the model under DeskAI's local model directory;
- add download consent, integrity/version metadata and resumable model setup;
- benchmark BGE-M3 vs OpenAI embeddings vs the compatibility fallback before changing the default;
- expose embedding provider/model and rebuild progress in desktop Settings.

## Phase 28 — Engineering RAG Benchmark

- create at least 100 grounded engineering questions;
- include exact-number, approval-date, person-role, conflict, no-evidence and citation-location cases;
- measure Recall@5, Recall@10, MRR, citation accuracy, conflict detection and refusal accuracy;
- run the benchmark automatically when retrieval logic changes;
- keep a stable fixture corpus and expected evidence references.

## Phase 29 — Knowledge Graph & Entity Resolution

- strengthen `entities`, `entity_aliases` and `relations` extraction/use;
- normalize engineering identifiers such as RRP-04/RRP04/RRP 04;
- persist relation provenance to source chunks;
- use entity matches as retrieval boosts before adding a graph UI.

## Phase 30 — Memory Reliability

- acceptance-test durable fact/preference/decision/correction/workflow/person/project/rule memories;
- make correction/supersede history explicit and auditable;
- test document-vs-memory conflicts and source precedence;
- verify a corrected fact survives a new conversation.

## Phase 31 — Document Intelligence V2

- layout-aware PDF parsing and table preservation;
- scanned-PDF detection and OCR provider boundary;
- Word heading/table/image-reference fidelity;
- Excel range/table/formula/value fidelity;
- PowerPoint slide/speaker-note/table fidelity;
- image metadata/OCR/vision provider path;
- citation-locator accuracy regression tests.

## Phase 32 — Frontend Architecture Refactor

- split the oversized `App.tsx` into pages/features/components/hooks/stores;
- isolate Chat, Workspace, Files, Knowledge, Memory, Tasks, Activity and Settings;
- introduce focused server-state/data hooks while preserving behavior;
- no feature expansion during the refactor.

## Phase 33 — CI & Supply-Chain Gates

- make frontend lint and Rust `cargo check` explicit CI gates;
- add dependency/security scanning suitable for Python/npm/Rust;
- add secret scanning and SBOM generation;
- keep Windows packaged-engine smoke, NSIS/MSI build and checksums mandatory.

## Phase 34 — Clean Windows Qualification

- install on clean Windows 10/11 VMs without Python/Node/Docker;
- test first launch, sidecar health, migration, workspace creation, indexing, retrieval, citations, Agent tools and artifact generation;
- test Chinese/Spanish/space-containing paths, offline mode, corrupted documents and reinstall/recovery;
- capture a repeatable release-qualification report.

## Phase 35 — Signed Human-Controlled Updates

- preserve manual check/approval introduced in Phase 26;
- add signed release/update evidence and installer verification;
- do not enable silent download/install/restart;
- require an explicit human action before installation.

## Phase 36 — V1.0 Final Acceptance & Release Candidate

- execute the original V1.0 acceptance checklist end to end;
- require code + unit tests + integration tests + usable UI + error handling + logs + documentation;
- require RAG benchmark thresholds and clean-Windows qualification to pass;
- publish an RC only after the acceptance evidence is complete.
