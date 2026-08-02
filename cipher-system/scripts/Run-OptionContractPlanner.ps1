param(
  [int]$Limit = 8,
  [string]$MinGrade = "A,B+",
  [ValidateSet("opra", "indicative")]
  [string]$Feed = "opra",
  [int]$MaxPages = 12,
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SystemRoot = Split-Path -Parent $ScriptDir
$PlannerPy = Join-Path $SystemRoot "core\option_contract_planner.py"

if (-not (Test-Path $PlannerPy)) {
  throw "Planner script not found: $PlannerPy"
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
  "--limit", "$Limit",
  "--min-grade", $MinGrade,
  "--feed", $Feed,
  "--max-pages", "$MaxPages"
)

Push-Location $SystemRoot
try {
  if ($PythonExe -eq "py") {
    & py -3 $PlannerPy @argsList
  } else {
    & $PythonExe $PlannerPy @argsList
  }
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
