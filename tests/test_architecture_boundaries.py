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


def _find_prefix_violations(root: Path, banned_prefixes: set[str], *, allowed_paths: set[str] | None = None):
    allowed_paths = allowed_paths or set()
    violations: list[str] = []
    for path in _iter_python_files(root):
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        if rel_path in allowed_paths:
            continue
        imported = _collect_imports(path)
        matched = sorted(
            module
            for module in imported
            if any(module == prefix or module.startswith(f"{prefix}.") for prefix in banned_prefixes)
        )
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


def _find_text_snippets_in_files(paths: list[Path], banned_snippets: set[str]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        matched = _find_text_snippets(path, banned_snippets)
        if not matched:
            continue
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        violations.append(f"{rel_path}: {', '.join(matched)}")
    return violations


def _find_literal_task_id_usage(root: Path, *, allowed_paths: set[str] | None = None):
    allowed_paths = allowed_paths or set()
    violations: list[str] = []
    positional_task_api_names = {"abandon", "abandon_task", "is_active", "is_active_task"}
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
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in positional_task_api_names
                    and node.args
                ):
                    first_arg = node.args[0]
                    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                        violations.append(rel_path)
                    elif isinstance(first_arg, ast.JoinedStr):
                        violations.append(rel_path)
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


def test_ui_layer_does_not_import_domains_or_infra_runtime_modules_directly():
    violations = _find_prefix_violations(
        REPO_ROOT / "ui",
        {"domains", "infra"},
    )
    assert not violations, (
        "UI layer bypassed app services and imported domain/infra modules directly:\n"
        + "\n".join(violations)
    )


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
        allowed_paths={
            "core/event_bus.py",
            "core/startup_orchestrator.py",
            "core/market_calendar.py",
        },
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


def test_app_layer_does_not_import_ui_modules_directly():
    violations = _find_prefix_violations(
        REPO_ROOT / "app",
        {"ui"},
    )
    assert not violations, "App layer imported UI modules directly:\n" + "\n".join(violations)


def test_window_command_service_avoids_ui_theme_and_private_window_hooks():
    path = REPO_ROOT / "app" / "use_cases" / "window_command_service.py"
    imports = _collect_imports(path)
    assert "ui.theme" not in imports

    violations = _find_text_snippets(
        path,
        {
            "_action_refresh_f5",
            "_activate_workspace_tab",
            "_apply_table_density",
            "_on_show_kline",
        },
    )
    assert not violations, "Window command service still references private main-window hooks:\n" + "\n".join(violations)


def test_main_window_runtime_bootstrap_goes_through_app_services():
    path = REPO_ROOT / "ui" / "main_window_qt.py"
    imports = _collect_imports(path)
    forbidden = {
        "core.startup_orchestrator",
        "vcp.data_provider",
        "vcp.engine",
    }
    matched = sorted(forbidden & imports)
    assert not matched, "Main window still imports legacy runtime bootstrap modules directly:\n" + "\n".join(matched)


def test_selected_ui_modules_do_not_import_vcp_engine_directly():
    module_paths = [
        "ui/kline_window_qt.py",
        "ui/tabs/base_stock_refresh.py",
        "ui/tabs/lhb_tab.py",
        "ui/tabs/scan_tab.py",
        "ui/workers/central_quotes_worker.py",
        "ui/workers/rt_scan_worker.py",
        "ui/workers/scan_worker.py",
    ]
    violations: list[str] = []
    for rel_path in module_paths:
        imports = _collect_imports(REPO_ROOT / rel_path)
        if "vcp.engine" in imports:
            violations.append(rel_path)
    assert not violations, "Selected UI runtime modules still depend on vcp.engine directly:\n" + "\n".join(violations)


def test_ui_layer_does_not_import_vcp_modules_directly():
    violations = _find_prefix_violations(
        REPO_ROOT / "ui",
        {"vcp"},
    )
    assert not violations, "UI layer still imports legacy vcp modules directly:\n" + "\n".join(violations)


def test_ui_layer_does_not_import_core_runtime_facades_directly():
    violations = _find_violations(
        REPO_ROOT / "ui",
        {
            "core.app_config",
            "core.background_job_runner",
            "core.domain_events",
            "core.market_calendar",
            "core.ui_signals",
        },
    )
    assert not violations, (
        "UI layer still imports core runtime/config compatibility modules directly:\n"
        + "\n".join(violations)
    )


def test_domain_and_market_data_layers_use_domains_market_calendar_entrypoint():
    violations = []
    violations.extend(_find_violations(REPO_ROOT / "domains" / "scan", {"core.market_calendar"}))
    violations.extend(_find_violations(REPO_ROOT / "domains" / "earnings", {"core.market_calendar"}))
    violations.extend(_find_violations(REPO_ROOT / "infra" / "market_data", {"core.market_calendar"}))
    assert not violations, (
        "Domain/market-data layers still import core.market_calendar compatibility path:\n"
        + "\n".join(violations)
    )


def test_non_core_layers_do_not_import_legacy_event_signal_shims():
    violations = []
    for root in ("app", "domains", "infra", "vcp"):
        violations.extend(
            _find_violations(
                REPO_ROOT / root,
                {"core.domain_events", "core.ui_signals"},
                allowed_paths={"infra/events/ui_signal_hub.py"},
            )
        )
    assert not violations, (
        "Non-core layers still depend on legacy event/signal compatibility shims:\n"
        + "\n".join(violations)
    )


def test_module_owner_registry_exists():
    assert (REPO_ROOT / "docs" / "module-owners.md").exists()


def test_workspace_facade_and_services_do_not_reach_into_tab_private_state():
    workspace_files = [
        REPO_ROOT / "ui" / "workspaces" / "classic_workspace.py",
        REPO_ROOT / "ui" / "workspaces" / "quote_universe_service.py",
        REPO_ROOT / "ui" / "workspaces" / "watchlist_radar_service.py",
        REPO_ROOT / "ui" / "workspaces" / "workspace_facade.py",
        REPO_ROOT / "ui" / "workspaces" / "workspace_navigation_service.py",
        REPO_ROOT / "ui" / "workspaces" / "workspace_table_service.py",
    ]
    violations = _find_text_snippets_in_files(
        workspace_files,
        {
            ".asian_table",
            ".model.row_data",
            ".na_daily_table",
            ".table_rt",
            ".table_scan",
            "FOREIGN_KEYWORDS",
            "_auto_refresh_realtime(",
            "_toggle_rt_monitor(",
            "find_scan_result(",
            "workspace.tab_",
        },
    )
    assert not violations, (
        "Workspace facade/services still reach into tab private state instead of public capabilities:\n"
        + "\n".join(violations)
    )
