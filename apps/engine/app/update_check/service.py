from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GITHUB_RELEASES_API = "https://api.github.com/repos/huoago/deskai-work/releases/latest"
RELEASE_DOWNLOAD_PREFIX = "https://github.com/huoago/deskai-work/releases/download/"
MAX_METADATA_BYTES = 256 * 1024
MAX_TEXT_ASSET_BYTES = 64 * 1024
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class UpdateCheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseCheckResult:
    current_version: str
    latest_version: str | None
    release_tag: str | None
    update_available: bool
    trusted: bool
    release_page_url: str | None
    published_at: str | None
    installer_assets: list[str]
    verification_issues: list[str]
    manifest_sha256: str | None
    checksums_sha256: str | None
    source_commit: str | None
    engine_version: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "release_tag": self.release_tag,
            "update_available": self.update_available,
            "trusted": self.trusted,
            "release_page_url": self.release_page_url,
            "published_at": self.published_at,
            "installer_assets": self.installer_assets,
            "verification_issues": self.verification_issues,
            "manifest_sha256": self.manifest_sha256,
            "checksums_sha256": self.checksums_sha256,
            "source_commit": self.source_commit,
            "engine_version": self.engine_version,
        }


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = SEMVER_RE.fullmatch(value)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def _read_limited(response, limit: int) -> bytes:
    data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateCheckError("Release metadata exceeded the allowed size limit.")
    return data


def _fetch_json(url: str) -> dict:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "DeskAI-Work-Update-Check/1",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=5) as response:
            payload = _read_limited(response, MAX_METADATA_BYTES)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise UpdateCheckError(f"Unable to retrieve release metadata: {exc}") from exc
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateCheckError("Release metadata was not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise UpdateCheckError("Release metadata had an unexpected shape.")
    return value


def _fetch_text_asset(url: str) -> str:
    if not url.startswith(RELEASE_DOWNLOAD_PREFIX):
        raise UpdateCheckError("Release evidence asset URL is outside the DeskAI GitHub release boundary.")
    request = Request(url, headers={"User-Agent": "DeskAI-Work-Update-Check/1"}, method="GET")
    try:
        with urlopen(request, timeout=5) as response:
            payload = _read_limited(response, MAX_TEXT_ASSET_BYTES)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise UpdateCheckError(f"Unable to retrieve release evidence: {exc}") from exc
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UpdateCheckError("Release evidence was not UTF-8 text.") from exc


def _parse_manifest(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key, sep, value = line.partition("=")
        if not sep or not key or not value:
            continue
        result[key.strip()] = value.strip()
    return result


def _parse_checksums(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, filename = parts[0].lower(), parts[1].strip().lstrip("*")
        if SHA256_RE.fullmatch(digest) and filename:
            result[filename] = digest
    return result


class ReleaseVerificationService:
    def check_latest(self, current_version: str) -> ReleaseCheckResult:
        current_tuple = _version_tuple(current_version)
        if current_tuple is None:
            raise UpdateCheckError("Current desktop version must use x.y.z format.")

        release = _fetch_json(GITHUB_RELEASES_API)
        tag = release.get("tag_name")
        html_url = release.get("html_url")
        published_at = release.get("published_at")
        assets = release.get("assets")
        issues: list[str] = []

        if not isinstance(tag, str) or not tag.startswith("v"):
            tag = None
            issues.append("release_tag_missing_or_invalid")
        candidate_version = tag[1:] if tag else None
        candidate_tuple = _version_tuple(candidate_version) if candidate_version else None
        if candidate_tuple is None:
            issues.append("release_version_not_semver")

        asset_rows = assets if isinstance(assets, list) else []
        by_name: dict[str, str] = {}
        installer_assets: list[str] = []
        for row in asset_rows:
            if not isinstance(row, dict):
                continue
            name = row.get("name")
            download_url = row.get("browser_download_url")
            if isinstance(name, str) and isinstance(download_url, str):
                by_name[name] = download_url
                if name.lower().endswith((".exe", ".msi")):
                    installer_assets.append(name)

        if not installer_assets:
            issues.append("windows_installers_missing")
        manifest_url = by_name.get("RELEASE-MANIFEST.txt")
        checksums_url = by_name.get("SHA256SUMS.txt")
        if manifest_url is None:
            issues.append("release_manifest_missing")
        if checksums_url is None:
            issues.append("checksums_missing")

        manifest_text = ""
        checksums_text = ""
        manifest: dict[str, str] = {}
        checksums: dict[str, str] = {}
        if manifest_url:
            try:
                manifest_text = _fetch_text_asset(manifest_url)
                manifest = _parse_manifest(manifest_text)
            except UpdateCheckError:
                issues.append("release_manifest_unreadable")
        if checksums_url:
            try:
                checksums_text = _fetch_text_asset(checksums_url)
                checksums = _parse_checksums(checksums_text)
            except UpdateCheckError:
                issues.append("checksums_unreadable")

        if tag and manifest:
            if manifest.get("tag") != tag:
                issues.append("manifest_tag_mismatch")
            if candidate_version and manifest.get("desktop_version") != candidate_version:
                issues.append("manifest_desktop_version_mismatch")
            source_commit = manifest.get("commit")
            if source_commit is None or COMMIT_RE.fullmatch(source_commit.lower()) is None:
                issues.append("manifest_commit_invalid")
            engine_version = manifest.get("engine_version")
            if not engine_version:
                issues.append("manifest_engine_version_missing")
        else:
            source_commit = None
            engine_version = None

        for installer_name in installer_assets:
            if installer_name not in checksums:
                issues.append(f"checksum_missing:{installer_name}")

        trusted = len(issues) == 0
        update_available = bool(candidate_tuple and candidate_tuple > current_tuple)
        return ReleaseCheckResult(
            current_version=current_version,
            latest_version=candidate_version,
            release_tag=tag,
            update_available=update_available,
            trusted=trusted,
            release_page_url=html_url if isinstance(html_url, str) else None,
            published_at=published_at if isinstance(published_at, str) else None,
            installer_assets=sorted(installer_assets),
            verification_issues=issues,
            manifest_sha256=(hashlib.sha256(manifest_text.encode("utf-8")).hexdigest() if manifest_text else None),
            checksums_sha256=(hashlib.sha256(checksums_text.encode("utf-8")).hexdigest() if checksums_text else None),
            source_commit=source_commit,
            engine_version=engine_version,
        )
