param(
    [string]$ShortcutPath,
    [string]$ShortcutName = "",
    [string]$AppUserModelId = "com.zijinresearch.vcphunter.quantterminal",
    [switch]$ForceDevLauncher,
    [switch]$StartMenu,
    [switch]$Desktop = $true
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-AppDisplayName {
    return (-join @([char]0x7D2B, [char]0x91D1, [char]0x6295, [char]0x7814))
}

$DisplayName = Get-AppDisplayName
if (-not $ShortcutName) {
    $ShortcutName = $DisplayName
}

$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$PackagedExe = Join-Path $RepoRoot ("dist\{0}\{0}.exe" -f $DisplayName)
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\pythonw.exe"
$EntryScript = Join-Path $RepoRoot "vcp_hunter_qt.pyw"
$LauncherSource = Join-Path $RepoRoot "scripts\launch_windows_silent.cs"
$LauncherOutputDir = Join-Path $env:LOCALAPPDATA "ZijinResearch\Launcher"
$LauncherExe = Join-Path $LauncherOutputDir "ZijinResearchLauncher.exe"
$IconPath = Join-Path $RepoRoot "bull_icon.ico"

function Assert-PathExists {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Label not found: $Path"
    }
}

function Ensure-SilentLauncherExe {
    Assert-PathExists -Path $LauncherSource -Label "Launcher source"

    if (-not (Test-Path -LiteralPath $LauncherOutputDir)) {
        New-Item -ItemType Directory -Path $LauncherOutputDir -Force | Out-Null
    }

    $sourceInfo = Get-Item -LiteralPath $LauncherSource
    $exeInfo = Get-Item -LiteralPath $LauncherExe -ErrorAction SilentlyContinue
    $needsBuild = $true
    if ($exeInfo -and ($exeInfo.LastWriteTimeUtc -ge $sourceInfo.LastWriteTimeUtc)) {
        $needsBuild = $false
    }

    if ($needsBuild) {
        if (Test-Path -LiteralPath $LauncherExe) {
            Remove-Item -LiteralPath $LauncherExe -Force -ErrorAction SilentlyContinue
        }

        $launcherCode = Get-Content -LiteralPath $LauncherSource -Raw
        Add-Type `
            -TypeDefinition $launcherCode `
            -OutputAssembly $LauncherExe `
            -OutputType WindowsApplication `
            -ReferencedAssemblies @("System.dll")
    }

    Assert-PathExists -Path $LauncherExe -Label "Compiled launcher"
}

