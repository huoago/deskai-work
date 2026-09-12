# Phase 21 — Recovery evidence export and incident package

Status: complete, fully verified, and merged to `main`.

## Objective

Phase 21 turns the read-only recovery diagnostics built in Phase 18–20 into a portable support/audit package without creating a new file-recovery or mutation path.

A Recovery Center transaction can export a ZIP evidence package that contains:

- transaction metadata already visible in Recovery Center;
- Phase 18 structured diagnostic data when present;
- a live Phase 19 snapshot when the transaction is still `recovery_required`;
- Phase 20 reconciliation proposal/history records;
- the related Task audit timeline;
- a human-readable Markdown summary;
- a manifest with SHA-256 and size for every package member.

## Deliberate exclusions

The package never embeds:

- Workspace file bytes;
- staged source-edit candidate bytes;
- automatic backup bytes;
- recycle quarantine bytes.

Export does not modify Workspace files, transaction status, reconciliation status, backup/quarantine content, root permissions, or any rollback/restore state machine.

## Storage boundary

ZIP files are written only through the existing generated-artifact sandbox under DeskAI's data directory and are registered as `GeneratedArtifact` records with kind `recovery_evidence`.

The caller does not choose an arbitrary output path. Evidence packages are limited to 10 MiB of metadata content before artifact registration.

## API

- `GET /recovery-evidence/capabilities`
- `POST /recovery/{entity_type}/{transaction_id}/evidence-package`

Only transactions currently visible in Recovery Center can be exported. Transactional batch members cannot be exported independently; the parent batch must be used.

## Desktop

Recovery Center exposes a per-transaction evidence export action and shows the generated filename, local DeskAI-generated-artifact path and ZIP SHA-256.

## Security invariants

- no Agent evidence-export tool;
- no arbitrary source or destination paths;
- no user-file contents in the ZIP;
- no force overwrite or ignore-SHA behavior;
- no rollback/restore execution;
- no reconciliation confirmation;
- no backup/quarantine purge;
- no transaction-state mutation;
- batch atomicity remains authoritative.

## Verification results

The first CI attempt, Run #106, passed the desktop build but Ruff correctly rejected two unnecessary f-string prefixes in the new evidence summary. The two `F541` findings were corrected before tests or Windows packaging were accepted as Phase 21 verification.

Final feature PR #37 verification passed on CI Run #107 (run id `34691306962`):

- npm install — passed;
- TypeScript typecheck — passed;
- Vite production build — passed;
- Ruff — **All checks passed**;
- full Engine Pytest suite — **143 passed, 59 warnings in 56.67s**;
- Windows PyInstaller sidecar build — passed;
- packaged Engine **0.21.0** health smoke — passed;
- packaged Recovery Evidence capability/API smoke — passed with schema `deskai-recovery-evidence-v1`;
- Tauri Windows NSIS build — passed;
- Tauri Windows MSI build — passed;
- Windows Artifact upload — passed.

Verified packaged smoke included:

- `Packaged engine health OK: 0.21.0`;
- `Packaged Recovery Evidence endpoint OK: schema=deskai-recovery-evidence-v1`.

Verified Windows installers:

- `DeskAI Work_0.1.0_x64-setup.exe`;
- `DeskAI Work_0.1.0_x64_en-US.msi`.

Verified Windows Artifact:

- name: `DeskAI-Work-Windows`;
- artifact id: `10297415235`;
- size: `394445265` bytes;
- SHA-256: `7c6f8b4b19a215fc20835cb2e0df152b084614d528bc1f1d3a2679f4cb1cf574`;
- feature head SHA: `2489d12d7cbe96fd8374f61b94dfadf04bcd1adc`.

Feature PR #37 was squash merged to `main` as:

`241c7897c24c13c6b09d6a923fe6e4a239c53fa2`
