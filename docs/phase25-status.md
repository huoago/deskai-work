# Phase 25 — Production Hardening & Release Readiness

Status: in progress on `phase25-release-readiness`.

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

## Current version note

The desktop package version remains `0.1.0`, while the packaged Engine reports `0.24.0`. Phase 25 treats those as separate version domains and records both in release evidence instead of silently forcing them to match.

## Non-goals

Phase 25 does not add unattended auto-update, code signing secrets, remote execution, network-wide Engine binding, destructive filesystem authority, or automatic production publication.

## Verification target

- Ruff
- full Engine pytest suite
- TypeScript typecheck
- Vite production build
- packaged Engine smoke
- Windows NSIS/MSI build
- checksum generation
- CI artifact upload
- release workflow syntax/review

Final CI run IDs, artifact IDs, hashes, PR merge SHA, and final `main` SHA will be recorded during closeout.
