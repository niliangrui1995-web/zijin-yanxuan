from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_sync_requires_python_314_and_uses_windows_constraints():
    script = (REPO_ROOT / "scripts" / "sync_runtime_env.ps1").read_text(encoding="utf-8")

    assert "Python310" not in script
    assert "py -3.14" in script
    assert "sys.version_info[:2] != (3, 14)" in script
    assert "constraints-py314-windows.txt" in script


def test_lock_files_are_trackable():
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "data/Trade/" in gitignore
    assert "uv.lock" not in gitignore


def test_public_ui_and_http_compatibility_exports_remain_available():
    from app.services import http_client_service
    from infra.http_safety import DEFAULT_REQUESTS_USER_AGENT, requests_get_https
    from ui.components import ToggleSwitch
    from ui.components.toggle_switch import ToggleSwitch as ToggleSwitchImplementation

    assert http_client_service.__all__ == ["DEFAULT_REQUESTS_USER_AGENT", "requests_get_https"]
    assert http_client_service.DEFAULT_REQUESTS_USER_AGENT is DEFAULT_REQUESTS_USER_AGENT
    assert http_client_service.requests_get_https is requests_get_https
    assert ToggleSwitch is ToggleSwitchImplementation


def test_account_trade_record_modules_stay_removed():
    for relative_path in (
        "infra/storage/trade_record_repository.py",
        "app/services/ui_trade_record_service.py",
        "ui/trade_record_store.py",
    ):
        assert not (REPO_ROOT / relative_path).exists()


def test_docs_match_eleven_lazy_tabs_and_disabled_real_tab_prewarm():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    architecture = (REPO_ROOT / "docs" / "technical-architecture.md").read_text(encoding="utf-8")

    assert "装配了 11 个主 Tab" in readme
    assert "装配 11 个主 Tab" in architecture
    assert "所有 Tab 首先挂载 `LazyTabPlaceholder`" in readme
    assert "`BACKGROUND_PREWARM_KEYS` 当前为空" in architecture
    assert "装配了 12 个主 Tab" not in readme
    assert "装配 12 个主 Tab" not in architecture
