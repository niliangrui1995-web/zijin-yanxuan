from pathlib import Path

import pytest

from app.services import ui_autostart_service
from infra.navigation.windows_autostart import (
    APP_DISPLAY_NAME,
    RUN_KEY,
    RUN_VALUE_NAME,
    AutoStartError,
    WindowsAutoStartManager,
    _default_repo_root,
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


class _FailingRegistry(_FakeRegistry):
    def __init__(self, *, open_error=None, create_error=None, delete_error=None):
        super().__init__()
        self.open_error = open_error
        self.create_error = create_error
        self.delete_error = delete_error

    def OpenKey(self, *args, **kwargs):
        if self.open_error is not None:
            raise self.open_error
        return super().OpenKey(*args, **kwargs)

    def CreateKey(self, *args, **kwargs):
        if self.create_error is not None:
            raise self.create_error
        return super().CreateKey(*args, **kwargs)

    def DeleteValue(self, *args, **kwargs):
        if self.delete_error is not None:
            raise self.delete_error
        return super().DeleteValue(*args, **kwargs)


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


def test_default_repo_root_points_to_project_root():
    assert _default_repo_root().name


def test_autostart_current_command_wraps_registry_os_errors(tmp_path):
    manager = WindowsAutoStartManager(
        tmp_path,
        os_name="nt",
        registry=_FailingRegistry(open_error=OSError("registry locked")),
        environ={},
    )

    with pytest.raises(AutoStartError, match="registry locked"):
        manager.current_command()


def test_autostart_prefers_packaged_exe(tmp_path):
    registry = _FakeRegistry()
    repo_root = tmp_path / "repo"
    packaged_exe = repo_root / "dist" / APP_DISPLAY_NAME / f"{APP_DISPLAY_NAME}.exe"
    _touch(packaged_exe)

    manager = WindowsAutoStartManager(repo_root, os_name="nt", registry=registry, environ={})

    command = manager.resolve_launch_command()

    assert command.source == "packaged-exe"
    assert command.command == f'"{packaged_exe}"'


def test_autostart_enable_wraps_registry_write_errors(tmp_path):
    repo_root = tmp_path / "repo"
    _touch(repo_root / ".venv" / "Scripts" / "pythonw.exe")
    _touch(repo_root / "vcp_hunter_qt.pyw")
    manager = WindowsAutoStartManager(
        repo_root,
        os_name="nt",
        registry=_FailingRegistry(create_error=OSError("write denied")),
        environ={},
    )

    with pytest.raises(AutoStartError, match="write denied"):
        manager.set_enabled(True)


def test_autostart_disable_ignores_missing_registry_value(tmp_path):
    registry = _FakeRegistry()
    manager = WindowsAutoStartManager(tmp_path, os_name="nt", registry=registry, environ={})

    manager.set_enabled(False)

    assert RUN_VALUE_NAME not in registry.values


def test_autostart_disable_wraps_registry_delete_errors(tmp_path):
    registry = _FailingRegistry(delete_error=OSError("delete denied"))
    manager = WindowsAutoStartManager(tmp_path, os_name="nt", registry=registry, environ={})

    with pytest.raises(AutoStartError, match="delete denied"):
        manager.set_enabled(False)


def test_ui_autostart_service_delegates_set_enabled(monkeypatch, tmp_path):
    calls = []

    class FakeManager:
        def set_enabled(self, enabled):
            calls.append(enabled)

    monkeypatch.setattr(ui_autostart_service, "windows_autostart_manager", lambda repo_root=None: FakeManager())

    ui_autostart_service.set_launch_at_login_enabled(True, tmp_path)

    assert calls == [True]
