$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SystemRoot = Split-Path -Parent $ScriptDir
$PidPath = Join-Path $SystemRoot "data\flash_agentic\live_loop.pid"

if (-not (Test-Path $PidPath)) {
  "Flash Agentic live simulation is not running."
  exit 0
}

$pidText = (Get-Content -Raw -LiteralPath $PidPath).Trim()
if (-not ($pidText -match '^\d+$')) {
  throw "Invalid PID file: $PidPath"
}

$proc = Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
if (-not $proc) {
  Remove-Item -LiteralPath $PidPath -Force
  "Removed stale PID file. Flash Agentic live simulation was not running."
  exit 0
}

Stop-Process -Id $proc.Id
"Stopped Flash Agentic live simulation PID $($proc.Id)."
