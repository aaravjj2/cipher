param(
  [string]$RuntimeRoot = "C:\Aarav\cipher-system\CipherCapture",
  [int]$CheckIntervalSeconds = 30,
  [switch]$SeedExisting,
  [switch]$ResetState
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SystemRoot = Split-Path -Parent $ScriptDir
$LogDir = Join-Path $RuntimeRoot "logs"
$PythonExe = Join-Path $RuntimeRoot ".venv\Scripts\python.exe"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
if (-not (Test-Path $PythonExe)) {
  $PythonExe = (Get-Command python -ErrorAction Stop).Source
}

$Args = "-m core.paper_executor.cluster_forward_test --root `"$RuntimeRoot`" --interval-seconds $CheckIntervalSeconds --watch"
if ($SeedExisting) {
  $Args = "$Args --seed-existing"
}
if ($ResetState) {
  $Args = "$Args --reset-state"
}

Start-Process `
  -FilePath $PythonExe `
  -ArgumentList $Args `
  -WorkingDirectory $SystemRoot `
  -WindowStyle Hidden
