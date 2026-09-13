from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.knowledge.embedding import (
    BGE_M3_DIMENSION,
    BGE_M3_MODEL,
    EMBEDDING_DIMENSION,
    EMBEDDING_PROVIDER,
    EmbeddingError,
    EmbeddingService,
    LocalHashEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from app.knowledge.search import _engineering_identifiers, _rerank, _terms


def test_local_hash_provider_remains_deterministic_compatibility_fallback():
    provider = LocalHashEmbeddingProvider()
    first = provider.embed_query("RRP-04 DN1500 324 开阀")
    second = provider.embed_query("RRP-04 DN1500 324 开阀")

    assert provider.model == EMBEDDING_PROVIDER
    assert len(first) == EMBEDDING_DIMENSION
    assert first == second


def test_embedding_service_default_descriptor_marks_hash_as_non_semantic():
    descriptor = EmbeddingService().descriptor()

    assert descriptor.provider == "local_hash"
    assert descriptor.semantic is False
    assert descriptor.signature == EMBEDDING_PROVIDER


def test_openai_provider_batches_documents_without_network(monkeypatch: pytest.MonkeyPatch):
    provider = OpenAIEmbeddingProvider(api_key="x" * 32, model="text-embedding-3-small")
    vector_a = [0.1] * 1536
    vector_b = [0.2] * 1536

    class FakeEmbeddings:
        def create(self, *, model: str, input: list[str]):  # noqa: A002
            assert model == "text-embedding-3-small"
            assert input == ["alpha", "beta"]
            return SimpleNamespace(
                data=[
                    SimpleNamespace(index=1, embedding=vector_b),
                    SimpleNamespace(index=0, embedding=vector_a),
                ]
            )

    provider._client = SimpleNamespace(embeddings=FakeEmbeddings())

    assert provider.embed_documents(["alpha", "beta"]) == [vector_a, vector_b]


def test_openai_provider_rejects_unknown_model():
    with pytest.raises(EmbeddingError):
        OpenAIEmbeddingProvider(api_key="x" * 32, model="not-a-real-model")


def test_phase27_settings_expose_embedding_provider(client):
    response = client.get("/settings")
    assert response.status_code == 200
    assert response.json()["embedding_provider"] == "local_hash"
    assert response.json()["embedding_model"] == "text-embedding-3-small"

    updated = client.patch(
        "/settings",
        json={
            "embedding_provider": "local_bge_m3",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["embedding_provider"] == "local_bge_m3"

    updated = client.patch(
        "/settings",
        json={
            "embedding_provider": "openai",
            "embedding_model": "text-embedding-3-large",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["embedding_provider"] == "openai"
    assert updated.json()["embedding_model"] == "text-embedding-3-large"


def test_phase27_local_model_status_is_explicit_and_does_not_auto_download(client):
    response = client.get("/knowledge/embeddings/local-model")
    assert response.status_code == 200
    payload = response.json()
    assert payload["model"] == BGE_M3_MODEL
    assert payload["installed"] is False
    assert payload["download_required"] is True
    assert payload["model_sha256"]
    assert payload["tokenizer_sha256"]


def test_phase27_local_bge_descriptor_is_semantic_without_forcing_model_load(client):
    updated = client.patch("/settings", json={"embedding_provider": "local_bge_m3"})
    assert updated.status_code == 200

    response = client.get("/knowledge/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["embedding"]["provider"] == "local_bge_m3"
    assert payload["embedding"]["model"] == BGE_M3_MODEL
    assert payload["embedding"]["dimension"] == BGE_M3_DIMENSION
    assert payload["embedding"]["semantic"] is True


def test_phase27_knowledge_status_reports_vector_provider(client):
    response = client.get("/knowledge/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["embedding"]["provider"] == "local_hash"
    assert payload["embedding"]["semantic"] is False
    assert isinstance(payload["vector_index_ready"], bool)


def test_phase27_embedding_rebuild_is_safe_with_empty_index(client):
    response = client.post("/knowledge/embeddings/rebuild?file_limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["files_rebuilt"] == 0
    assert payload["chunks_written"] == 0
    assert payload["provider"]["signature"] == EMBEDDING_PROVIDER


def test_reranker_rewards_query_term_coverage_and_metadata():
    query_terms = _terms("RRP-04 AD69")
    strong = _rerank(
        query_terms,
        "RRP-04 is directly related to AD69 and the retaining wall change.",
        "AD69_RRP-04_report.pdf",
        "RRP-04 / AD69",
    )
    weak = _rerank(query_terms, "generic project progress", "notes.txt", None)

    assert strong > weak
    assert 0.0 <= weak <= strong <= 1.0


def test_engineering_identifier_extraction_handles_common_project_codes():
    identifiers = _engineering_identifiers("Sector S01 connects RRP-04 to DN1500 at VC-03.")

    assert {"s01", "rrp-04", "dn1500", "vc-03"} <= identifiers
