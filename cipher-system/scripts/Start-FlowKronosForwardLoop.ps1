param(
  [string[]]$Ticker = @("AMD", "AMZN", "COIN", "GOOGL", "NVDA"),
  [int]$IntervalMinutes = 30,
  [double]$MinPremium = 25000,
  [int]$MaxDte = 45,
  [int]$MinPrints = 20,
  [int]$Horizon = 2,
  [double]$StopPct = 0.03,
  [double]$MinAbsKronosPredReturnPct = 0.3,
  [double]$MinTargetDistancePct = 0.3,
  [double]$MaxTargetDistancePct = 5.0,
  [int]$KronosLookback = 128,
  [int]$KronosPredBars = 32,
  [int]$Seed = 42,
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $ScriptDir "Run-FlowKronosForwardTest.ps1"

if (-not (Test-Path $Runner)) {
  throw "Forward-test runner not found: $Runner"
}

Write-Host "Starting read-only Flow + Kronos forward-test loop."
Write-Host "Tickers: $($Ticker -join ', ')"
Write-Host "Interval: $IntervalMinutes minutes"
Write-Host "No orders will be submitted."

while ($true) {
  $started = Get-Date
  Write-Host ""
  Write-Host "[$($started.ToString('s'))] Scoring open signals..."
  & $Runner -Command score -PythonExe $PythonExe

  Write-Host ""
  Write-Host "[$((Get-Date).ToString('s'))] Capturing new flow/Kronos candidates..."
  & $Runner `
    -Command capture `
    -Ticker $Ticker `
    -MinPremium $MinPremium `
    -MaxDte $MaxDte `
    -MinPrints $MinPrints `
    -Horizon $Horizon `
    -StopPct $StopPct `
    -MinAbsKronosPredReturnPct $MinAbsKronosPredReturnPct `
    -MinTargetDistancePct $MinTargetDistancePct `
    -MaxTargetDistancePct $MaxTargetDistancePct `
    -KronosLookback $KronosLookback `
    -KronosPredBars $KronosPredBars `
    -Seed $Seed `
    -PythonExe $PythonExe

  Write-Host ""
  Write-Host "[$((Get-Date).ToString('s'))] Ledger state..."
  & $Runner -Command list -PythonExe $PythonExe

  $sleepSeconds = [Math]::Max(60, $IntervalMinutes * 60)
  Write-Host "Sleeping $sleepSeconds seconds. Press Ctrl+C to stop."
  Start-Sleep -Seconds $sleepSeconds
}
