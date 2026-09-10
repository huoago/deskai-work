$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$EngineDir = Join-Path $RepoRoot "apps\engine"
$TauriDir = Join-Path $RepoRoot "apps\desktop\src-tauri"
$BinaryDir = Join-Path $TauriDir "binaries"

New-Item -ItemType Directory -Force -Path $BinaryDir | Out-Null
Set-Location $EngineDir

python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m PyInstaller `
  --clean `
  --noconfirm `
  --onefile `
  --name deskai-engine `
  --add-data "alembic.ini;." `
  --add-data "migrations;migrations" `
  main.py

$Triple = "x86_64-pc-windows-msvc"
$Source = Join-Path $EngineDir "dist\deskai-engine.exe"
$Target = Join-Path $BinaryDir "deskai-engine-$Triple.exe"
Copy-Item -Force $Source $Target
Write-Host "Sidecar created: $Target"
