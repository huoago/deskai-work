# Phase 21 — Recovery evidence export and incident package

Status: implementation complete on the Phase 21 verification branch; full CI and Windows artifact verification pending.

## Objective

Phase 21 turns the read-only recovery diagnostics built in Phase 18–20 into a portable support/audit package without creating a new file-recovery or mutation path.

A Recovery Center transaction can export a ZIP evidence package that contains:

- transaction metadata already visible in Recovery Center;
- Phase 18 structured diagnostic data when present;
- a live Phase 19 snapshot when the transaction is still recovery_required;
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

ZIP files are written only through the existing generated-artifact sandbox under DeskAI's data directory and are registered as GeneratedArtifact records with kind recovery_evidence.

The caller does not choose an arbitrary output path.

## API

- GET /recovery-evidence/capabilities
- POST /recovery/{entity_type}/{transaction_id}/evidence-package

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

Final test count, CI run, Windows installers, artifact id/hash, PR number and merge SHA will be recorded after verification.
