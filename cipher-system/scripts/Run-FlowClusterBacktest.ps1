param(
  [string[]]$Ticker,
  [switch]$All,
  [Parameter(Mandatory = $true)]
  [string]$Start,
  [Parameter(Mandatory = $true)]
  [string]$End,
  [ValidateSet("gamma_volume", "gamma_premium")]
  [string]$WeightMode = "gamma_volume",
  [string]$Horizons = "1,3,5",
  [int]$MinPrints = 20,
  [ValidateSet("local_then_lse", "local", "lse")]
  [string]$BarProvider = "local_then_lse",
  [ValidateSet("1m", "5m", "15m")]
  [string]$LocalTimeframe = "5m",
  [string]$LocalStockRoot = "",
  [int]$MinEdgeN = 5,
  [double]$StopPct = 0.02,
  [double]$MinTargetPct = 0,
  [string]$MaxTargetPct = "",
  [switch]$Sweep,
  [string]$StopGrid = "0.02,0.03",
  [string]$MinTargetGrid = "0,0.0025,0.005",
  [string]$MaxTargetGrid = "none,0.03,0.05",
  [switch]$PriceOptions,
  [int]$Limit = 0,
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SystemRoot = Split-Path -Parent $ScriptDir
$FlowPy = Join-Path $SystemRoot "core\flow_cluster_backtest.py"

if (-not (Test-Path $FlowPy)) {
  throw "Flow cluster script not found: $FlowPy"
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

$Command = if ($Sweep) { "sweep" } else { "backtest" }

$argsList = @(
  $Command,
  "--start", $Start,
  "--end", $End,
  "--weight-mode", $WeightMode,
  "--horizons", $Horizons,
  "--min-prints", "$MinPrints",
  "--bar-provider", $BarProvider,
  "--local-timeframe", $LocalTimeframe,
  "--min-edge-n", "$MinEdgeN",
)

if ($Sweep) {
  $argsList += @(
    "--stop-grid", $StopGrid,
    "--min-target-grid", $MinTargetGrid,
    "--max-target-grid", $MaxTargetGrid
  )
} else {
  $argsList += @("--stop-pct", "$StopPct", "--min-target-pct", "$MinTargetPct")
  if (-not [string]::IsNullOrWhiteSpace($MaxTargetPct)) {
    $argsList += @("--max-target-pct", "$MaxTargetPct")
  }
}

if (-not [string]::IsNullOrWhiteSpace($LocalStockRoot)) {
  $argsList += @("--local-stock-root", $LocalStockRoot)
}

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

if ($PriceOptions) {
  $argsList += "--price-options"
}
if ($Limit -gt 0) {
  $argsList += @("--limit", "$Limit")
}

Push-Location $SystemRoot
try {
  if ($PythonExe -eq "py") {
    & py -3 $FlowPy @argsList
  } else {
    & $PythonExe $FlowPy @argsList
  }
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
