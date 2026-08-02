param(
  [string]$RuntimeRoot = "C:\Aarav\cipher-system\CipherCapture",
  [int]$CheckIntervalSeconds = 60,
  [int]$MaxAgeSeconds = 120
)

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SystemRoot = Split-Path -Parent $ScriptDir
$LogDir = Join-Path $RuntimeRoot "logs"
$LogPath = Join-Path $LogDir "fronttest_supervisor.log"
$RuntimePython = Join-Path $RuntimeRoot ".venv\Scripts\python.exe"
$RuntimePythonw = Join-Path $RuntimeRoot ".venv\Scripts\pythonw.exe"
$RuntimeConfig = Join-Path $RuntimeRoot "config\paper-executor.yaml"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
if (-not (Test-Path $RuntimePython)) {
  $RuntimePython = (Get-Command python -ErrorAction Stop).Source
}
if (-not (Test-Path $RuntimePythonw)) {
  $RuntimePythonw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
}
if ([string]::IsNullOrWhiteSpace($RuntimePythonw)) {
  $RuntimePythonw = $RuntimePython
}

function Write-SupervisorLog {
  param([string]$Message)
  $line = "$(Get-Date -Format o) $Message"
  Add-Content -Path $LogPath -Value $line -Encoding utf8
}

function Test-PaperExecutorHealthy {
  try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8787/health" -TimeoutSec 3
    return [bool]($health.ok -and $health.mode -eq "shadow" -and $health.paper_only)
  } catch {
    return $false
  }
}

function Ensure-PaperExecutor {
  if (Test-PaperExecutorHealthy) {
    return
  }
  Write-SupervisorLog "starting paper executor"
  try {
    Start-Process `
      -FilePath $RuntimePythonw `
      -ArgumentList "-m core.paper_executor.service `"$RuntimeConfig`"" `
      -WorkingDirectory $SystemRoot `
      -WindowStyle Hidden
  } catch {
    Write-SupervisorLog "paper executor start failed: $($_.Exception.Message)"
  }
}

function Ensure-CaptureFileIngest {
  $ingest = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -like "python*" -and $_.CommandLine -like "*core.paper_executor.capture_files*" } |
    Select-Object -First 1
  if ($ingest) {
    return
  }
  Write-SupervisorLog "starting capture file ingest"
  try {
    Start-Process `
      -FilePath $RuntimePython `
      -ArgumentList "-m core.paper_executor.capture_files --root `"$RuntimeRoot`" --url http://127.0.0.1:8787/api/scanner-ingest --max-age-seconds $MaxAgeSeconds --include-uploaded --watch" `
      -WorkingDirectory $SystemRoot `
      -WindowStyle Hidden
    Write-SupervisorLog "capture file ingest launch requested"
  } catch {
    Write-SupervisorLog "capture file ingest start failed: $($_.Exception.Message)"
  }
}

function Ensure-ClusterForwardTest {
  $clusterForward = Get-CimInstance Win32_Process |
    Where-Object { $_.Name -like "python*" -and $_.CommandLine -like "*core.paper_executor.cluster_forward_test*" } |
    Select-Object -First 1
  if ($clusterForward) {
    return
  }
  Write-SupervisorLog "starting cluster forward test"
  try {
    Start-Process `
      -FilePath $RuntimePython `
      -ArgumentList "-m core.paper_executor.cluster_forward_test --root `"$RuntimeRoot`" --interval-seconds 30 --watch" `
      -WorkingDirectory $SystemRoot `
      -WindowStyle Hidden
    Write-SupervisorLog "cluster forward test launch requested"
  } catch {
    Write-SupervisorLog "cluster forward test start failed: $($_.Exception.Message)"
  }
}

Write-SupervisorLog "supervisor started"
while ($true) {
  Write-SupervisorLog "check starting"
  Ensure-PaperExecutor
  Write-SupervisorLog "paper executor check complete"
  Ensure-CaptureFileIngest
  Write-SupervisorLog "capture file ingest check complete"
  Ensure-ClusterForwardTest
  Write-SupervisorLog "cluster forward test check complete"
  Start-Sleep -Seconds $CheckIntervalSeconds
}
