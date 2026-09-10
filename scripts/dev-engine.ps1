$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Engine = Join-Path $RepoRoot "apps\engine"
Set-Location $Engine
python main.py --host 127.0.0.1 --port 8765
