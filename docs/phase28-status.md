# Phase 28 — Engineering RAG Acceptance Benchmark

Status: implementation in progress on `phase28-rag-benchmark`.

## Objective

Phase 28 converts retrieval quality from an informal judgement into a repeatable acceptance gate. It does not add Agent authority or new file-writing permissions.

## Benchmark design

`build_engineering_benchmark_v1()` produces exactly 100 synthetic engineering questions over 18 synthetic municipal-engineering records:

- 90 answerable questions;
- 10 no-evidence questions;
- 18 quantity questions;
- 18 pressure questions;
- 18 date questions;
- 18 responsible-role questions;
- 18 handover-status questions.

The corpus is intentionally synthetic and contains no production/customer project data, personnel details or confidential document identifiers.

## Metrics

The common evaluator reports Recall@1/5/10, MRR, answer accuracy, evidence-token accuracy, citation-label accuracy, no-evidence accuracy, hallucination rate, category counts, and per-category Recall@5.

`answer_accuracy` is deterministic extractive Ground Truth: the authoritative retrieved evidence must contain every required answer token. No-evidence cases carry explicit `support_tokens` for the queried entity/fact. Generic lexical overlap is not treated as evidence; a documentary false positive exists only when returned evidence contains the case-specific support probe. `hallucination_rate` is therefore `1 - no_evidence_accuracy`. These definitions require no LLM judge and are exactly reproducible.

## CI regression floor

The CI gate uses `local_hash` as a deterministic compatibility baseline, not as the semantic-quality target. The test creates a real Workspace, scans 18 files, parses and indexes them, then executes all 100 questions through `/search`.

Acceptance thresholds:

- Recall@1 >= 0.90;
- Recall@5 >= 0.98;
- Recall@10 >= 0.98;
- MRR >= 0.92;
- answer accuracy >= 0.98;
- evidence accuracy >= 0.98;
- citation accuracy >= 0.98;
- no-evidence accuracy >= 0.90;
- hallucination rate <= 0.10;
- each answerable category Recall@5 >= 0.95.

The thresholds remain strict. CI #170 exposed an overly broad negative-case definition, not a retrieval-quality miss: all 90 answerable questions scored 1.0 on retrieval/answer/evidence/citation metrics, while generic FTS words incorrectly made no-evidence accuracy appear as 0.0. The evaluator was corrected to use case-specific support probes rather than weakening the threshold.

## Provider comparison and private project packs

`apps/engine/scripts/run_rag_benchmark.py` supports `local_hash`, `local_bge_m3`, and `openai` using the same evaluator and isolated Workspaces.

It also supports `--pack <private-ground-truth.json>`. With a private pack:

- `--corpus-dir` is read in place and no synthetic files are written there;
- the private pack stays outside the public repository;
- answerable cases define `expected_files` and `required_tokens`;
- no-evidence cases define `support_tokens` for deterministic false-positive scoring;
- duplicate IDs and inconsistent answerable/no-evidence definitions fail closed;
- parser/index queues are drained in bounded batches for larger real project corpora.

This is the path for validating actual Lima project material without publishing that material or its Ground Truth to GitHub. The connected Drive already contains project-wide technical/quality summaries and 311/313/324/DN1500 source material, so a private Lima acceptance pack is feasible. A result is recorded only after those files are deliberately materialized into a DeskAI-accessible private corpus and actually run through the Engine.

The public CI never downloads BGE-M3 and never uses cloud credentials. BGE-M3/OpenAI/private-project runs occur only when the corresponding model, credentials and corpus are deliberately available.

## Phase 28 completion gates

Phase 28 is complete only when:

- the 100-question benchmark is present and deterministic;
- scoring unit tests pass;
- all 100 questions execute through real scan -> parse -> index -> `/search`;
- Recall/MRR/answer/citation/no-evidence/hallucination thresholds pass;
- Ruff and the full Engine test suite pass;
- Desktop Web remains green;
- Windows packaged Engine smoke and NSIS/MSI build remain green;
- Windows checksum and Artifact upload remain green;
- the final PR is squash merged and closeout is verified on `main`.
