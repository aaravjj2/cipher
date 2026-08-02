$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSCommandPath
function Start-LocalServer([int]$Port, [string]$Arguments) {
  if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath python -ArgumentList $Arguments -WorkingDirectory $root -WindowStyle Hidden
  }
}
Start-LocalServer 8182 (Join-Path $root 'core\app.py')
Start-LocalServer 8181 ("-m http.server 8181 --directory `"$root`"")
Start-Sleep -Milliseconds 700
Start-Process 'http://127.0.0.1:8181/demo.html'