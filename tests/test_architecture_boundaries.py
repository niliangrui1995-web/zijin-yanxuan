# -*- coding: utf-8 -*-

import ast
import importlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_FILE_SUFFIXES = {".py", ".pyw"}
IGNORED_SCAN_DIRS = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "data",
    "dist",
    "env",
    "htmlcov",
    "node_modules",
    "temp",
    "tmp",
    "venv",
}
BROAD_EXCEPTION_SCAN_ROOTS = ("app", "domains", "infra")
BROAD_EXCEPTION_ALLOWED_HANDLERS = {
    "app/services/ui_earnings_service.py:run_startup_gap_fill",
    "app/services/ui_earnings_service.py:run_gap_fill",
    "app/services/ui_earnings_service.py:run_routine_scan",
    "infra/diagnostics/runtime_health.py:<module>",
}
TYPE_ANNOTATION_SCAN_ROOTS = ("app", "domains", "infra")
MIN_RETURN_ANNOTATION_RATIO = 0.75
MIN_ARGUMENT_ANNOTATION_RATIO = 0.76


def _iter_python_files(root: Path):
    paths: list[Path] = []
    for current_root, dirs, files in os.walk(root):
        dirs[:] = [dirname for dirname in dirs if dirname.lower() not in IGNORED_SCAN_DIRS]
        current_path = Path(current_root)
        for filename in files:
            path = current_path / filename
            if path.suffix.lower() in PYTHON_FILE_SUFFIXES:
                paths.append(path)
    return sorted(
        path
        for path in paths
        if not any(part.lower() in IGNORED_SCAN_DIRS for part in path.relative_to(REPO_ROOT).parts)
    )


def _collect_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Call):
            imported_module = _extract_constant_import_call(node)
            if imported_module:
                modules.add(imported_module)
    return modules


def _extract_constant_import_call(node: ast.Call) -> str | None:
    if not node.args:
        return None

    first_arg = node.args[0]
    if not isinstance(first_arg, ast.Constant) or not isinstance(first_arg.value, str):
        return None

    if isinstance(node.func, ast.Name) and node.func.id in {"import_module", "__import__"}:
        return first_arg.value
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "import_module"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "importlib"
    ):
        return first_arg.value
    return None


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


def _find_root_app_services_imports(root: Path, allowed_imports: dict[str, set[str]]):
    violations: list[str] = []
    for path in _iter_python_files(root):
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.services":
                imported_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module == "app":
                imported_names.extend(alias.name for alias in node.names if alias.name == "services")
            elif isinstance(node, ast.Import):
                imported_names.extend(alias.name for alias in node.names if alias.name == "app.services")
        unexpected = sorted({name for name in imported_names if name not in allowed_imports.get(rel_path, set())})
        if unexpected:
            violations.append(f"{rel_path}: {', '.join(unexpected)}")
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


def _find_self_attribute_accesses(path: Path, attribute_name: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rel_path = path.relative_to(REPO_ROOT).as_posix()
    violations: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == attribute_name
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            violations.append(f"{rel_path}:{node.lineno}")
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
                if isinstance(node.func, ast.Attribute) and node.func.attr in positional_task_api_names and node.args:
                    first_arg = node.args[0]
                    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                        violations.append(rel_path)
                    elif isinstance(first_arg, ast.JoinedStr):
                        violations.append(rel_path)
    return sorted(set(violations))


def _find_vcp_constants_imports_outside_vcp(root: Path):
    violations: list[str] = []
    for path in _iter_python_files(root):
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        if rel_path.startswith("vcp/") or rel_path == "tests/test_architecture_boundaries.py":
            continue
        if "vcp.constants" in _collect_imports(path):
            violations.append(f"{rel_path}: vcp.constants")
    return violations


def _enclosing_function_name(node, parents: dict[ast.AST, ast.AST]) -> str:
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return parent.name
        parent = parents.get(parent)
    return "<module>"


def _find_broad_exception_handlers(root: Path) -> list[str]:
    violations: list[str] = []
    for path in _iter_python_files(root):
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if node.type is None:
                violations.append(f"{rel_path}:{_enclosing_function_name(node, parents)}")
            elif isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}:
                violations.append(f"{rel_path}:{_enclosing_function_name(node, parents)}")
    return sorted(set(violations))


def _type_annotation_stats(root: Path) -> dict[str, int]:
    stats = {
        "functions": 0,
        "return_annotated": 0,
        "arguments": 0,
        "argument_annotated": 0,
    }
    for path in _iter_python_files(root):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            stats["functions"] += 1
            if node.returns is not None:
                stats["return_annotated"] += 1
            arguments = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
            for argument in arguments:
                if argument.arg in {"self", "cls"}:
                    continue
                stats["arguments"] += 1
                if argument.annotation is not None:
                    stats["argument_annotated"] += 1
    return stats


