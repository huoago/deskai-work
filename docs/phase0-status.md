# Phase 0 / early file-foundation status

Date: 2026-09-10

## Verified in the current development environment

- Python source compiles.
- FastAPI engine starts on loopback.
- `/health` returns database status `ok`.
- Non-loopback CLI host (`0.0.0.0`) is rejected.
- Optional per-launch `X-DeskAI-Token` is enforced when the Tauri sidecar supplies a session token.
- Alembic migration creates the V1 metadata schema plus the `chunks_fts` FTS5 virtual table.
- Workspace create/list persists through real SQLite.
- Authorized folder scanner calculates SHA256 for supported files.
- Unsupported/executable files are not queued for indexing.
- Re-scan detects unchanged files by SHA256.
- Deleted files are marked deleted.
- Unix symlink escape test confirms a link pointing outside the authorized root is skipped.
- 9 backend tests pass.
- TypeScript/TSX source was parsed for syntax successfully in the available environment.
- JSON configs parse successfully.

## Implemented but awaiting Windows CI/native verification

- Tauri Rust host.
- Native dialog capability.
- PyInstaller one-file sidecar with bundled Alembic resources.
- Tauri external binary naming for `x86_64-pc-windows-msvc`.
- Native NSIS/MSI build.
- Packaged sidecar smoke test.
- Full frontend dependency installation, TypeScript semantic typecheck and Vite production build.

The current execution environment is Linux and does not contain the Rust toolchain or PyInstaller. These items must not be marked complete until the Windows GitHub Actions job passes.

## Pre-staged next-phase foundation

To keep development moving, the repository already contains the first secure file-ingestion foundation (workspace roots, path containment, scanner, hash and index-job creation). This does **not** mean Phase 2 is accepted; the formal phase gate remains the successful Windows native Phase 0 build.
