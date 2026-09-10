param([int]$Port = 18765)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Binary = Join-Path $RepoRoot "apps\desktop\src-tauri\binaries\deskai-engine-x86_64-pc-windows-msvc.exe"
if (!(Test-Path $Binary)) { throw "Missing engine sidecar: $Binary" }
$Token = [Guid]::NewGuid().ToString()
$process = Start-Process -FilePath $Binary -ArgumentList @("--host", "127.0.0.1", "--port", "$Port", "--session-token", $Token) -PassThru -WindowStyle Hidden
try {
  $deadline = (Get-Date).AddSeconds(20)
  do {
    Start-Sleep -Milliseconds 500
    try {
      $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 2
      if ($health.status -eq "ok") {
        $provider = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/providers/openai/status" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 3
        if ($null -eq $provider.configured -or $provider.model -ne "gpt-5.6-sol") {
          throw "Packaged provider endpoint returned an invalid payload."
        }
        Write-Host "Packaged engine health OK: $($health.version); OpenAI provider endpoint OK."
        return
      }
    } catch {}
  } while ((Get-Date) -lt $deadline)
  throw "Packaged deskai-engine did not become healthy within 20 seconds."
} finally {
  if (!$process.HasExited) { Stop-Process -Id $process.Id -Force }
}
