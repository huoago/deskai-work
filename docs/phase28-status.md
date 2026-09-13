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

The common evaluator reports:

- Recall@1;
- Recall@5;
- Recall@10;
- Mean Reciprocal Rank (MRR);
- answer accuracy;
- evidence-token accuracy;
- citation-label accuracy;
- no-evidence accuracy;
- hallucination rate;
- per-category case counts;
- per-category Recall@5.

For this public deterministic benchmark, `answer_accuracy` is an extractive-ground-truth metric: the authoritative retrieved evidence must contain every required answer token for the case. `hallucination_rate` is the documentary-support false-positive rate on the 10 no-evidence questions and is exactly `1 - no_evidence_accuracy`. These definitions are deterministic and do not require an LLM judge.

The CI gate uses the deterministic `local_hash` provider as a regression floor. This does not claim that `local_hash` is semantic. The exact same benchmark can be run against `local_bge_m3` or `openai` with `apps/engine/scripts/run_rag_benchmark.py`, producing directly comparable JSON reports.

## CI regression floor

The end-to-end benchmark creates a real Workspace, scans the 18 files, parses them, indexes them, and executes all 100 queries through `/search`.

Initial acceptance thresholds:

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

Thresholds are intentionally strict for the synthetic fixture because every answerable question has authoritative ground truth. Future real-project/private acceptance packs may define separate thresholds without weakening this public regression floor.

## Provider comparison

The manual runner supports:

- `local_hash` — deterministic compatibility baseline;
- `local_bge_m3` — true local semantic embedding, requiring the explicitly installed model;
- `openai` — cloud semantic embedding, requiring configured credentials and Local Only disabled.

Provider runs use separate Workspaces so vector dimensions and provider-specific indexes never mix.

The public CI does not download BGE-M3 or use cloud credentials. A private/local run can execute the same 100 questions against all three providers and persist comparable JSON reports without changing the benchmark or thresholds.

## Phase 28 completion gates

Phase 28 is complete only when:

- the 100-question benchmark is present and deterministic;
- benchmark scoring unit tests pass;
- all 100 questions execute end-to-end through the real indexing/search path;
- Recall/MRR/answer/citation/no-evidence/hallucination thresholds pass on the deterministic baseline;
- Ruff and the full Engine test suite pass;
- Desktop Web remains green;
- Windows packaged Engine smoke and NSIS/MSI build remain green;
- Windows checksum and Artifact upload remain green;
- the final PR is squash merged and closeout is verified on `main`.

A live BGE-M3/OpenAI provider comparison is recorded only when the corresponding model/credential is deliberately available; Phase 28 does not silently download a model or use cloud credentials in CI.
