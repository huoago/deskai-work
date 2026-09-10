from __future__ import annotations

from app.security.secrets import SecretStore


def test_secret_store_environment_key_has_priority_and_never_leaks_value(monkeypatch):
    secret = "sk-test-environment-secret-12345678901234567890"
    monkeypatch.setenv("OPENAI_API_KEY", secret)

    store = SecretStore()
    assert store.get_openai_api_key() == secret

    status = store.status().as_dict()
    assert status["configured"] is True
    assert status["source"] == "environment"
    assert status["writable"] is False
    assert secret not in repr(status)
    assert "api_key" not in status


def test_secret_store_rejects_short_user_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    store = SecretStore()

    try:
        store.set_openai_api_key("short")
    except ValueError as exc:
        assert "too short" in str(exc)
    else:
        raise AssertionError("Short API key should be rejected")
