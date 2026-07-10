# -*- coding: utf-8 -*-
"""
tests/conftest.py — pytest 通用 Fixture

为什么需要这个：
    PyQt6 应用的测试需要一个 QApplication 实例存在，否则任何 QWidget/QSettings
    都会直接 segfault。这里在需要 Qt 的测试中统一创建一次，会话内共享。
"""

import ast
import atexit
import os
import shutil
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("VCP_HUNTER_SETTINGS_ORGANIZATION", "VCPHunterTests")
os.environ.setdefault("VCP_HUNTER_SETTINGS_APPLICATION", "MainTest")

_QT_APPLICATION = None
_QT_SETTINGS_CONFIGURED = False
_QT_SETTINGS_ROOT = None
_TEST_LOG_ROOT = None
_TEST_DB_ROOT = None

_QT_APPLICATION_IMPORT_PREFIXES = (
    "ui.components",
    "ui.kline_window_qt",
    "ui.main_window_qt",
    "ui.models",
    "ui.splash_screen",
    "ui.tabs",
    "ui.workspaces",
)
_QT_SETTINGS_IMPORT_PREFIXES = (
    "app.services.ui_config_service",
    "core.app_config",
    "infra.settings",
    "ui.theme",
    "ui.theme_tokens",
)


def _module_matches(module, prefixes):
    return any(module == prefix or module.startswith(f"{prefix}.") for prefix in prefixes)


def _imported_module_names(node):
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        names = [module]
        names.extend(f"{module}.{alias.name}" for alias in node.names if module and alias.name != "*")
        return names
    return []


def _is_pyqt_import(node):
    return any(_module_matches(module, ("PyQt6",)) for module in _imported_module_names(node))


def _is_qt_application_import(node):
    return any(_module_matches(module, _QT_APPLICATION_IMPORT_PREFIXES) for module in _imported_module_names(node))


def _is_qsettings_import(node):
    return any(_module_matches(module, _QT_SETTINGS_IMPORT_PREFIXES) for module in _imported_module_names(node))


def _source_has_pyqt_import(source):
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("import PyQt6") or stripped.startswith("from PyQt6"):
            return True
    return False


def _source_has_project_qt_import(source):
    prefixes = _QT_APPLICATION_IMPORT_PREFIXES + _QT_SETTINGS_IMPORT_PREFIXES
    for line in source.splitlines():
        stripped = line.lstrip()
        for prefix in prefixes:
            if stripped.startswith(f"import {prefix}") or stripped.startswith(f"from {prefix}"):
                return True
    return False


@lru_cache(maxsize=None)
def _test_file_qt_metadata(path):
    try:
        source = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        source = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return (False, False, False)

    try:
        tree = ast.parse(source)
    except SyntaxError:
        pyqt_import = _source_has_pyqt_import(source)
        project_qt_import = _source_has_project_qt_import(source)
        return (pyqt_import, project_qt_import, project_qt_import)

    pyqt_import = False
    project_qt_import = False
    qsettings_import = False
    for node in ast.walk(tree):
        pyqt_import = pyqt_import or _is_pyqt_import(node)
        project_qt_import = project_qt_import or _is_qt_application_import(node)
        qsettings_import = qsettings_import or _is_qsettings_import(node)
    return (pyqt_import, project_qt_import, qsettings_import)


def _test_file_needs_qt_environment(path):
    return any(_test_file_qt_metadata(path))


def _test_file_needs_qt_application(path):
    pyqt_import, project_qt_import, _qsettings_import = _test_file_qt_metadata(path)
    return pyqt_import or project_qt_import


def _node_path(node):
    path = getattr(node, "path", None)
    if path is not None:
        return Path(path)
    fspath = getattr(node, "fspath", None)
    if fspath is not None:
        return Path(str(fspath))
    return None


def _node_needs_qt_application(node):
    if "qt_application" in getattr(node, "fixturenames", ()):
        return True

    path = _node_path(node)
    if path is None or path.suffix != ".py":
        return False
    return _test_file_needs_qt_application(str(path))


def _pyqt_is_loaded():
    return any(module == "PyQt6" or module.startswith("PyQt6.") for module in sys.modules)


def _qsettings_test_root():
    global _QT_SETTINGS_ROOT

    if _QT_SETTINGS_ROOT is not None:
        return _QT_SETTINGS_ROOT

    configured = os.environ.get("VCP_HUNTER_TEST_QSETTINGS_DIR")
    if configured:
        root = Path(configured).resolve()
        root.mkdir(parents=True, exist_ok=True)
    else:
        root = Path(tempfile.mkdtemp(prefix="vcp_hunter_qsettings_")).resolve()
        os.environ["VCP_HUNTER_TEST_QSETTINGS_DIR"] = str(root)
        atexit.register(shutil.rmtree, root, ignore_errors=True)

    _QT_SETTINGS_ROOT = root
    return root


