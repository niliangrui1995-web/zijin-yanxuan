# -*- coding: utf-8 -*-

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _iter_python_files(root: Path):
    return sorted(
        path for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _collect_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _find_violations(root: Path, banned_modules: set[str], *, allowed_paths: set[str] | None = None):
    allowed_paths = allowed_paths or set()
    violations: list[str] = []
    for path in _iter_python_files(root):
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        if rel_path in allowed_paths:
            continue
        imported = _collect_imports(path)
        matched = sorted(module for module in banned_modules if module in imported)
        if matched:
            violations.append(f"{rel_path}: {', '.join(matched)}")
    return violations


def _find_qsettings_imports(root: Path, *, allowed_paths: set[str] | None = None):
    allowed_paths = allowed_paths or set()
    violations: list[str] = []
    for path in _iter_python_files(root):
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        if rel_path in allowed_paths:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "PyQt6.QtCore":
                for alias in node.names:
                    if alias.name == "QSettings":
                        violations.append(rel_path)
                        break
            if rel_path in violations:
                break
    return violations


def _find_text_snippets(path: Path, banned_snippets: set[str]) -> list[str]:
    content = path.read_text(encoding="utf-8")
    return sorted(snippet for snippet in banned_snippets if snippet in content)


def _find_literal_task_id_usage(root: Path, *, allowed_paths: set[str] | None = None):
    allowed_paths = allowed_paths or set()
    violations: list[str] = []
    for path in _iter_python_files(root):
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        if rel_path in allowed_paths:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.endswith("task_id"):
                        if isinstance(node.value, (ast.Constant, ast.JoinedStr)):
                            if not isinstance(node.value, ast.Constant) or isinstance(node.value.value, str):
                                violations.append(rel_path)
                                break
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg not in {"task_id", "quote_task_id"}:
                        continue
                    if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                        violations.append(rel_path)
                        break
                    if isinstance(keyword.value, ast.JoinedStr):
                        violations.append(rel_path)
                        break
    return sorted(set(violations))


def test_ui_layer_does_not_import_legacy_event_or_task_manager_modules():
    violations = _find_violations(
        REPO_ROOT / "ui",
        {"core.event_bus", "core.task_manager"},
    )
    assert not violations, "UI layer imported forbidden legacy modules:\n" + "\n".join(violations)


def test_ui_layer_does_not_import_system_automation_modules_directly():
    violations = _find_violations(
        REPO_ROOT / "ui",
        {"subprocess", "pyautogui", "win32con", "win32gui"},
    )
    assert not violations, "UI layer imported system/process automation modules directly:\n" + "\n".join(violations)


def test_vcp_layer_does_not_depend_on_ui_signals_or_job_runner():
    violations = _find_violations(
        REPO_ROOT / "vcp",
        {"core.ui_signals", "core.background_job_runner"},
    )
    assert not violations, "VCP layer crossed UI/task orchestration boundary:\n" + "\n".join(violations)


def test_core_ui_signal_and_job_runner_usage_is_centralized():
    violations = _find_violations(
        REPO_ROOT / "core",
        {"core.ui_signals", "core.background_job_runner"},
        allowed_paths={"core/event_bus.py", "core/startup_orchestrator.py"},
    )
    assert not violations, "Core layer imported UI/task orchestration helpers outside allowed hubs:\n" + "\n".join(violations)


def test_only_settings_infrastructure_uses_qsettings_directly():
    violations = _find_qsettings_imports(
        REPO_ROOT,
        allowed_paths={
            "infra/settings/settings_repository.py",
            "infra/settings/settings_schema.py",
            "tests/test_app_config.py",
        },
    )
    assert not violations, "Direct QSettings usage escaped the settings infrastructure:\n" + "\n".join(violations)


def test_main_window_does_not_call_tab_private_hooks_for_workspace_actions():
    violations = _find_text_snippets(
        REPO_ROOT / "ui" / "main_window_qt.py",
        {
            "_show_scan_settings",
            "_manual_refresh",
            "btn_update.click(",
            "find_scan_result(",
        },
    )
    assert not violations, "Main window still depends on tab private hooks:\n" + "\n".join(violations)


def test_ui_layer_uses_task_registry_instead_of_literal_task_ids():
    violations = _find_literal_task_id_usage(REPO_ROOT / "ui")
    assert not violations, "UI layer still contains literal background task ids:\n" + "\n".join(violations)


def test_legacy_quote_terminal_shim_has_been_removed():
    assert not (REPO_ROOT / "infra" / "navigation" / "quote_terminal_service.py").exists()
