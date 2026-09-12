from __future__ import annotations


def test_settings_have_defaults_and_persist_updates(client):
    defaults = client.get("/settings")
    assert defaults.status_code == 200
    assert defaults.json() == {
        "privacy_mode": "hybrid",
        "default_model": "gpt-5.6-sol",
        "reasoning_level": "medium",
        "auto_index": True,
        "memory_auto_learn": True,
        "memory_min_confidence": 0.78,
        "embedding_provider": "local_hash",
        "embedding_model": "text-embedding-3-small",
    }

    updated = client.patch(
        "/settings",
        json={
            "privacy_mode": "local",
            "default_model": "custom-provider-model",
            "reasoning_level": "high",
            "auto_index": False,
            "memory_auto_learn": False,
            "memory_min_confidence": 0.91,
            "embedding_provider": "openai",
            "embedding_model": "text-embedding-3-large",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["privacy_mode"] == "local"
    assert updated.json()["default_model"] == "custom-provider-model"
    assert updated.json()["reasoning_level"] == "high"
    assert updated.json()["auto_index"] is False
    assert updated.json()["memory_auto_learn"] is False
    assert updated.json()["memory_min_confidence"] == 0.91
    assert updated.json()["embedding_provider"] == "openai"
    assert updated.json()["embedding_model"] == "text-embedding-3-large"

    persisted = client.get("/settings").json()
    assert persisted == updated.json()
