param([int]$Port = 18765)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Binary = Join-Path $RepoRoot "apps\desktop\src-tauri\binaries\deskai-engine-x86_64-pc-windows-msvc.exe"
if (!(Test-Path $Binary)) { throw "Missing engine sidecar: $Binary" }

$Token = [Guid]::NewGuid().ToString()
$TempRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [System.IO.Path]::GetTempPath() }
$Stdout = Join-Path $TempRoot "deskai-engine-smoke-stdout.log"
$Stderr = Join-Path $TempRoot "deskai-engine-smoke-stderr.log"
Remove-Item -Force -ErrorAction SilentlyContinue $Stdout, $Stderr

$process = Start-Process -FilePath $Binary -ArgumentList @("--host", "127.0.0.1", "--port", "$Port", "--session-token", $Token) -PassThru -WindowStyle Hidden -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr

function Write-EngineLogs {
  if (Test-Path $Stdout) {
    Write-Host "----- deskai-engine stdout -----"
    Get-Content $Stdout -ErrorAction SilentlyContinue | Write-Host
  }
  if (Test-Path $Stderr) {
    Write-Host "----- deskai-engine stderr -----"
    Get-Content $Stderr -ErrorAction SilentlyContinue | Write-Host
  }
}

try {
  # PyInstaller --onefile must extract the bundled Python runtime, LanceDB,
  # Arrow, OpenAI SDK and credential backend before the loopback API starts.
  # Hosted Windows runners can be materially slower than end-user machines.
  $deadline = (Get-Date).AddSeconds(75)
  $health = $null

  do {
    if ($process.HasExited) {
      Write-EngineLogs
      throw "Packaged deskai-engine exited before becoming healthy (exit code $($process.ExitCode))."
    }

    Start-Sleep -Milliseconds 500
    try {
      $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 3
    } catch {
      $health = $null
    }
  } while (($null -eq $health -or $health.status -ne "ok") -and (Get-Date) -lt $deadline)

  if ($null -eq $health -or $health.status -ne "ok") {
    Write-EngineLogs
    throw "Packaged deskai-engine did not become healthy within 75 seconds."
  }

  Write-Host "Packaged engine health OK: $($health.version)"

  try {
    $provider = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/providers/openai/status" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 5
  } catch {
    Write-EngineLogs
    throw "Packaged OpenAI provider status endpoint failed: $($_.Exception.Message)"
  }

  if ($null -eq $provider.configured -or [string]::IsNullOrWhiteSpace([string]$provider.model)) {
    Write-EngineLogs
    throw "Packaged provider endpoint returned an invalid payload."
  }

  Write-Host "Packaged OpenAI provider endpoint OK: configured=$($provider.configured), model=$($provider.model)"

  try {
    $agent = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/agent/status" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 5
  } catch {
    Write-EngineLogs
    throw "Packaged Agent status endpoint failed: $($_.Exception.Message)"
  }

  if ($null -eq $agent.running -or $null -eq $agent.task_counts) {
    Write-EngineLogs
    throw "Packaged Agent status endpoint returned an invalid payload."
  }

  Write-Host "Packaged Agent endpoint OK: running=$($agent.running)"

  if ([string]$health.version -ne "0.13.0") {
    Write-EngineLogs
    throw "Expected packaged engine version 0.13.0, got $($health.version)."
  }

  try {
    $artifacts = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/artifacts" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 5
  } catch {
    Write-EngineLogs
    throw "Packaged Artifact API failed: $($_.Exception.Message)"
  }

  Write-Host "Packaged Artifact endpoint OK: count=$(@($artifacts).Count)"

  try {
    $fileEdits = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/file-edits" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 5
  } catch {
    Write-EngineLogs
    throw "Packaged Source Edit API failed: $($_.Exception.Message)"
  }

  Write-Host "Packaged Source Edit endpoint OK: count=$(@($fileEdits).Count)"

  try {
    $editBatches = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/file-edit-batches" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 5
  } catch {
    Write-EngineLogs
    throw "Packaged Source Edit Batch API failed: $($_.Exception.Message)"
  }

  Write-Host "Packaged Source Edit Batch endpoint OK: count=$(@($editBatches).Count)"

  try {
    $fileOperations = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/file-operations" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 5
  } catch {
    Write-EngineLogs
    throw "Packaged File Organization API failed: $($_.Exception.Message)"
  }

  Write-Host "Packaged File Organization endpoint OK: count=$(@($fileOperations).Count)"
} finally {
  if (!$process.HasExited) {
    Stop-Process -Id $process.Id -Force
    $process.WaitForExit()
  }
}
