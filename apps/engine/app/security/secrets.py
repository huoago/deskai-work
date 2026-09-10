from __future__ import annotations

import os
from dataclasses import asdict, dataclass

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

SERVICE_NAME = "DeskAI Work"
OPENAI_ACCOUNT = "openai_api_key"


@dataclass(slots=True)
class SecretStatus:
    configured: bool
    source: str | None
    credential_store_available: bool
    writable: bool
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class SecretStore:
    """Stores cloud API credentials outside SQLite and the frontend.

    Environment variables are supported for development and enterprise-managed
    deployments. User-entered credentials are stored through the operating
    system keyring; no plaintext fallback is permitted.
    """

    def get_openai_api_key(self) -> str | None:
        env_value = os.environ.get("OPENAI_API_KEY", "").strip()
        if env_value:
            return env_value
        try:
            value = keyring.get_password(SERVICE_NAME, OPENAI_ACCOUNT)
        except KeyringError:
            return None
        return value.strip() if value else None

    def set_openai_api_key(self, value: str) -> None:
        key = value.strip()
        if len(key) < 20:
            raise ValueError("API key is too short")
        try:
            keyring.set_password(SERVICE_NAME, OPENAI_ACCOUNT, key)
        except KeyringError as exc:
            raise RuntimeError(f"Operating-system credential store is unavailable: {exc}") from exc

    def delete_openai_api_key(self) -> bool:
        try:
            keyring.delete_password(SERVICE_NAME, OPENAI_ACCOUNT)
            return True
        except PasswordDeleteError:
            return False
        except KeyringError as exc:
            raise RuntimeError(f"Operating-system credential store is unavailable: {exc}") from exc

    def status(self) -> SecretStatus:
        env_value = os.environ.get("OPENAI_API_KEY", "").strip()
        if env_value:
            return SecretStatus(
                configured=True,
                source="environment",
                credential_store_available=True,
                writable=False,
            )

        try:
            value = keyring.get_password(SERVICE_NAME, OPENAI_ACCOUNT)
            backend_name = type(keyring.get_keyring()).__name__
            priority = getattr(keyring.get_keyring(), "priority", 0)
            available = bool(priority and priority > 0)
            return SecretStatus(
                configured=bool(value),
                source="credential_manager" if value else None,
                credential_store_available=available,
                writable=available,
                error=None if available else f"Unsupported keyring backend: {backend_name}",
            )
        except Exception as exc:
            return SecretStatus(
                configured=False,
                source=None,
                credential_store_available=False,
                writable=False,
                error=f"{type(exc).__name__}: {exc}",
            )
