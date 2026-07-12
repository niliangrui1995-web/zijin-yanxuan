param(
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ReqPath = Join-Path $RepoRoot "requirements.txt"
$ConstraintPath = Join-Path $RepoRoot "constraints-py314-windows.txt"
$VenvPath = Join-Path $RepoRoot ".venv"
$DefaultVenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not $PythonExe) {
    if (Test-Path -LiteralPath $DefaultVenvPython) {
        $PythonExe = $DefaultVenvPython
    } else {
        $PyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
        if (-not $PyLauncher) {
            throw "Python 3.14 launcher not found. Install Python 3.14 or run: py -3.14 -m venv .venv"
        }
        & $PyLauncher.Source -3.14 -m venv $VenvPath
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create the Python 3.14 virtual environment: $VenvPath"
        }
        $PythonExe = $DefaultVenvPython
    }
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python not found: $PythonExe"
}

& $PythonExe -c "import sys; raise SystemExit(sys.version_info[:2] != (3, 14))"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.14 is required: $PythonExe"
}

if (-not (Test-Path -LiteralPath $ConstraintPath)) {
    throw "Dependency constraints not found: $ConstraintPath"
}

function Invoke-PythonChecked {
    param(
        [string[]]$Arguments,
        [string]$Description
    )

    & $PythonExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

Invoke-PythonChecked -Arguments @("-m", "pip", "install", "--upgrade", "pip", "-c", $ConstraintPath) -Description "pip upgrade"
Invoke-PythonChecked -Arguments @("-m", "pip", "install", "-r", $ReqPath, "-c", $ConstraintPath) -Description "runtime dependency sync"
Invoke-PythonChecked -Arguments @("-m", "pip", "check") -Description "dependency consistency check"
