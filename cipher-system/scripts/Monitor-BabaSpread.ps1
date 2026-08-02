param(
  [ValidateSet("opra", "indicative")]
  [string]$Feed = "opra",
  [int]$MaxPages = 12,
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SystemRoot = Split-Path -Parent $ScriptDir
$MonitorPy = Join-Path $SystemRoot "core\position_monitor.py"
$PositionPath = Join-Path $SystemRoot "data\positions\baba_20260724_118_120_call_debit_spread.json"

if (-not (Test-Path $MonitorPy)) {
  throw "Monitor script not found: $MonitorPy"
}
if (-not (Test-Path $PositionPath)) {
  throw "Position file not found: $PositionPath"
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
  "monitor",
  "--position", $PositionPath,
  "--feed", $Feed,
  "--max-pages", "$MaxPages"
)

Push-Location $SystemRoot
try {
  if ($PythonExe -eq "py") {
    & py -3 $MonitorPy @argsList
  } else {
    & $PythonExe $MonitorPy @argsList
  }
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