def _safe_ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


def test_python_boundary_scan_includes_root_pyw_entry_and_excludes_noise_dirs():
    paths = {path.relative_to(REPO_ROOT).as_posix() for path in _iter_python_files(REPO_ROOT)}

    assert "vcp_hunter_qt.pyw" in paths
    assert not any(part.lower() in IGNORED_SCAN_DIRS for rel_path in paths for part in Path(rel_path).parts)


def test_root_app_services_scan_catches_from_app_import_services(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    ui_root = repo_root / "ui"
    ui_root.mkdir(parents=True)
    (ui_root / "from_app.py").write_text(
        "from app import services\nfrom app import services as svc\n",
        encoding="utf-8",
    )
    (ui_root / "import_root.py").write_text(
        "import app.services as services\n",
        encoding="utf-8",
    )
    (ui_root / "narrow.py").write_text(
        "from app.services.ui_event_service import domain_events\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(globals(), "REPO_ROOT", repo_root)

    violations = _find_root_app_services_imports(ui_root, {})

    assert violations == [
        "ui/from_app.py: services",
        "ui/import_root.py: app.services",
    ]


def test_prefix_scan_catches_constant_dynamic_import_calls(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    ui_root = repo_root / "ui"
    ui_root.mkdir(parents=True)
    (ui_root / "dynamic.py").write_text(
        "\n".join(
            [
                "from importlib import import_module",
                "import importlib",
                'import_module("vcp.constants")',
                'importlib.import_module("infra.diagnostics.runtime_health")',
                '__import__("domains.scan")',
                "import_module(module_name)",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(globals(), "REPO_ROOT", repo_root)

    violations = _find_prefix_violations(ui_root, {"domains", "infra", "vcp"})

    assert violations == ["ui/dynamic.py: domains.scan, infra.diagnostics.runtime_health, vcp.constants"]


def test_vcp_constants_import_is_side_effect_free(monkeypatch):
    makedirs_calls = []
    filterwarning_calls = []

    def fake_makedirs(*args, **kwargs):
        makedirs_calls.append((args, kwargs))

    def fake_filterwarnings(*args, **kwargs):
        filterwarning_calls.append((args, kwargs))

    monkeypatch.setattr(os, "makedirs", fake_makedirs)
    import warnings

    monkeypatch.setattr(warnings, "filterwarnings", fake_filterwarnings)
    sys.modules.pop("vcp.constants", None)

    importlib.import_module("vcp.constants")

    assert makedirs_calls == []
    assert filterwarning_calls == []


def test_runtime_paths_create_directories_only_when_explicitly_ensured(tmp_path, monkeypatch):
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "PROJECT_ROOT", str(tmp_path))
    cache_dir = Path(runtime_paths.get_data_dir("Cache"))

    assert cache_dir == tmp_path / "data" / "Cache"
    assert not cache_dir.exists()

    ensured_dir = Path(runtime_paths.ensure_cache_dir())

    assert ensured_dir == cache_dir
    assert cache_dir.is_dir()


def test_non_vcp_layers_do_not_import_vcp_constants_directly():
    violations = _find_vcp_constants_imports_outside_vcp(REPO_ROOT)
    assert not violations, "Non-VCP layers imported vcp.constants directly:\n" + "\n".join(violations)


def test_app_domain_infra_broad_exceptions_stay_allowlisted():
    broad_handlers: list[str] = []
    for root in BROAD_EXCEPTION_SCAN_ROOTS:
        broad_handlers.extend(_find_broad_exception_handlers(REPO_ROOT / root))
    violations = sorted(set(broad_handlers) - BROAD_EXCEPTION_ALLOWED_HANDLERS)
    assert not violations, (
        "App/domain/infra broad exception handlers must be narrowed or explicitly allowlisted:\n"
        + "\n".join(violations)
    )


def test_app_domain_infra_type_annotation_baseline_does_not_regress():
    stats = {
        "functions": 0,
        "return_annotated": 0,
        "arguments": 0,
        "argument_annotated": 0,
    }
    for root in TYPE_ANNOTATION_SCAN_ROOTS:
        root_stats = _type_annotation_stats(REPO_ROOT / root)
        for key, value in root_stats.items():
            stats[key] += value

    return_ratio = _safe_ratio(stats["return_annotated"], stats["functions"])
    argument_ratio = _safe_ratio(stats["argument_annotated"], stats["arguments"])

    assert return_ratio >= MIN_RETURN_ANNOTATION_RATIO, (
        f"App/domain/infra return annotation ratio regressed to {return_ratio:.3f}; "
        f"minimum is {MIN_RETURN_ANNOTATION_RATIO:.3f}"
    )
    assert argument_ratio >= MIN_ARGUMENT_ANNOTATION_RATIO, (
        f"App/domain/infra argument annotation ratio regressed to {argument_ratio:.3f}; "
        f"minimum is {MIN_ARGUMENT_ANNOTATION_RATIO:.3f}"
    )


def test_ui_layer_does_not_import_legacy_event_or_task_manager_modules():
    violations = _find_violations(
        REPO_ROOT / "ui",
        {"core.event_bus", "core.task_manager", "core.ui_stall_probe"},
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
    assert not violations, "UI layer bypassed app services and imported domain/infra modules directly:\n" + "\n".join(
        violations
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
    assert not violations, "Core layer imported UI/task orchestration helpers outside allowed hubs:\n" + "\n".join(
        violations
    )


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


def test_main_window_runtime_does_not_reach_into_workspace_tab_attributes():
    violations = _find_text_snippets(
        REPO_ROOT / "ui" / "main_window_runtime.py",
        {
            'getattr(workspace, "tab_',
            "tab_fund_holdings",
        },
    )
    assert not violations, "Main window runtime still reaches into concrete workspace tab attributes:\n" + "\n".join(
        violations
    )


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


def test_infra_layer_does_not_import_app_layer_directly():
    violations = _find_prefix_violations(
        REPO_ROOT / "infra",
        {"app"},
    )
    assert not violations, "Infra layer imported app layer directly:\n" + "\n".join(violations)


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
    assert not violations, "Window command service still references private main-window hooks:\n" + "\n".join(
        violations
    )


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


def test_startup_orchestrator_uses_host_adapter_instead_of_raw_main_window():
    path = REPO_ROOT / "core" / "startup_orchestrator.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}

    assert "StartupHostAdapter" in class_names
    violations = _find_self_attribute_accesses(path, "mw")
    assert not violations, "StartupOrchestrator still reaches raw main-window state:\n" + "\n".join(violations)


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
    assert not violations, "UI layer still imports core runtime/config compatibility modules directly:\n" + "\n".join(
        violations
    )


def test_ui_layer_uses_narrow_app_services_instead_of_runtime_barrel():
    violations = _find_violations(
        REPO_ROOT / "ui",
        {"app.services.ui_runtime_service"},
    )
    assert not violations, (
        "UI layer still imports the broad ui_runtime_service barrel instead of narrow ui_* services:\n"
        + "\n".join(violations)
    )


def test_ui_layer_uses_narrow_app_service_modules_instead_of_root_barrel():
    allowed_root_barrel_imports: dict[str, set[str]] = {}
    violations = _find_root_app_services_imports(
        REPO_ROOT / "ui",
        allowed_root_barrel_imports,
    )
    assert not violations, (
        "UI layer imported the broad app.services root barrel. "
        "Import from app.services.<narrow_service> instead:\n" + "\n".join(violations)
    )


def test_app_and_ui_layers_use_public_realtime_quote_port_for_cooldown():
    paths = _iter_python_files(REPO_ROOT / "app") + _iter_python_files(REPO_ROOT / "ui")
    violations = _find_text_snippets_in_files(paths, {"_enter_realtime_cooldown"})
    assert not violations, "App/UI layers should use the public RealtimeQuotePort cooldown method:\n" + "\n".join(
        violations
    )


def test_scripts_use_narrow_app_services_instead_of_runtime_barrel():
    violations = _find_violations(
        REPO_ROOT / "scripts",
        {"app.services.ui_runtime_service"},
    )
    assert not violations, (
        "Scripts still import the broad ui_runtime_service barrel instead of narrow ui_* services:\n"
        + "\n".join(violations)
    )


def test_domain_and_market_data_layers_use_domains_market_calendar_entrypoint():
    violations = []
    violations.extend(_find_violations(REPO_ROOT / "domains" / "scan", {"core.market_calendar"}))
    violations.extend(_find_violations(REPO_ROOT / "domains" / "earnings", {"core.market_calendar"}))
    violations.extend(_find_violations(REPO_ROOT / "infra" / "market_data", {"core.market_calendar"}))
    assert not violations, (
        "Domain/market-data layers still import core.market_calendar compatibility path:\n" + "\n".join(violations)
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
    assert not violations, "Non-core layers still depend on legacy event/signal compatibility shims:\n" + "\n".join(
        violations
    )


def test_module_owner_registry_exists():
    assert (REPO_ROOT / "docs" / "module-owners.md").exists()


def test_workspace_facade_and_services_do_not_reach_into_tab_private_state():
    workspace_files = [
        REPO_ROOT / "ui" / "workspaces" / "classic_workspace.py",
        REPO_ROOT / "ui" / "workspaces" / "quote_universe_service.py",
        REPO_ROOT / "ui" / "workspaces" / "stock_context_service.py",
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
