param(
    [string]$PythonExe,
    [string]$OutputName = "ZijinQuantTerminal",
    [switch]$OneFile,
    [switch]$SkipInstallPyInstaller,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$EntryScript = Join-Path $RepoRoot "vcp_hunter_qt.pyw"
$IconPath = Join-Path $RepoRoot "bull_icon.ico"
$AssetsPath = Join-Path $RepoRoot "assets"
$DistPath = Join-Path $RepoRoot "dist"
$WorkPath = Join-Path $RepoRoot "build\pyinstaller"

function Get-FullPathSafe {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function Assert-PathExists {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Label not found: $Path"
    }
}

function Remove-RepoPathIfPresent {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    $resolvedPath = Get-FullPathSafe -Path $Path
    if (-not $resolvedPath.StartsWith($RepoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside repo root: $resolvedPath"
    }

    Remove-Item -LiteralPath $resolvedPath -Recurse -Force
}

if (-not $PythonExe) {
    $DefaultPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $DefaultPython) {
        $PythonExe = $DefaultPython
    } else {
        $PythonExe = "python"
    }
}

Assert-PathExists -Path $EntryScript -Label "Entry script"
Assert-PathExists -Path $IconPath -Label "Icon"
Assert-PathExists -Path $AssetsPath -Label "Assets directory"

$DistTarget = if ($OneFile) {
    Join-Path $DistPath "$OutputName.exe"
} else {
    Join-Path $DistPath $OutputName
}

$PyInstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--name", $OutputName,
    "--icon", $IconPath,
    "--distpath", $DistPath,
    "--workpath", $WorkPath,
    "--specpath", $WorkPath,
    "--hidden-import", "PyQt6.QtWebEngineWidgets",
    "--collect-submodules", "qdarkstyle",
    "--collect-data", "qdarkstyle",
    "--add-data", "$IconPath;.",
    "--add-data", "$AssetsPath;assets"
)

if ($OneFile) {
    $PyInstallerArgs += "--onefile"
} else {
    $PyInstallerArgs += "--onedir"
}

$PyInstallerArgs += $EntryScript

if ($DryRun) {
    Write-Host "Repo root: $RepoRoot"
    Write-Host "Python: $PythonExe"
    Write-Host "Output target: $DistTarget"
    Write-Host "PyInstaller command:"
    Write-Host "$PythonExe $($PyInstallerArgs -join ' ')"
    exit 0
}

if (-not $SkipInstallPyInstaller) {
    & $PythonExe -m pip install --disable-pip-version-check --upgrade pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install or upgrade PyInstaller."
    }
}

Remove-RepoPathIfPresent -Path $DistTarget
Remove-RepoPathIfPresent -Path $WorkPath

& $PythonExe @PyInstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

$ExpectedExePath = if ($OneFile) {
    Join-Path $DistPath "$OutputName.exe"
} else {
    Join-Path $DistPath "$OutputName\$OutputName.exe"
}

Assert-PathExists -Path $ExpectedExePath -Label "Packaged executable"
Write-Host "Build completed: $ExpectedExePath"
