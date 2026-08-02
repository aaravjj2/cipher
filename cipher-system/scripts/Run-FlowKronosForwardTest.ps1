param(
  [ValidateSet("capture", "score", "list")]
  [string]$Command = "capture",
  [string[]]$Ticker,
  [string]$AsOf = "",
  [switch]$NoDownload,
  [double]$MinPremium = 25000,
  [int]$MaxDte = 45,
  [int]$MinPrints = 20,
  [int]$Horizon = 2,
  [double]$StopPct = 0.03,
  [double]$MinAbsKronosPredReturnPct = 0.3,
  [double]$MinTargetDistancePct = 0.3,
  [double]$MaxTargetDistancePct = 5.0,
  [ValidateSet("1m", "5m", "15m")]
  [string]$KronosTimeframe = "5m",
  [int]$KronosLookback = 128,
  [int]$KronosPredBars = 32,
  [int]$KronosSampleCount = 1,
  [int]$Seed = 42,
  [switch]$AllSetups,
  [string]$Db = "",
  [string]$FlowDb = "",
  [string]$LocalStockRoot = "",
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SystemRoot = Split-Path -Parent $ScriptDir
$ForwardPy = Join-Path $SystemRoot "core\flow_forward_test.py"

if (-not (Test-Path $ForwardPy)) {
  throw "Forward-test script not found: $ForwardPy"
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

$argsList = @($Command)

if ($Command -eq "capture") {
  $argsList += @(
    "--min-premium", "$MinPremium",
    "--max-dte", "$MaxDte",
    "--min-prints", "$MinPrints",
    "--horizon", "$Horizon",
    "--stop-pct", "$StopPct",
    "--min-abs-kronos-pred-return-pct", "$MinAbsKronosPredReturnPct",
    "--min-target-distance-pct", "$MinTargetDistancePct",
    "--max-target-distance-pct", "$MaxTargetDistancePct",
    "--kronos-timeframe", $KronosTimeframe,
    "--kronos-lookback", "$KronosLookback",
    "--kronos-pred-bars", "$KronosPredBars",
    "--kronos-sample-count", "$KronosSampleCount",
    "--seed", "$Seed"
  )
  if (-not [string]::IsNullOrWhiteSpace($AsOf)) {
    $argsList += @("--as-of", $AsOf)
  }
  if ($NoDownload) {
    $argsList += "--no-download"
  }
  if ($AllSetups) {
    $argsList += "--all-setups"
  }
  if ($Ticker -and $Ticker.Count -gt 0) {
    foreach ($t in $Ticker) {
      if (-not [string]::IsNullOrWhiteSpace($t)) {
        $argsList += "--ticker"
        $argsList += $t.Trim().ToUpperInvariant()
      }
    }
  }
  if (-not [string]::IsNullOrWhiteSpace($FlowDb)) {
    $argsList += @("--flow-db", $FlowDb)
  }
} elseif ($Command -eq "score") {
  $argsList += @("--local-timeframe", $KronosTimeframe)
  if (-not [string]::IsNullOrWhiteSpace($LocalStockRoot)) {
    $argsList += @("--local-stock-root", $LocalStockRoot)
  }
}

if (-not [string]::IsNullOrWhiteSpace($Db)) {
  $argsList += @("--db", $Db)
}

Push-Location $SystemRoot
try {
  if ($PythonExe -eq "py") {
    & py -3 $ForwardPy @argsList
  } else {
    & $PythonExe $ForwardPy @argsList
  }
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
