# Phase 26 — Controlled Update & Release Verification

Status: implementation complete on `phase26-controlled-update-verification`; pending CI and merge.

## Objective

Phase 26 connects the Phase 25 controlled Windows release pipeline to an explicit, human-triggered desktop update check without introducing unattended download or installation authority.

The phase answers four questions before a user considers an update:

1. What desktop version is currently installed?
2. Is a newer stable GitHub Release available?
3. Does that release carry internally consistent DeskAI release evidence?
4. Where is the official release page for a human-controlled next action?

## User-triggered update check

The desktop exposes a small update control mounted independently of the existing `App.tsx` task/file state machines.

- no startup update request;
- no timer or background polling;
- the request is made only after the user presses **检查更新**;
- Local Only privacy mode blocks the request before any GitHub access;
- a failed or malformed check does not change local files, settings, tasks, plans, or permissions.

The desktop sends only the current application version to the local Engine endpoint:

`POST /updates/check`

## Release verification

The Engine reads only the public `huoago/deskai-work` latest stable GitHub Release metadata and two small release-evidence text assets:

- `RELEASE-MANIFEST.txt`
- `SHA256SUMS.txt`

It does **not** download the `.exe` or `.msi` installers.

The response validates:

- release tag uses `vX.Y.Z`;
- candidate desktop version uses strict `X.Y.Z` SemVer form;
- at least one Windows installer exists;
- both release-evidence files exist and are readable within bounded size limits;
- manifest tag equals the GitHub release tag;
- manifest `desktop_version` equals the release version;
- manifest source commit is a 40-character SHA-1 commit id;
- manifest contains an Engine version;
- every published `.exe` / `.msi` installer filename has a SHA-256 entry.

The service also returns SHA-256 hashes of the fetched manifest and checksum-list text as inspection evidence.

`trusted=true` means the GitHub release evidence is internally consistent under the above checks. It is not a substitute for Windows Authenticode/code-signing identity, which remains outside Phase 26.

## Release pipeline change

Phase 25 already generated release manifests and installer checksums. Phase 26 adds:

`desktop_version=<tauri.conf.json version>`

to `RELEASE-MANIFEST.txt`, allowing the desktop-side verifier to bind the tag to the packaged desktop version.

## Desktop behavior

The update panel shows:

- installed desktop version;
- latest stable release version;
- release-evidence status;
- packaged Engine version reported by the manifest;
- any verification issues;
- whether a newer version exists.

Only when both `update_available=true` and `trusted=true` does the UI offer to copy the official GitHub Release page URL for the user.

DeskAI does not open an installer, download an installer, execute an installer, replace application files, or restart itself.

## Network boundary

Phase 26 introduces one narrowly scoped public-network operation:

- fixed GitHub Releases API endpoint for `huoago/deskai-work`;
- fixed DeskAI GitHub release-download prefix for evidence text files;
- 5-second request timeout;
- 256 KiB maximum release metadata;
- 64 KiB maximum per evidence text asset;
- no caller-supplied URL;
- no arbitrary URL fetch;
- no installer download.

## Safety invariants

- update checks are user-triggered only;
- Local Only blocks update networking;
- release URLs are fixed to the DeskAI GitHub repository boundary;
- no credentials are sent to GitHub;
- no Workspace content is sent to GitHub;
- no existing ToolRegistry or PermissionGate authority is expanded;
- no file write, overwrite, delete, recycle, restore, rollback, shell, browser-control, or installer-execution authority is added;
- no automatic updater or silent installation exists;
- failed verification is fail-closed for the recommended-update UI;
- current Phase 11–24 confirmation and recovery state machines remain authoritative.

## Verification target

- Ruff across Engine;
- full Engine pytest suite including Phase 26 release-evidence tests;
- Local Only API blocking test;
- TypeScript typecheck including the new update control/client;
- Vite production build;
- packaged Engine smoke;
- Windows NSIS/MSI build;
- checksum generation and artifact upload.

Final CI run, artifact metadata, merge SHA, and final `main` SHA will be recorded during closeout.
