$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$EngineDir = Join-Path $RepoRoot "apps\engine"
$TauriDir = Join-Path $RepoRoot "apps\desktop\src-tauri"
$BinaryDir = Join-Path $TauriDir "binaries"

# Regenerate a PNG-compressed ICO on every Windows package build. This avoids
# legacy DIB ICO files that modern Windows RC.EXE rejects with RC2176.
python (Join-Path $PSScriptRoot "make-windows-icon.py")
if ($LASTEXITCODE -ne 0) { throw "Failed to generate Windows icon." }

New-Item -ItemType Directory -Force -Path $BinaryDir | Out-Null
Set-Location $EngineDir

python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m PyInstaller `
  --clean `
  --noconfirm `
  --onefile `
  --name deskai-engine `
  --collect-submodules "keyring.backends" `
  --add-data "alembic.ini;." `
  --add-data "migrations;migrations" `
  main.py

$Triple = "x86_64-pc-windows-msvc"
$Source = Join-Path $EngineDir "dist\deskai-engine.exe"
$Target = Join-Path $BinaryDir "deskai-engine-$Triple.exe"
Copy-Item -Force $Source $Target
Write-Host "Sidecar created: $Target"
