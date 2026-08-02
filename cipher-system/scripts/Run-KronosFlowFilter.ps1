param(
  [Parameter(Mandatory = $true)]
  [string]$CandidateCsv,
  [string[]]$Ticker,
  [ValidateSet("1m", "5m", "15m")]
  [string]$Timeframe = "5m",
  [int]$Lookback = 400,
  [int]$MaxPredBars = 160,
  [string]$ModelId = "NeoQuasar/Kronos-small",
  [string]$TokenizerId = "NeoQuasar/Kronos-Tokenizer-base",
  [string]$Device = "",
  [int]$SampleCount = 1,
  [int]$Seed = 42,
  [double]$MinAbsPredReturnPct = 0,
  [switch]$AllSetups,
  [switch]$Sweep,
  [string]$LookbackGrid = "64,128,256",
  [string]$MaxPredBarsGrid = "32,64",
  [int]$Horizon = 2,
  [int]$Limit = 0,
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SystemRoot = Split-Path -Parent $ScriptDir
$KronosPy = Join-Path $SystemRoot "core\kronos_research.py"

if (-not (Test-Path $KronosPy)) {
  throw "Kronos research script not found: $KronosPy"
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

$Command = if ($Sweep) { "sweep-candidates" } else { "filter-candidates" }

$argsList = @(
  $Command,
  "--candidate-csv", $CandidateCsv,
  "--timeframe", $Timeframe,
  "--model-id", $ModelId,
  "--tokenizer-id", $TokenizerId,
  "--sample-count", "$SampleCount",
  "--seed", "$Seed",
  "--horizon", "$Horizon"
)

if ($Sweep) {
  $argsList += @(
    "--lookback-grid", $LookbackGrid,
    "--max-pred-bars-grid", $MaxPredBarsGrid
  )
} else {
  $argsList += @(
    "--lookback", "$Lookback",
    "--max-pred-bars", "$MaxPredBars",
    "--min-abs-pred-return-pct", "$MinAbsPredReturnPct"
  )
}

if (-not [string]::IsNullOrWhiteSpace($Device)) {
  $argsList += @("--device", $Device)
}
if ($AllSetups) {
  $argsList += "--all-setups"
}
if ($Limit -gt 0) {
  $argsList += @("--limit", "$Limit")
}
if ($Ticker -and $Ticker.Count -gt 0) {
  foreach ($t in $Ticker) {
    if (-not [string]::IsNullOrWhiteSpace($t)) {
      $argsList += "--ticker"
      $argsList += $t.Trim().ToUpperInvariant()
    }
  }
}

Push-Location $SystemRoot
try {
  if ($PythonExe -eq "py") {
    & py -3 $KronosPy @argsList
  } else {
    & $PythonExe $KronosPy @argsList
  }
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
