from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from app.evaluation.rag_benchmark import (  # noqa: E402
    BenchmarkCase,
    build_engineering_benchmark_v1,
    evaluate_retrieval,
)


def _load_private_pack(path: Path) -> tuple[str, list[BenchmarkCase]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Private benchmark pack must be a JSON object")
    name = str(payload.get("name") or path.stem).strip()
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("Private benchmark pack must contain a non-empty cases array")

    cases: list[BenchmarkCase] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_cases, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Case {index} must be an object")
        case_id = str(item.get("id") or "").strip()
        category = str(item.get("category") or "").strip()
        question = str(item.get("question") or "").strip()
        answerable = bool(item.get("answerable", True))
        expected_files_raw = item.get("expected_files", [])
        required_tokens_raw = item.get("required_tokens", [])
        if not case_id or not category or not question:
            raise ValueError(f"Case {index} must define id, category and question")
        if case_id in seen_ids:
            raise ValueError(f"Duplicate benchmark case id: {case_id}")
        if not isinstance(expected_files_raw, list) or not isinstance(required_tokens_raw, list):
            raise ValueError(f"Case {case_id} expected_files/required_tokens must be arrays")
        expected_files = tuple(str(value).strip() for value in expected_files_raw if str(value).strip())
        required_tokens = tuple(str(value).strip() for value in required_tokens_raw if str(value).strip())
        if answerable and (not expected_files or not required_tokens):
            raise ValueError(f"Answerable case {case_id} requires expected_files and required_tokens")
        if not answerable and (expected_files or required_tokens):
            raise ValueError(f"No-evidence case {case_id} must not define expected evidence")
        seen_ids.add(case_id)
        cases.append(
            BenchmarkCase(
                id=case_id,
                category=category,
                question=question,
                expected_files=expected_files,
                required_tokens=required_tokens,
                answerable=answerable,
            )
        )
    return name, cases


def _drain_pipeline(client: httpx.Client, endpoint: str, *, batch_limit: int, max_batches: int) -> int:
    total = 0
    for _ in range(max_batches):
        response = client.post(endpoint, params={"limit": batch_limit})
        response.raise_for_status()
        processed = int(response.json().get("processed") or 0)
        total += processed
        if processed == 0:
            return total
    raise RuntimeError(f"Pipeline did not drain after {max_batches} batches: {endpoint}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DeskAI Engineering RAG Benchmark v1")
    parser.add_argument("--api-base", default="http://127.0.0.1:18765")
    parser.add_argument("--session-token", required=True)
    parser.add_argument("--provider", choices=("local_hash", "local_bge_m3", "openai"), required=True)
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument(
        "--pack",
        type=Path,
        help="Private Ground Truth JSON. When supplied, corpus-dir is read-only input and no synthetic files are written.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--batch-limit", type=int, default=1000)
    parser.add_argument("--max-batches", type=int, default=100)
    args = parser.parse_args()

    if args.batch_limit < 1 or args.max_batches < 1:
        parser.error("--batch-limit and --max-batches must be positive")

    if args.pack:
        if not args.pack.is_file():
            parser.error(f"Private benchmark pack does not exist: {args.pack}")
        if not args.corpus_dir.is_dir():
            parser.error(f"Private corpus directory does not exist: {args.corpus_dir}")
        benchmark_name, cases = _load_private_pack(args.pack)
        synthetic_documents = None
    else:
        documents, cases = build_engineering_benchmark_v1()
        benchmark_name = "DeskAI Engineering RAG Benchmark v1 — synthetic public fixture"
        synthetic_documents = documents
        args.corpus_dir.mkdir(parents=True, exist_ok=True)
        for document in documents:
            (args.corpus_dir / document.filename).write_text(document.content, encoding="utf-8")

    headers = {"X-DeskAI-Token": args.session_token}
    with httpx.Client(base_url=args.api_base, headers=headers, timeout=120.0) as client:
        settings = client.patch("/settings", json={"embedding_provider": args.provider})
        settings.raise_for_status()

        workspace = client.post("/workspaces", json={"name": f"RAG Benchmark — {args.provider}"})
        workspace.raise_for_status()
        workspace_id = workspace.json()["id"]

        root = client.post(
            f"/workspaces/{workspace_id}/roots",
            json={"path": str(args.corpus_dir.resolve()), "scan_now": True, "watch_enabled": False},
        )
        root.raise_for_status()
        queued = int(root.json().get("scan", {}).get("queued") or 0)
        if queued <= 0:
            raise RuntimeError("Benchmark corpus scan queued no files")

        parsed_total = _drain_pipeline(
            client,
            "/parser/process",
            batch_limit=args.batch_limit,
            max_batches=args.max_batches,
        )
        indexed_total = _drain_pipeline(
            client,
            "/knowledge/process",
            batch_limit=args.batch_limit,
            max_batches=args.max_batches,
        )
        if parsed_total < queued or indexed_total < queued:
            raise RuntimeError(
                f"Benchmark corpus did not fully parse/index: queued={queued}, "
                f"parsed={parsed_total}, indexed={indexed_total}"
            )
        if synthetic_documents is not None and queued != len(synthetic_documents):
            raise RuntimeError(
                f"Synthetic benchmark corpus mismatch: expected={len(synthetic_documents)}, queued={queued}"
            )

        def search(question: str, limit: int) -> list[dict]:
            response = client.post(
                "/search",
                json={"workspace_id": workspace_id, "query": question, "limit": limit},
            )
            response.raise_for_status()
            return response.json()["results"]

        report = evaluate_retrieval(cases, search).as_dict()
        report["provider"] = args.provider
        report["workspace_id"] = workspace_id
        report["benchmark_name"] = benchmark_name
        report["private_pack"] = bool(args.pack)
        report["corpus_files_queued"] = queued
        report["parsed_files"] = parsed_total
        report["indexed_files"] = indexed_total

    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
