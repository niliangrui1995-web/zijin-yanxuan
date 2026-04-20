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


def test_ui_layer_does_not_import_legacy_event_or_task_manager_modules():
    violations = _find_violations(
        REPO_ROOT / "ui",
        {"core.event_bus", "core.task_manager"},
    )
    assert not violations, "UI layer imported forbidden legacy modules:\n" + "\n".join(violations)


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
