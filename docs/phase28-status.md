# Phase 28 — Engineering RAG Acceptance Benchmark

Status: feature implementation merged to `main`; final private Lima semantic acceptance is pending executable BGE-M3 and OpenAI provider runs. Phase 28 must not be described as fully accepted until those runs are recorded.

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

A private 100-question Lima acceptance pack has been generated from two connected, real project source documents exported locally and **not committed to GitHub**:

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

The private pack exists only in the controlled local working environment as `lima_rag_acceptance_pack_v1.private.json`. It is intentionally excluded from this public repository.

## Real Lima local_hash execution

The real Lima private corpus and 100-question pack were executed in the controlled local environment using a retrieval-core harness reproduced directly from the current production source for:

- plain-text parsing/block boundaries (`app/parsing/readers.py`);
- Phase 27 chunking (`MAX_CHARS=1600`, `OVERLAP_CHARS=180`);
- SQLite FTS5 trigram query construction and BM25 ordering;
- `local-hash-384-v1` feature hashing and normalized vectors;
- Phase 27 Hybrid Search RRF/vector-similarity/deterministic reranking/engineering-ID boost;
- Phase 28 `evaluate_retrieval` semantics and no-evidence support probes.

This is a faithful retrieval-core execution, but it is **not represented as a packaged FastAPI Engine run** because the controlled container cannot clone/install the complete repository runtime from the network.

Observed Lima `local_hash` metrics:

| Metric | Result | Real-project threshold | Verdict |
| --- | ---: | ---: | --- |
| Recall@1 | 0.744444 | >= 0.90 | FAIL |
| Recall@5 | 0.977778 | >= 0.98 | FAIL (marginal) |
| Recall@10 | 1.000000 | >= 0.98 | PASS |
| MRR | 0.841481 | >= 0.92 | FAIL |
| Answer Accuracy | 0.744444 | >= 0.98 | FAIL |
| Evidence Accuracy | 0.744444 | >= 0.98 | FAIL |
| Citation Accuracy | 1.000000 | >= 0.98 | PASS |
| No-Evidence Accuracy | 1.000000 | >= 0.90 | PASS |
| Hallucination Rate | 0.000000 | <= 0.10 | PASS |

Per-category Recall@5:

- change: 1.000000
- handover: 1.000000
- hot_tap: 1.000000
- measurement: 0.833333
- project_data: 1.000000
- quality: 1.000000
- test: 1.000000
- water_meter: 1.000000

Failure analysis: citation/file selection remained correct, but 23 of the 90 answerable cases failed exact Ground Truth token evidence in the first matching-file chunk. The largest clusters were water-meter (5), quality (5), measurement (4), hot-tap (3), project-data (3), and handover (3). This supports the intended architecture decision: `local_hash` remains an offline compatibility fallback, not the semantic-quality target.

## Semantic-provider execution blockers — do not fabricate results

The same controlled environment was checked for the exact production semantic runtimes.

### local_bge_m3

Production requires the pinned Xenova BGE-M3 quantized ONNX model and tokenizer plus Python `onnxruntime` and `tokenizers`. The host contains an OS-level `libonnxruntime.so.1.21.0`, but the required Python packages, tokenizer asset, and pinned model asset are not present. Outbound DNS is blocked, so PyPI/Hugging Face installation/download attempts fail. A different embedding model is not an acceptable substitute for this acceptance comparison.

Status: **BLOCKED BY EXECUTION ENVIRONMENT — no score recorded.**

### openai

Neither `OPENAI_API_KEY` nor `DESKAI_OPENAI_API_KEY` is exposed to the controlled runtime. Phase 27 intentionally keeps credentials in the authorized secret store and prevents inventing or leaking them.

Status: **BLOCKED BY AUTHORIZED CREDENTIAL AVAILABILITY — no score recorded.**

## Remaining acceptance gate — do not waive

Final acceptance still requires the same private Lima pack to be run with:

1. `local_bge_m3` using the exact pinned production model/tokenizer;
2. `openai` using an explicitly authorized configured credential;
3. the same Recall@K, MRR, Answer Accuracy, Citation Accuracy, No-Evidence Accuracy and Hallucination Rate metrics side-by-side;
4. a clear final pass/fail verdict against the real-project thresholds.

The `local_hash` result is now recorded and demonstrates that the compatibility fallback is **not sufficient** for the real Lima semantic target.

Until the two semantic-provider reports exist, the correct status is: **Phase 28 feature complete and merged; real Lima local_hash baseline executed and failed semantic acceptance; BGE-M3/OpenAI comparison pending due external runtime/credential prerequisites. The closeout branch must remain unmerged.**
