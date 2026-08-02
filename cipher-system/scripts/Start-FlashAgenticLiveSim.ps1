param(
  [int]$IntervalSeconds = 60,
  [int]$CaptureTimeoutSeconds = 180,
  [double]$TakeProfitPct = 20,
  [double]$StopLossPct = 12,
  [int]$MaxNew = 5,
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SystemRoot = Split-Path -Parent $ScriptDir
$LoopPy = Join-Path $SystemRoot "core\flash_agentic_live_loop.py"
$DataDir = Join-Path $SystemRoot "data\flash_agentic"
$LogPath = Join-Path $DataDir "live_loop.windows.log"

if (-not (Test-Path $LoopPy)) {
  throw "Flash live loop script not found: $LoopPy"
}
if (-not (Test-Path $DataDir)) {
  New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
}
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py) {
    $PythonExe = "py"
  } else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
      throw "Python was not found. Install Python 3 or pass -PythonExe."
    }
    $PythonExe = "python"
  }
}

$argsList = @(
  $LoopPy,
  "--interval-seconds", [string]$IntervalSeconds,
  "--capture-timeout-seconds", [string]$CaptureTimeoutSeconds,
  "--take-profit-pct", [string]$TakeProfitPct,
  "--stop-loss-pct", [string]$StopLossPct,
  "--max-new", [string]$MaxNew
)
if ($PythonExe -eq "py") {
  $argsList = @("-3") + $argsList
}

$proc = Start-Process -FilePath $PythonExe -ArgumentList $argsList -WorkingDirectory $SystemRoot -WindowStyle Hidden -RedirectStandardOutput $LogPath -RedirectStandardError $LogPath -PassThru
"Started Flash Agentic live simulation PID $($proc.Id). Status: $DataDir\live_status.json"
