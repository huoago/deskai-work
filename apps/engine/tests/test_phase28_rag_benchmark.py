from __future__ import annotations

from pathlib import Path

from app.evaluation.rag_benchmark import build_engineering_benchmark_v1, evaluate_retrieval


def _prepare_benchmark(client, tmp_path: Path) -> str:
    documents, _ = build_engineering_benchmark_v1()
    source = tmp_path / "phase28-rag-corpus"
    source.mkdir()
    for document in documents:
        (source / document.filename).write_text(document.content, encoding="utf-8")

    workspace = client.post("/workspaces", json={"name": "Phase 28 RAG Benchmark"})
    assert workspace.status_code == 201
    workspace_id = workspace.json()["id"]

    added = client.post(
        f"/workspaces/{workspace_id}/roots",
        json={"path": str(source), "scan_now": True, "watch_enabled": False},
    )
    assert added.status_code == 201
    assert added.json()["scan"]["queued"] == len(documents)
    assert client.post("/parser/process", params={"limit": 100}).json()["processed"] == len(documents)
    assert client.post("/knowledge/process", params={"limit": 100}).json()["processed"] == len(documents)
    return workspace_id


def test_phase28_benchmark_is_exactly_100_questions_and_has_required_coverage():
    documents, cases = build_engineering_benchmark_v1()
    assert len(documents) == 18
    assert len(cases) == 100
    assert sum(case.answerable for case in cases) == 90
    assert sum(not case.answerable for case in cases) == 10
    assert all(case.support_tokens for case in cases if not case.answerable)
    categories = {case.category for case in cases}
    assert categories == {"quantity", "pressure", "date", "role", "status", "no_evidence"}


def test_phase28_scoring_math_is_deterministic():
    _, cases = build_engineering_benchmark_v1()
    sample = [cases[0], cases[5]]

    def fake_search(question: str, limit: int) -> list[dict]:
        del question, limit
        case = sample[0]
        return [
            {
                "filename": case.expected_files[0],
                "content": " ".join(case.required_tokens),
                "citation_label": case.expected_files[0] + " · section",
                "lexical_rank": 1,
            }
        ]

    report = evaluate_retrieval(sample, fake_search)
    assert report.total_cases == 2
    assert report.recall_at_1 == 0.5
    assert report.recall_at_5 == 0.5
    assert report.mrr == 0.5
    assert report.answer_accuracy == 0.5
    assert report.evidence_accuracy == 0.5


def test_phase28_hallucination_metric_is_entity_probe_based():
    _, cases = build_engineering_benchmark_v1()
    negative = [case for case in cases if not case.answerable][:2]
    calls = 0

    def fake_search(question: str, limit: int) -> list[dict]:
        nonlocal calls
        del question, limit
        calls += 1
        if calls == 1:
            # Generic lexical overlap alone is not evidence for the nonexistent asset.
            return [{"filename": "generic.md", "content": "certified value", "lexical_rank": 1}]
        return [
            {
                "filename": "wrong-support.md",
                "content": f"fabricated documentary support for {negative[1].support_tokens[0]}",
                "citation_label": "wrong-support.md",
                "lexical_rank": 1,
            }
        ]

    report = evaluate_retrieval(negative, fake_search)
    assert report.no_evidence_accuracy == 0.5
    assert report.hallucination_rate == 0.5


def test_phase28_local_hash_end_to_end_retrieval_gate(client, tmp_path: Path):
    workspace_id = _prepare_benchmark(client, tmp_path)
    _, cases = build_engineering_benchmark_v1()

    def search(question: str, limit: int) -> list[dict]:
        response = client.post(
            "/search",
            json={"workspace_id": workspace_id, "query": question, "limit": limit},
        )
        assert response.status_code == 200
        return response.json()["results"]

    report = evaluate_retrieval(cases, search)
    diagnostics = report.as_dict()
    assert report.total_cases == 100, diagnostics
    assert report.recall_at_1 >= 0.90, diagnostics
    assert report.recall_at_5 >= 0.98, diagnostics
    assert report.recall_at_10 >= 0.98, diagnostics
    assert report.mrr >= 0.92, diagnostics
    assert report.answer_accuracy >= 0.98, diagnostics
    assert report.evidence_accuracy >= 0.98, diagnostics
    assert report.citation_accuracy >= 0.98, diagnostics
    assert report.no_evidence_accuracy >= 0.90, diagnostics
    assert report.hallucination_rate <= 0.10, diagnostics
    for category in ("quantity", "pressure", "date", "role", "status"):
        assert report.category_recall_at_5[category] >= 0.95, diagnostics
