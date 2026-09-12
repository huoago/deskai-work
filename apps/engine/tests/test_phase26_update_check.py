from __future__ import annotations

import pytest

from app.update_check import service as update_service
from app.update_check.service import ReleaseVerificationService, UpdateCheckError


def _release_payload(*, tag: str = "v0.2.0") -> dict:
    base = f"https://github.com/huoago/deskai-work/releases/download/{tag}"
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/huoago/deskai-work/releases/tag/{tag}",
        "published_at": "2026-09-13T00:00:00Z",
        "assets": [
            {
                "name": "DeskAI Work_0.2.0_x64-setup.exe",
                "browser_download_url": f"{base}/DeskAI.Work_0.2.0_x64-setup.exe",
            },
            {
                "name": "DeskAI Work_0.2.0_x64_en-US.msi",
                "browser_download_url": f"{base}/DeskAI.Work_0.2.0_x64_en-US.msi",
            },
            {
                "name": "SHA256SUMS.txt",
                "browser_download_url": f"{base}/SHA256SUMS.txt",
            },
            {
                "name": "RELEASE-MANIFEST.txt",
                "browser_download_url": f"{base}/RELEASE-MANIFEST.txt",
            },
        ],
    }


def _manifest() -> str:
    return "\n".join(
        [
            "tag=v0.2.0",
            "commit=0123456789abcdef0123456789abcdef01234567",
            "desktop_version=0.2.0",
            "engine_version=0.26.0",
            "generated_utc=2026-09-13T00:00:00Z",
        ]
    )


def _checksums(*, include_msi: bool = True) -> str:
    lines = [
        f"{'a' * 64}  DeskAI Work_0.2.0_x64-setup.exe",
    ]
    if include_msi:
        lines.append(f"{'b' * 64}  DeskAI Work_0.2.0_x64_en-US.msi")
    return "\n".join(lines)


def test_release_check_marks_complete_evidence_trusted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(update_service, "_fetch_json", lambda _: _release_payload())
    monkeypatch.setattr(
        update_service,
        "_fetch_text_asset",
        lambda url: _manifest() if url.endswith("RELEASE-MANIFEST.txt") else _checksums(),
    )

    result = ReleaseVerificationService().check_latest("0.1.0")

    assert result.update_available is True
    assert result.trusted is True
    assert result.latest_version == "0.2.0"
    assert result.source_commit == "0123456789abcdef0123456789abcdef01234567"
    assert result.engine_version == "0.26.0"
    assert result.verification_issues == []
    assert len(result.installer_assets) == 2
    assert result.manifest_sha256 is not None
    assert result.checksums_sha256 is not None


def test_release_check_rejects_missing_installer_checksum(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(update_service, "_fetch_json", lambda _: _release_payload())
    monkeypatch.setattr(
        update_service,
        "_fetch_text_asset",
        lambda url: _manifest() if url.endswith("RELEASE-MANIFEST.txt") else _checksums(include_msi=False),
    )

    result = ReleaseVerificationService().check_latest("0.1.0")

    assert result.trusted is False
    assert "checksum_missing:DeskAI Work_0.2.0_x64_en-US.msi" in result.verification_issues


def test_release_check_requires_semver_current_version():
    with pytest.raises(UpdateCheckError):
        ReleaseVerificationService().check_latest("dev")


def test_update_check_is_blocked_in_local_only_mode(client):
    settings_response = client.patch("/settings", json={"privacy_mode": "local"})
    assert settings_response.status_code == 200

    response = client.post("/updates/check", json={"current_version": "0.1.0"})

    assert response.status_code == 400
    assert response.json()["code"] == "UPDATE_CHECK_BLOCKED_LOCAL_ONLY"
