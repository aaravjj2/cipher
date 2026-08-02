param(
  [string[]]$Ticker,
  [switch]$All,
  [ValidateSet("opra", "indicative")]
  [string]$Feed = "opra",
  [string]$Depth = "0.06",
  [int]$Expirations = 1,
  [int]$ChainPages = 0,
  [string]$Tiers = "mega,large,medium",
  [int]$Limit = 0,
  [int]$SleepMs = 1250,
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SystemRoot = Split-Path -Parent $ScriptDir
$CapturePy = Join-Path $SystemRoot "core\gex_capture.py"

if (-not (Test-Path $CapturePy)) {
  throw "Capture script not found: $CapturePy"
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

$argsList = @()
if ($Ticker -and $Ticker.Count -gt 0) {
  foreach ($t in $Ticker) {
    if (-not [string]::IsNullOrWhiteSpace($t)) {
      $argsList += "--ticker"
      $argsList += $t.Trim().ToUpperInvariant()
    }
  }
} else {
  $argsList += "--all"
}

$argsList += @(
  "--feed", $Feed,
  "--depth", $Depth,
  "--expirations", "$Expirations",
  "--tiers", $Tiers,
  "--sleep-ms", "$SleepMs"
)

if ($Limit -gt 0) {
  $argsList += @("--limit", "$Limit")
}
if ($ChainPages -gt 0) {
  $argsList += @("--chain-pages", "$ChainPages")
}

Push-Location $SystemRoot
try {
  if ($PythonExe -eq "py") {
    & py -3 $CapturePy @argsList
  } else {
    & $PythonExe $CapturePy @argsList
  }
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
