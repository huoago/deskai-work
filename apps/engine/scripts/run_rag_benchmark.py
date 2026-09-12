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
    build_engineering_benchmark_v1,
    evaluate_retrieval,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DeskAI Engineering RAG Benchmark v1")
    parser.add_argument("--api-base", default="http://127.0.0.1:18765")
    parser.add_argument("--session-token", required=True)
    parser.add_argument("--provider", choices=("local_hash", "local_bge_m3", "openai"), required=True)
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    documents, cases = build_engineering_benchmark_v1()
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
        expected = len(documents)
        parsed = client.post("/parser/process", params={"limit": 100})
        parsed.raise_for_status()
        indexed = client.post("/knowledge/process", params={"limit": 100})
        indexed.raise_for_status()
        if parsed.json()["processed"] != expected or indexed.json()["processed"] != expected:
            raise RuntimeError("Benchmark corpus did not fully parse/index")

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

    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
