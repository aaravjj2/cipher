param(
  [string]$Symbols = "SPY,QQQ,IWM,NVDA,MSFT,AAPL,AVGO,AMZN,IBIT,GOOGL,TSLA,META,MU,AMD",
  [string]$Filters = "quote,trade,timesale,tradex,summary",
  [int]$DurationSeconds = 300,
  [string]$Env = "production",
  [string]$PositionFile = ""
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Resolve-Path (Join-Path $scriptDir "..\..")
Set-Location $repoRoot

$argsList = @(
  "cipher-system/core/tradier_stream_capture.py",
  "--symbols", $Symbols,
  "--filters", $Filters,
  "--duration-seconds", [string]$DurationSeconds,
  "--env", $Env
)

if ($PositionFile.Trim().Length -gt 0) {
  $argsList += @("--position-file", $PositionFile)
}

python3 @argsList
