param(
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SystemRoot = Split-Path -Parent $ScriptDir
$ContextPy = Join-Path $SystemRoot "core\index_daytrade_context.py"

if (-not (Test-Path $ContextPy)) {
  throw "Context script not found: $ContextPy"
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
    & py -3 $ContextPy
  } else {
    & $PythonExe $ContextPy
  }
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