if (-not ("ShortcutNative.IShellLinkW" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

namespace ShortcutNative {
    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IShellLinkW {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cch, IntPtr pfd, int fFlags);
        void GetIDList(out IntPtr ppidl);
        void SetIDList(IntPtr pidl);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cch);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cch);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cch);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
        void GetHotkey(out short pwHotkey);
        void SetHotkey(short wHotkey);
        void GetShowCmd(out int piShowCmd);
        void SetShowCmd(int iShowCmd);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cch, out int iIcon);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, int dwReserved);
        void Resolve(IntPtr hwnd, int fFlags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
    }

    [ComImport, Guid("0000010b-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPersistFile {
        void GetClassID(out Guid pClassID);
        void IsDirty();
        void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
        void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, bool fRemember);
        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
        void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    public struct PROPERTYKEY {
        public Guid fmtid;
        public uint pid;

        public PROPERTYKEY(Guid formatId, uint propertyId) {
            fmtid = formatId;
            pid = propertyId;
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct PROPVARIANT {
        public ushort vt;
        public ushort wReserved1;
        public ushort wReserved2;
        public ushort wReserved3;
        public IntPtr p;
        public int p2;

        public static PROPVARIANT FromString(string value) {
            var pv = new PROPVARIANT();
            pv.vt = 31;
            pv.p = Marshal.StringToCoTaskMemUni(value);
            return pv;
        }
    }

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IPropertyStore {
        uint GetCount(out uint cProps);
        uint GetAt(uint iProp, out PROPERTYKEY pkey);
        uint GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
        uint SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);
        uint Commit();
    }

    public static class PropVariantNative {
        [DllImport("Ole32.dll")]
        public static extern int PropVariantClear(ref PROPVARIANT pvar);
    }

    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    public class ShellLink {
    }

    public static class ShortcutBuilder {
        public static void Create(
            string shortcutPath,
            string targetPath,
            string arguments,
            string workingDirectory,
            string description,
            string iconLocation,
            string appUserModelId
        ) {
            var shellLink = (IShellLinkW)new ShellLink();
            var persist = (IPersistFile)shellLink;
            var propertyStore = (IPropertyStore)shellLink;
            var appIdKey = new PROPERTYKEY(
                new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
                5
            );
            var appIdValue = PROPVARIANT.FromString(appUserModelId);

            try {
                shellLink.SetPath(targetPath);
                shellLink.SetArguments(arguments ?? string.Empty);
                shellLink.SetWorkingDirectory(workingDirectory);
                shellLink.SetDescription(description ?? string.Empty);
                shellLink.SetIconLocation(iconLocation, 0);
                shellLink.SetShowCmd(1);

                var setResult = propertyStore.SetValue(ref appIdKey, ref appIdValue);
                if (setResult != 0) {
                    Marshal.ThrowExceptionForHR((int)setResult);
                }

                var commitResult = propertyStore.Commit();
                if (commitResult != 0) {
                    Marshal.ThrowExceptionForHR((int)commitResult);
                }

                persist.Save(shortcutPath, true);
            } finally {
                PropVariantNative.PropVariantClear(ref appIdValue);
                if (propertyStore != null) {
                    Marshal.ReleaseComObject(propertyStore);
                }
                if (persist != null) {
                    Marshal.ReleaseComObject(persist);
                }
                if (shellLink != null) {
                    Marshal.ReleaseComObject(shellLink);
                }
            }
        }
    }
}
"@
}

function Resolve-Launcher {
    if ((-not $ForceDevLauncher) -and (Test-Path -LiteralPath $PackagedExe)) {
        return @{
            TargetPath = $PackagedExe
            Arguments = ""
            IconLocation = $PackagedExe
            Description = ("Launch {0}" -f $DisplayName)
        }
    }

    Assert-PathExists -Path $PythonExe -Label "pythonw.exe"
    Assert-PathExists -Path $EntryScript -Label "Entry script"
    Assert-PathExists -Path $IconPath -Label "Icon"

    return @{
        TargetPath = $PythonExe
        Arguments = ('"{0}"' -f $EntryScript)
        IconLocation = $IconPath
        Description = ("Launch {0} (dev)" -f $DisplayName)
    }
}

function New-ShortcutFile {
    param(
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][hashtable]$Launcher
    )

    $directory = Split-Path -Parent $OutputPath
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    [ShortcutNative.ShortcutBuilder]::Create(
        $OutputPath,
        $Launcher.TargetPath,
        $Launcher.Arguments,
        $RepoRoot,
        $Launcher.Description,
        $Launcher.IconLocation,
        $AppUserModelId
    )
}

$Launcher = Resolve-Launcher
$OutputPaths = @()

if ($ShortcutPath) {
    $OutputPaths += $ShortcutPath
} else {
    if ($Desktop) {
        $desktopDir = [Environment]::GetFolderPath("Desktop")
        $OutputPaths += (Join-Path $desktopDir "$ShortcutName.lnk")
    }

    if ($StartMenu) {
        $startMenuDir = Join-Path ([Environment]::GetFolderPath("Programs")) $DisplayName
        $OutputPaths += (Join-Path $startMenuDir "$ShortcutName.lnk")
    }
}

if (-not $OutputPaths) {
    throw "No shortcut destination was requested."
}

foreach ($outputPath in $OutputPaths) {
    New-ShortcutFile -OutputPath $outputPath -Launcher $Launcher
    Write-Host "Shortcut created: $outputPath"
}
