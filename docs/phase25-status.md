# Phase 25 — Production Hardening & Release Readiness

Status: complete, verified, and merged to `main`.

## Objective

Move DeskAI Work from feature-complete internal builds toward a controlled, repeatable Windows release process without expanding runtime authority.

## Phase 25 release gates

1. CI runs with explicit least-privilege repository permissions.
2. Engine, web, and Windows jobs have bounded timeouts.
3. Windows CI fails if expected installers are missing.
4. Every Windows artifact carries SHA-256 checksums.
5. A manual release workflow must re-run the full verification suite before publication.
6. A release tag must match the Tauri desktop version exactly.
7. Every release carries a manifest with source commit and Engine version.
8. GitHub Release publication is manual only; no push or PR may publish a release automatically.
9. Release artifacts include NSIS, MSI, checksums, and a release manifest.
10. Existing PermissionGate, proposal gates, workspace write boundaries, recovery semantics, and Phase 11–24 safety controls remain authoritative.

## Implemented in this phase

### CI hardening

- `permissions: contents: read` for normal CI.
- bounded job timeouts for Engine, desktop-web, and windows-native.
- installer existence is verified before artifact upload.
- SHA-256 checksum evidence is generated for `.exe` and `.msi` outputs.
- artifact upload uses `if-no-files-found: error` and a defined retention period.

### Controlled release workflow

`.github/workflows/release.yml` adds an explicit `workflow_dispatch` release path.

The release job:

- validates `v<desktop-version>` against `tauri.conf.json`;
- runs Ruff and the full Engine test suite;
- runs TypeScript typecheck and Vite production build;
- builds and smoke-tests the packaged Engine;
- builds NSIS and MSI installers;
- produces SHA-256 checksums;
- produces `RELEASE-MANIFEST.txt` containing tag, source commit, Engine version, and UTC generation time;
- uploads release evidence as a retained Actions artifact;
- creates a GitHub Release only after all prior verification succeeds;
- defaults manual releases to prerelease.

## Version domains

The desktop package version remains `0.1.0`, while the packaged Engine reports `0.24.0`. Phase 25 treats those as separate version domains and records both in release evidence instead of silently forcing them to match.

## Final verification

- Feature PR: #45 — Phase 25 — Production Hardening & Release Readiness.
- Feature head: `514c3183873724ec432dadad100d1e073ac9af82`.
- Feature merge SHA: `0fefe99e20e5c2ed68dfcb246156aeb27bad83fc`.
- CI: Run #136, workflow run ID `34705461718`, completed with `success`.
- Engine CI: success.
- Desktop web CI: success.
- Windows native CI: success.
- Packaged Engine smoke: `0.24.0`.
- Windows installers produced:
  - `DeskAI Work_0.1.0_x64-setup.exe`
  - `DeskAI Work_0.1.0_x64_en-US.msi`
- Installer checksum generation: success; `SHA256SUMS.txt` included in the uploaded artifact.
- Windows artifact: `DeskAI-Work-Windows`.
- Artifact ID: `10301840818`.
- Artifact size: `394625430` bytes.
- Artifact ZIP SHA-256: `2b19cba25af1bc3739dce7534e96dd6704d4d200efb1c225e074be0e68fe2e7e`.
- Artifact created: `2026-09-12T16:45:57Z`.
- Artifact retention expiry: `2026-10-12T16:45:44Z`.
- Upload evidence confirms exactly three files were included: NSIS installer, MSI installer, and `SHA256SUMS.txt`.
- Normal CI used least-privilege GitHub token permissions: contents read, metadata read.
- No unattended auto-update, signing secrets, remote execution, broad network binding, destructive filesystem authority, or automatic release-on-push behavior was added.

Phase 25 is fully merged to `main`; this closeout records the final release-readiness evidence.
