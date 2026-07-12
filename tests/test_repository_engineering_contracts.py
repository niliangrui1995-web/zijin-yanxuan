from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_sync_requires_python_314_and_uses_windows_constraints():
    script = (REPO_ROOT / "scripts" / "sync_runtime_env.ps1").read_text(encoding="utf-8")

    assert "Python310" not in script
    assert "py -3.14" in script
    assert "sys.version_info[:2] != (3, 14)" in script
    assert "constraints-py314-windows.txt" in script


def test_trade_data_is_ignored_and_lock_files_are_trackable():
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "data/Trade/" in gitignore
    assert "uv.lock" not in gitignore


def test_docs_match_eleven_lazy_tabs_and_disabled_real_tab_prewarm():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (REPO_ROOT / "docs" / "technical-architecture.md").read_text(encoding="utf-8")

    assert "装配了 11 个主 Tab" in readme
    assert "装配 11 个主 Tab" in architecture
    assert "所有 Tab 首先挂载 `LazyTabPlaceholder`" in readme
    assert "`BACKGROUND_PREWARM_KEYS` 当前为空" in architecture
    assert "装配了 12 个主 Tab" not in readme
    assert "装配 12 个主 Tab" not in architecture
