from pathlib import Path

import pytest

from infra.navigation.windows_autostart import (
    RUN_KEY,
    RUN_VALUE_NAME,
    AutoStartError,
    WindowsAutoStartManager,
)


class _FakeKey:
    def __init__(self, registry):
        self.registry = registry

    def __enter__(self):
        return self.registry

    def __exit__(self, exc_type, exc, traceback):
        return False


class _FakeRegistry:
    HKEY_CURRENT_USER = object()
    KEY_READ = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}
        self.opened = []

    def OpenKey(self, root, path, reserved=0, access=0):
        self.opened.append((root, path, reserved, access))
        if path != RUN_KEY:
            raise FileNotFoundError(path)
        return _FakeKey(self)

    def CreateKey(self, root, path):
        self.opened.append((root, path, 0, 0))
        if path != RUN_KEY:
            raise FileNotFoundError(path)
        return _FakeKey(self)

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, value_type, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def test_autostart_is_unsupported_outside_windows(tmp_path):
    manager = WindowsAutoStartManager(tmp_path, os_name="posix", registry=_FakeRegistry())

    assert manager.is_supported is False
    assert manager.is_enabled() is False
    with pytest.raises(AutoStartError):
        manager.set_enabled(True)


def test_autostart_enable_writes_project_venv_command(tmp_path):
    registry = _FakeRegistry()
    repo_root = tmp_path / "repo"
    _touch(repo_root / ".venv" / "Scripts" / "pythonw.exe")
    _touch(repo_root / "vcp_hunter_qt.pyw")

    manager = WindowsAutoStartManager(repo_root, os_name="nt", registry=registry, environ={})

    manager.set_enabled(True)

    command = registry.values[RUN_VALUE_NAME]
    assert command == f'"{repo_root / ".venv" / "Scripts" / "pythonw.exe"}" "{repo_root / "vcp_hunter_qt.pyw"}"'
    assert manager.is_enabled() is True


def test_autostart_prefers_existing_silent_launcher(tmp_path):
    registry = _FakeRegistry()
    repo_root = tmp_path / "repo"
    local_app_data = tmp_path / "local_app_data"
    _touch(repo_root / ".venv" / "Scripts" / "pythonw.exe")
    _touch(repo_root / "vcp_hunter_qt.pyw")
    launcher = local_app_data / "ZijinResearch" / "Launcher" / "ZijinResearchLauncher.exe"
    _touch(launcher)

    manager = WindowsAutoStartManager(
        repo_root,
        os_name="nt",
        registry=registry,
        environ={"LOCALAPPDATA": str(local_app_data)},
    )

    command = manager.resolve_launch_command()

    assert command.source == "silent-launcher"
    assert command.command == f'"{launcher}" "{repo_root}"'


def test_autostart_disable_removes_registry_value(tmp_path):
    registry = _FakeRegistry()
    registry.values[RUN_VALUE_NAME] = '"old command"'
    manager = WindowsAutoStartManager(tmp_path, os_name="nt", registry=registry, environ={})

    manager.set_enabled(False)

    assert RUN_VALUE_NAME not in registry.values
    assert manager.is_enabled() is False


def test_autostart_enable_reports_missing_launcher(tmp_path):
    registry = _FakeRegistry()
    manager = WindowsAutoStartManager(tmp_path, os_name="nt", registry=registry, environ={})

    with pytest.raises(AutoStartError):
        manager.set_enabled(True)
