$ErrorActionPreference = "Stop"

$backupRef = "codex/ui-backup-20260414-pre-terminal-overhaul"
$restorePaths = @(
    "ui/components/__init__.py",
    "ui/components/main_window_shell.py",
    "ui/styles/global_qss.py",
    "ui/theme_tokens.py",
    "ui/tabs/base_stock_tab.py",
    "ui/tabs/watchlist_tab.py",
    "ui/tabs/rt_monitor_tab.py",
    "ui/tabs/foreign_block_trade_tab.py",
    "ui/tabs/lhb_tab.py",
    "ui/tabs/earnings_tab.py",
    "ui/tabs/na_daily_tab.py",
    "ui/tabs/log_tab.py",
    "tests/test_base_stock_tab.py",
    "tests/test_rt_monitor_tab.py",
    "tests/test_log_tab.py"
)
$removePaths = @()

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Args
    )

    & git @Args
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Args -join ' ') 执行失败"
    }
}

function Test-GitPathExistsInRef {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Ref,
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    & git cat-file -e "$Ref`:$Path" 2>$null
    return $LASTEXITCODE -eq 0
}

$repoRoot = (& git rev-parse --show-toplevel).Trim()
if (-not $repoRoot) {
    throw "未找到 git 仓库根目录"
}

Set-Location $repoRoot
Invoke-Git -Args @("rev-parse", "--verify", $backupRef)

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$snapshotBranch = "codex/ui-overhaul-snapshot-$timestamp"
Invoke-Git -Args @("branch", $snapshotBranch)

& git stash push -u -m "ui-rollback-$timestamp" *> $null

foreach ($path in $restorePaths) {
    if (Test-GitPathExistsInRef -Ref $backupRef -Path $path) {
        Invoke-Git -Args @("checkout", $backupRef, "--", $path)
        continue
    }

    if (Test-Path -LiteralPath $path) {
        & git ls-files --error-unmatch -- $path *> $null
        if ($LASTEXITCODE -eq 0) {
            Invoke-Git -Args @("rm", "-f", "--quiet", "--", $path)
        } else {
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
}

foreach ($path in $removePaths) {
    if (-not (Test-Path -LiteralPath $path)) {
        continue
    }

    & git ls-files --error-unmatch -- $path *> $null
    if ($LASTEXITCODE -eq 0) {
        Invoke-Git -Args @("rm", "-f", "--quiet", "--", $path)
    } else {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
}

Write-Host ""
Write-Host "旧 UI 已恢复到改造前基线。" -ForegroundColor Green
Write-Host "备份基线: $backupRef"
Write-Host "回滚前快照分支: $snapshotBranch"
Write-Host "如需找回本次新 UI 的未提交改动，请查看 git stash list。"
