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

  if ([string]$health.version -ne "0.24.0") {
    Write-EngineLogs
    throw "Expected packaged engine version 0.24.0, got $($health.version)."
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

  try {
    $fileOperationBatches = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/file-operation-batches" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 5
  } catch {
    Write-EngineLogs
    throw "Packaged File Organization Batch API failed: $($_.Exception.Message)"
  }

  Write-Host "Packaged File Organization Batch endpoint OK: count=$(@($fileOperationBatches).Count)"

  try {
    $recycleProposals = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/recycle-proposals" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 5
  } catch {
    Write-EngineLogs
    throw "Packaged Recycle Bin API failed: $($_.Exception.Message)"
  }

  Write-Host "Packaged Recycle Bin endpoint OK: count=$(@($recycleProposals).Count)"

  try {
    $recycleBatches = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/recycle-batches" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 5
  } catch {
    Write-EngineLogs
    throw "Packaged Recycle Batch API failed: $($_.Exception.Message)"
  }

  Write-Host "Packaged Recycle Batch endpoint OK: count=$(@($recycleBatches).Count)"

  try {
    $recovery = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/recovery" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 5
  } catch {
    Write-EngineLogs
    throw "Packaged Recovery Center API failed: $($_.Exception.Message)"
  }

  Write-Host "Packaged Recovery Center endpoint OK: count=$(@($recovery).Count)"

  try {
    $openApi = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/openapi.json" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 5
  } catch {
    Write-EngineLogs
    throw "Packaged OpenAPI endpoint failed: $($_.Exception.Message)"
  }

  $snapshotRoute = "/recovery/{entity_type}/{transaction_id}/snapshot"
  if ($null -eq $openApi.paths.PSObject.Properties[$snapshotRoute]) {
    Write-EngineLogs
    throw "Packaged Recovery Snapshot route is missing from OpenAPI."
  }

  Write-Host "Packaged Recovery Snapshot route OK"

  $reconciliationListRoute = "/recovery-reconciliations"
  $reconciliationConfirmRoute = "/recovery-reconciliations/{proposal_id}/confirm"
  if ($null -eq $openApi.paths.PSObject.Properties[$reconciliationListRoute]) {
    Write-EngineLogs
    throw "Packaged Recovery Reconciliation list route is missing from OpenAPI."
  }
  if ($null -eq $openApi.paths.PSObject.Properties[$reconciliationConfirmRoute]) {
    Write-EngineLogs
    throw "Packaged Recovery Reconciliation confirm route is missing from OpenAPI."
  }

  try {
    $reconciliations = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/recovery-reconciliations" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 5
  } catch {
    Write-EngineLogs
    throw "Packaged Recovery Reconciliation API failed: $($_.Exception.Message)"
  }

  Write-Host "Packaged Recovery Reconciliation endpoint OK: count=$(@($reconciliations).Count)"

  $evidenceRoute = "/recovery/{entity_type}/{transaction_id}/evidence-package"
  if ($null -eq $openApi.paths.PSObject.Properties[$evidenceRoute]) {
    Write-EngineLogs
    throw "Packaged Recovery Evidence route is missing from OpenAPI."
  }

  try {
    $evidenceCapabilities = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/recovery-evidence/capabilities" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 5
  } catch {
    Write-EngineLogs
    throw "Packaged Recovery Evidence capabilities endpoint failed: $($_.Exception.Message)"
  }

  if ([string]$evidenceCapabilities.schema -ne "deskai-recovery-evidence-v1" -or $evidenceCapabilities.user_files_modified -ne $false) {
    Write-EngineLogs
    throw "Packaged Recovery Evidence capabilities returned an invalid payload."
  }

  Write-Host "Packaged Recovery Evidence endpoint OK: schema=$($evidenceCapabilities.schema)"

  $workPlanListRoute = "/work-plans"
  $workPlanDraftRoute = "/tasks/{task_id}/work-plan"
  $workPlanResumeRoute = "/work-plans/{plan_id}/resume"
  if ($null -eq $openApi.paths.PSObject.Properties[$workPlanListRoute]) {
    Write-EngineLogs
    throw "Packaged Work Plan list route is missing from OpenAPI."
  }
  if ($null -eq $openApi.paths.PSObject.Properties[$workPlanDraftRoute]) {
    Write-EngineLogs
    throw "Packaged Work Plan draft route is missing from OpenAPI."
  }
  if ($null -eq $openApi.paths.PSObject.Properties[$workPlanResumeRoute]) {
    Write-EngineLogs
    throw "Packaged Work Plan resume route is missing from OpenAPI."
  }

  try {
    $workPlans = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/work-plans" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 5
  } catch {
    Write-EngineLogs
    throw "Packaged Work Plan API failed: $($_.Exception.Message)"
  }

  Write-Host "Packaged Work Plan endpoint OK: count=$(@($workPlans).Count)"

  $workPlanWorkerStatusRoute = "/work-plan-worker/status"
  $workPlanWorkerProcessRoute = "/work-plan-worker/process"
  if ($null -eq $openApi.paths.PSObject.Properties[$workPlanWorkerStatusRoute]) {
    Write-EngineLogs
    throw "Packaged Work Plan Worker status route is missing from OpenAPI."
  }
  if ($null -eq $openApi.paths.PSObject.Properties[$workPlanWorkerProcessRoute]) {
    Write-EngineLogs
    throw "Packaged Work Plan Worker process route is missing from OpenAPI."
  }

  try {
    $workPlanWorker = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/work-plan-worker/status" -Headers @{"X-DeskAI-Token"=$Token} -TimeoutSec 5
  } catch {
    Write-EngineLogs
    throw "Packaged Work Plan Worker status endpoint failed: $($_.Exception.Message)"
  }

  if ($null -eq $workPlanWorker.processed -or $null -eq $workPlanWorker.running) {
    Write-EngineLogs
    throw "Packaged Work Plan Worker status returned an invalid payload."
  }

  Write-Host "Packaged Work Plan Worker OK: running=$($workPlanWorker.running); processed=$($workPlanWorker.processed)"

  $workPlanSupervisionRoute = "/work-plans/{plan_id}/supervision"
  $workPlanPauseRoute = "/work-plans/{plan_id}/pause"
  $workPlanContinueRoute = "/work-plans/{plan_id}/continue"
  $workPlanApproveRoute = "/work-plans/{plan_id}/steps/{step_id}/approve"
  $workPlanSkipRoute = "/work-plans/{plan_id}/steps/{step_id}/skip"
  $workPlanEventsRoute = "/work-plans/{plan_id}/events"
  $workPlanNotificationsRoute = "/work-plans/{plan_id}/notifications"

  foreach ($route in @(
    $workPlanSupervisionRoute,
    $workPlanPauseRoute,
    $workPlanContinueRoute,
    $workPlanApproveRoute,
    $workPlanSkipRoute,
    $workPlanEventsRoute,
    $workPlanNotificationsRoute
  )) {
    if ($null -eq $openApi.paths.PSObject.Properties[$route]) {
      Write-EngineLogs
      throw "Packaged Phase 24 supervision route is missing: $route"
    }
  }

  if ($null -eq $workPlanWorker.awaiting_step_approval -or $null -eq $workPlanWorker.paused) {
    Write-EngineLogs
    throw "Packaged Work Plan Worker status is missing Phase 24 supervision counters."
  }

  Write-Host "Packaged Work Plan Supervision routes OK"
} finally {
  if (!$process.HasExited) {
    Stop-Process -Id $process.Id -Force
    $process.WaitForExit()
  }
}
