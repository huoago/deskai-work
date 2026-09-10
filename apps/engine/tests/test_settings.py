from __future__ import annotations


def test_settings_have_defaults_and_persist_updates(client):
    defaults = client.get("/settings")
    assert defaults.status_code == 200
    assert defaults.json() == {
        "privacy_mode": "hybrid",
        "default_model": "gpt-5.6-sol",
        "reasoning_level": "medium",
        "auto_index": True,
    }

    updated = client.patch(
        "/settings",
        json={
            "privacy_mode": "local",
            "default_model": "custom-provider-model",
            "reasoning_level": "high",
            "auto_index": False,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["privacy_mode"] == "local"
    assert updated.json()["default_model"] == "custom-provider-model"
    assert updated.json()["reasoning_level"] == "high"
    assert updated.json()["auto_index"] is False

    persisted = client.get("/settings").json()
    assert persisted == updated.json()
