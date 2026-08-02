param(
  [string[]]$Ticker,
  [switch]$All,
  [Parameter(Mandatory = $true)]
  [string]$Start,
  [Parameter(Mandatory = $true)]
  [string]$End,
  [double]$MinPremium = 25000,
  [int]$MaxDte = 45,
  [int]$Limit = 0,
  [int]$LimitPerCall = 5000,
  [int]$SleepMs = 350,
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

$argsList = @("download-lse", "--start", $Start, "--end", $End, "--min-premium", "$MinPremium", "--max-dte", "$MaxDte", "--limit-per-call", "$LimitPerCall", "--sleep-ms", "$SleepMs")

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
