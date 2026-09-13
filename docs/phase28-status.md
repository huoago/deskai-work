# Phase 28 — Engineering RAG Acceptance Benchmark

Status: feature implementation merged to `main`; final private Lima semantic acceptance is pending an executable provider run. Phase 28 must not be described as fully accepted until that run is recorded.

## Feature merge

- Feature PR: #52 — `Phase 28 — Engineering RAG Acceptance Benchmark`
- Verified feature head: `f30d576ceb2b4d69c4e043248b11bcb9f35e7f4d`
- Feature squash merge: `8193885f2df6910b7a830db3a411f89826d40082`
- CI run: #175 / run `34730959107`
- Desktop Web: success
- Ruff: success
- Engine: **188 passed / 301 warnings in 80.06 s**
- Windows packaged Engine build/smoke: success
- NSIS/MSI build: success
- checksum generation and artifact upload: success

### Windows evidence

- Artifact: `DeskAI-Work-Windows`
- Artifact ID: `10309566504`
- Artifact ZIP size: `442,227,141` bytes
- Artifact ZIP SHA-256: `8e8ec9ff0751d870e14a07f70dfa076055bbb172b5d30a524fe3ee6d59726078`
- NSIS SHA-256 (independently re-hashed after download): `115d65462d6792b609a0029ad6eedd483e0997d61bfaed80fbe9073f8aed46a9`
- MSI SHA-256 (independently re-hashed after download): `b6f3e5d40b3bd5ccdda5db274ead1b65849b98da4b34cc2fab39c0d1bd1276e6`
- `SHA256SUMS.txt` matched both independently computed installer hashes.

## Public deterministic benchmark

`build_engineering_benchmark_v1()` provides exactly 100 public synthetic engineering questions over 18 synthetic municipal-engineering records:

- 90 answerable questions;
- 10 no-evidence questions;
- quantity, pressure, date, responsible-role and handover-status coverage.

The evaluator reports:

- Recall@1 / Recall@5 / Recall@10;
- Mean Reciprocal Rank (MRR);
- deterministic Answer Accuracy;
- evidence accuracy;
- citation accuracy;
- no-evidence accuracy;
- Hallucination Rate (`1 - no_evidence_accuracy`);
- per-category Recall@5.

The deterministic `local_hash` CI regression floor remains intentionally strict:

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

CI #170 exposed an overly broad no-evidence scoring rule: generic FTS overlap made all negative cases look supported even though the 90 answerable cases scored 1.0 on retrieval/answer/evidence/citation metrics. The rule was corrected to use case-specific support probes rather than lowering the acceptance threshold.

## Provider comparison runner

`apps/engine/scripts/run_rag_benchmark.py` supports isolated runs for:

- `local_hash` — deterministic compatibility baseline;
- `local_bge_m3` — local semantic embedding when the BGE-M3 model is deliberately installed;
- `openai` — cloud semantic embedding when credentials are deliberately configured and Local Only is disabled.

The runner also supports `--pack <private-ground-truth.json>` so real project corpora can be tested without committing project material to the public repository. Private packs fail closed on duplicate IDs and inconsistent answerable/no-evidence definitions and use bounded parser/index draining for larger corpora.

## Private Lima acceptance pack prepared outside GitHub

A private 100-question Lima acceptance pack has now been generated from two connected, real project source documents exported locally and **not committed to GitHub**:

1. `利马管网项目的技术、质量、测量、变更、绘图、试验经验总结｜V4完整重编版｜2026.txt` (~300 KB)
2. `秘鲁管网施工标准作业体系_第三版中文扩编版_2026.txt` (~219 KB)

Pack structure:

- exactly **100 cases**;
- **90 answerable + 10 no-evidence**;
- 53 answerable cases sourced from the project experience summary;
- 37 answerable cases sourced from the standard-operation-system document;
- source-derived categories include handover/acceptance, testing, measurement/GIS/As-Built, DN1500 hot tapping, water meters, quality/materials, change/evidence and project data;
- every answerable Ground Truth token was reverse-checked against its declared source file;
- Ground Truth source-consistency errors: **0**.

The private pack currently exists only in the controlled local working environment as `lima_rag_acceptance_pack_v1.private.json`. It is intentionally excluded from this public repository.

## Remaining acceptance gate — do not waive

Phase 28 is **not yet fully accepted** because the real Lima pack has not yet been executed end-to-end through the packaged DeskAI Engine with all deliberately available semantic providers.

The current controlled execution environment cannot clone/run the public repository directly (outbound GitHub DNS is unavailable in the container), no BGE-M3 model is present in the local model cache, and no OpenAI API credential is exposed to this runtime. These are execution-environment constraints, not reasons to substitute synthetic results for real-project results.

Final acceptance requires recording, for the private Lima 100-question pack:

1. `local_hash` baseline report;
2. `local_bge_m3` semantic report after the model is deliberately installed;
3. `openai` semantic report only when an authorized credential is deliberately configured;
4. Recall@K, MRR, Answer Accuracy, Citation Accuracy, No-Evidence Accuracy and Hallucination Rate side-by-side;
5. a clear pass/fail verdict against the agreed real-project thresholds.

Until those results exist, the correct status is: **Phase 28 feature complete and merged; real Lima semantic acceptance pending.**
