param(
  [string]$Expiry = "",
  [int]$Limit = 20,
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SystemRoot = Split-Path -Parent $ScriptDir
$CapturePy = Join-Path $SystemRoot "core\scan_option_mark_capture.py"

if (-not (Test-Path $CapturePy)) {
  throw "Scan option mark capture script not found: $CapturePy"
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
  $argsList = @($CapturePy, "--limit", [string]$Limit)
  if (-not [string]::IsNullOrWhiteSpace($Expiry)) {
    $argsList += @("--expiry", $Expiry)
  }
  if ($PythonExe -eq "py") {
    & py -3 @argsList
  } else {
    & $PythonExe @argsList
  }
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
