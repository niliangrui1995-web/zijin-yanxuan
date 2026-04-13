param(
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ReqPath = Join-Path $RepoRoot "requirements.txt"
$DefaultVenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$DefaultProjectPython = "C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe"

if (-not $PythonExe) {
    if (Test-Path -LiteralPath $DefaultVenvPython) {
        $PythonExe = $DefaultVenvPython
    } else {
        $PythonExe = $DefaultProjectPython
    }
}

if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python not found: $PythonExe"
}

& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r $ReqPath
& $PythonExe -m pip check
