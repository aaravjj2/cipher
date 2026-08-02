param(
  [ValidateSet("seed-current-best", "seed-golden-calls", "mark")]
  [string]$Command = "mark",
  [string]$Book = "setup_blend_20260722",
  [int]$Quantity = 1,
  [double]$MaxContractCost = 500,
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SystemRoot = Split-Path -Parent $ScriptDir
$PaperPy = Join-Path $SystemRoot "core\paper_trade_engine.py"

if (-not (Test-Path $PaperPy)) {
  throw "Paper trade script not found: $PaperPy"
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

Push-Location $SystemRoot
try {
  $argsList = @($PaperPy, $Command, "--book", $Book)
  if ($Command -eq "seed-current-best") {
    $argsList += @("--quantity", [string]$Quantity)
  }
  if ($Command -eq "seed-golden-calls") {
    $argsList += @("--quantity", [string]$Quantity, "--max-contract-cost", [string]$MaxContractCost)
  }
  if ($PythonExe -eq "py") {
    & py -3 @argsList
  } else {
    & $PythonExe @argsList
  }
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