_qsettings_test_root()


def _pytest_db_path():
    """在任何项目模块导入前把默认 SQLite 隔离到本次 pytest 会话。"""
    global _TEST_DB_ROOT

    configured = str(os.environ.get("VCP_HUNTER_TEST_DB_PATH", "") or "").strip()
    if configured:
        db_path = Path(configured).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        root = Path(tempfile.mkdtemp(prefix="vcp_hunter_db_", dir=tempfile.gettempdir())).resolve()
        db_path = root / "vcp_hunter_test.db"
        _TEST_DB_ROOT = root
        atexit.register(shutil.rmtree, root, ignore_errors=True)

    os.environ["VCP_HUNTER_DB_PATH"] = str(db_path)
    return db_path


_pytest_db_path()


def _pytest_log_root():
    global _TEST_LOG_ROOT

    if _TEST_LOG_ROOT is not None:
        return _TEST_LOG_ROOT

    configured = os.environ.get("VCP_HUNTER_TEST_LOG_DIR")
    if configured:
        root = Path(configured).resolve()
        root.mkdir(parents=True, exist_ok=True)
    else:
        root = Path(tempfile.mkdtemp(prefix="vcp_hunter_logs_")).resolve()
        os.environ["VCP_HUNTER_TEST_LOG_DIR"] = str(root)
        atexit.register(shutil.rmtree, root, ignore_errors=True)

    os.environ["VCP_HUNTER_LOG_DIR"] = str(root)
    _TEST_LOG_ROOT = root
    return root


_pytest_log_root()


def _configure_qt_test_environment():
    global _QT_SETTINGS_CONFIGURED

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if _QT_SETTINGS_CONFIGURED:
        return

    root = _qsettings_test_root()
    os.environ.setdefault("VCP_HUNTER_TEST_QSETTINGS_DIR", str(root))
    _QT_SETTINGS_CONFIGURED = True


def _resolve_collection_arg(arg):
    target = str(arg or "").split("::", 1)[0]
    if not target or target.startswith("-"):
        return None

    path = Path(target)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _path_is_relative_to(path, parent):
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _is_selected_collection_path(path, config):
    selected = []
    for arg in getattr(config, "args", ()):
        resolved = _resolve_collection_arg(arg)
        if resolved is not None and resolved.exists():
            selected.append(resolved)

    if not selected:
        return True

    for target in selected:
        if target.is_dir() and (path == target or _path_is_relative_to(path, target)):
            return True
        if target.is_file() and path == target:
            return True
    return False


def pytest_collect_file(file_path, parent):
    path = Path(str(file_path)).resolve()
    if (
        path.suffix == ".py"
        and path.name.startswith("test_")
        and _is_selected_collection_path(path, parent.config)
        and _test_file_needs_qt_environment(str(path))
    ):
        _configure_qt_test_environment()
    return None


def _ensure_qt_application_instance():
    global _QT_APPLICATION

    _configure_qt_test_environment()

    from PyQt6.QtWidgets import QApplication

    existing_app = QApplication.instance()
    if existing_app is not None:
        _QT_APPLICATION = existing_app
        return existing_app

    _QT_APPLICATION = QApplication(sys.argv)
    return _QT_APPLICATION


def _runtime_global_store_if_available():
    module = sys.modules.get("core.global_store")
    if module is not None:
        return getattr(module, "global_store", None)
    if not _pyqt_is_loaded():
        return None

    try:
        from core.global_store import global_store
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        return None
    return global_store


def _flush_qt_deferred_deletes_if_available():
    if "PyQt6.QtWidgets" not in sys.modules:
        return

    try:
        from PyQt6.QtCore import QCoreApplication, QEvent
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            for _ in range(3):
                app.processEvents()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        pass


@pytest.fixture(scope="session")
def qt_application():
    """整个测试会话只创建一次 QApplication，防止重复创建导致崩溃"""
    app = _ensure_qt_application_instance()
    yield app


@pytest.fixture(autouse=True)
def _ensure_qt_application_for_qt_tests(request):
    if _node_needs_qt_application(request.node):
        request.getfixturevalue("qt_application")


@pytest.fixture(autouse=True)
def reset_global_runtime_state(_ensure_qt_application_for_qt_tests):
    """隔离跨测试的全局行情快照，避免排序和表格用例互相污染。"""
    global_store = _runtime_global_store_if_available()
    if global_store is not None:
        global_store.reset_runtime_state()

    yield

    if global_store is None:
        global_store = _runtime_global_store_if_available()
    if global_store is not None:
        global_store.reset_runtime_state()
    _flush_qt_deferred_deletes_if_available()
