param(
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SystemRoot = Split-Path -Parent $ScriptDir
$ResearchPy = Join-Path $SystemRoot "core\setup_research_engine.py"

if (-not (Test-Path $ResearchPy)) {
  throw "Research script not found: $ResearchPy"
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

Push-Location $SystemRoot
try {
  if ($PythonExe -eq "py") {
    & py -3 $ResearchPy
  } else {
    & $PythonExe $ResearchPy
  }
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
