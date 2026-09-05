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
    "app/bootstrap/startup_orchestrator.py:_global_earnings_calendar_cache_snapshot",
    "app/bootstrap/startup_orchestrator.py:_mark_global_earnings_calendar_degraded_events",
    "app/bootstrap/startup_orchestrator.py:refresh_global_earnings_calendar",
    "app/services/ui_earnings_service.py:run_startup_gap_fill",
    "app/services/ui_earnings_service.py:run_gap_fill",
    "app/services/ui_earnings_service.py:run_routine_scan",
    # The isolated F5 process must persist a terminal receipt even for SystemExit,
    # KeyboardInterrupt and MemoryError; cleanup must preserve that outcome.
    # Covered by test_f5_worker_main_coverage.py terminalization/cleanup tests.
    "app/workers/f5_worker_main.py:execute_request",
    "app/workers/f5_worker_main.py:main",
    "app/workers/f5_worker_main.py:_persist_terminal_result",
    "app/workers/f5_worker_main.py:_discard_failed_generation",
    "domains/earnings/refresh_cache.py:main",
    "domains/global_earnings_calendar/refresh_cache.py:main",
    "domains/global_earnings_calendar/http_utils.py:_sanitized_exception",
    "domains/global_earnings_calendar/http_utils.py:raise_for_status",
    # Independent provider failures are converted to degraded cache state; process-control signals are re-raised.
    "domains/global_earnings_calendar/providers/_utils.py:_worker",
    "domains/global_earnings_calendar/service.py:_collect_serial_provider_fetch_results",
    "domains/global_earnings_calendar/service.py:_collect_provider_fetch_results",
    "domains/global_earnings_calendar/service.py:_fetch",
    "domains/global_earnings_calendar/service.py:refresh_events",
    "infra/diagnostics/runtime_health.py:<module>",
    "infra/diagnostics/ui_exception_boundary.py:ui_exception_hook",
    "infra/market_data/asian_quote_provider.py:_call_yfinance",
    "infra/tasks/owner_lifecycle.py:_deliver",
    # Application terminal callbacks must not mask a submission/cleanup failure.
    "infra/tasks/owner_lifecycle.py:deliver_terminated_once",
    "infra/tasks/task_scheduler.py:_deliver_submission_terminated_callback",
    "infra/tasks/task_scheduler.py:_handle_terminated",
    "infra/tasks/task_scheduler.py:run",
    "infra/storage/data_store.py:transaction",
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


def _collect_import_targets(path: Path, prefix: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(
                alias.name
                for alias in node.names
                if alias.name == prefix or alias.name.startswith(f"{prefix}.")
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == prefix or module.startswith(f"{prefix}."):
                targets.update(f"{module}.{alias.name}" for alias in node.names)
    return targets


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


def test_vcp_fetcher_imports_are_centralized_behind_market_data_adapters():
    violations = []
    allowed_paths = {
        "infra/market_data/asian_kline_provider.py",
        "infra/market_data/yfinance_session.py",
    }
    for root in ("app", "domains", "infra", "ui"):
        violations.extend(
            _find_prefix_violations(
                REPO_ROOT / root,
                {"vcp.fetchers"},
                allowed_paths=allowed_paths,
            )
        )
    assert not violations, "vcp.fetchers imports must go through infra.market_data adapters:\n" + "\n".join(violations)


def test_app_and_domain_layers_do_not_import_vcp_directly():
    violations: list[str] = []
    for root in ("app", "domains"):
        violations.extend(_find_prefix_violations(REPO_ROOT / root, {"vcp"}))
    assert not violations, (
        "App/domain code must reach legacy vcp implementations through typed infra adapters:\n"
        + "\n".join(violations)
    )


def test_domains_do_not_depend_on_app_services_except_deprecated_scheduler_shim():
    violations = _find_prefix_violations(
        REPO_ROOT / "domains",
        {"app"},
        allowed_paths={"domains/earnings/scheduler.py"},
    )
    assert not violations, "Domain code must not depend on app/UI services:\n" + "\n".join(violations)


def test_infra_vcp_compatibility_imports_stay_on_a_decrementing_allowlist():
    allowed_targets = {
        "infra/market_data/adjustment_service.py": {
            "vcp.data_provider_local.apply_forward_adjustment",
            "vcp.data_provider_local.get_market_code",
            "vcp.data_provider_local.load_local_gbbq",
            "vcp.data_provider_local.load_local_gbbq_for_code",
        },
        "infra/market_data/asian_kline_provider.py": {"vcp.fetchers.asian_kline_fetcher"},
        "infra/market_data/realtime_quote_provider.py": {
            "vcp.realtime_quote_runtime.RealtimeQuoteRuntime"
        },
        "infra/market_data/tdx_data_provider.py": {
            "vcp.data_provider_cache.compact_runtime_caches",
            "vcp.data_provider_cache.downcast_memory",
            "vcp.data_provider_cache.prune_rt_quote_cache",
            "vcp.data_provider_history_mixin.TdxDataProviderHistoryMixin",
            "vcp.data_provider_local.fetch_from_local_tdx",
            "vcp.data_provider_realtime_mixin.TdxDataProviderRealtimeMixin",
            "vcp.utils._load_tdx_local_config",
        },
        "infra/market_data/vcp_scan_adapter.py": {
            "vcp.data_provider_local",
            "vcp.engine_external",
            "vcp.polars_engine",
            "vcp.sector.SectorManager",
        },
        "infra/market_data/yfinance_session.py": {"vcp.fetchers.yf_session"},
    }
    violations: list[str] = []
    for path in _iter_python_files(REPO_ROOT / "infra"):
        repo_path = path.relative_to(REPO_ROOT).as_posix()
        actual_targets = _collect_import_targets(path, "vcp")
        unexpected = sorted(actual_targets - allowed_targets.get(repo_path, set()))
        violations.extend(f"{repo_path}: {target}" for target in unexpected)
    assert not violations, "New infra -> vcp compatibility imports are forbidden:\n" + "\n".join(violations)


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


def test_stock_context_service_delegates_persistence_to_app_services():
    path = REPO_ROOT / "ui" / "workspaces" / "stock_context_service.py"
    imports = _collect_imports(path)
    forbidden_imports = {
        "json",
        "sqlite3",
        "pathlib",
        "core.ai_industry_chain_pool",
        "core.data_store",
        "core.json_cache",
        "core.lhb_pool_manager",
    }
    matched = sorted(forbidden_imports & imports)
    assert not matched, "StockContextService still owns storage I/O imports:\n" + "\n".join(matched)


def test_stock_context_app_service_delegates_persistence_to_infra_repository():
    path = REPO_ROOT / "app" / "services" / "stock_context_snapshot_service.py"
    imports = _collect_imports(path)
    forbidden_imports = {"json", "sqlite3", "pathlib", "core.data_store", "core.json_cache"}
    matched = sorted(forbidden_imports & imports)
    assert not matched, "Stock-context app orchestration still owns persistence:\n" + "\n".join(matched)
    assert "infra.storage.stock_context_repository" in imports


def test_stock_context_domain_and_app_pipeline_stay_qt_widget_free():
    domain_root = REPO_ROOT / "domains" / "stock_context"
    domain_paths = list(_iter_python_files(domain_root))
    assert domain_paths, "StockContext domain package is missing"
    violations = _find_prefix_violations(
        domain_root,
        {"PyQt5", "PyQt6", "PySide2", "PySide6", "app", "core", "infra", "qtpy", "ui"},
    )
    app_paths = sorted((REPO_ROOT / "app" / "services").glob("stock_context_*.py"))
    assert app_paths, "StockContext app service pipeline is missing"
    forbidden_prefixes = ("PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy", "ui")
    for path in app_paths:
        matched = sorted(
            module
            for module in _collect_imports(path)
            if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
        )
        violations.extend(f"{path.relative_to(REPO_ROOT).as_posix()}: {module}" for module in matched)

    assert not violations, "StockContext pipeline regained Qt/widget dependencies:\n" + "\n".join(violations)


def test_asian_market_ui_delegates_http_and_cache_io_to_app_services():
    paths = [
        REPO_ROOT / "ui" / "tabs" / "asian_market_workers.py",
        REPO_ROOT / "ui" / "tabs" / "asian_market_tab.py",
        REPO_ROOT / "ui" / "services" / "asian_market_runtime_service.py",
    ]
    forbidden_imports = {"requests", "ui.services.asian_market_http"}
    import_violations: list[str] = []
    for path in paths:
        matched = sorted(forbidden_imports & _collect_imports(path))
        import_violations.extend(f"{path.relative_to(REPO_ROOT)}:{name}" for name in matched)
    snippet_violations = _find_text_snippets_in_files(
        paths,
        {
            "with open(",
            "json.load(",
            "json.dump(",
        },
    )
    violations = import_violations + snippet_violations
    assert not violations, "Asian-market UI still owns HTTP/cache I/O:\n" + "\n".join(violations)


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


def test_core_data_store_is_a_compatibility_shim_over_infra_storage():
    path = REPO_ROOT / "core" / "data_store.py"
    imports = _collect_imports(path)
    assert "infra.storage.data_store" in imports
    assert "sqlite3" not in imports, "SQLite implementation belongs to infra.storage, not core"


def test_core_pool_modules_are_compatibility_shims_over_app_services():
    expectations = {
        "core/ai_industry_chain_pool.py": "app.services.ui_industry_chain_service",
        "core/lhb_pool_manager.py": "app.services.ui_lhb_pool_service",
    }
    for rel_path, canonical_module in expectations.items():
        imports = _collect_imports(REPO_ROOT / rel_path)
        assert canonical_module in imports, f"{rel_path} must delegate to {canonical_module}"
        assert "sqlite3" not in imports, f"{rel_path} still owns persistence implementation"


def test_pool_app_facades_delegate_storage_to_infra_repositories():
    expectations = {
        "app/services/ui_industry_chain_service.py": "infra.storage.industry_chain_repository",
        "app/services/ui_lhb_pool_service.py": "infra.storage.lhb_pool_repository",
    }
    forbidden = {"json", "openpyxl", "os", "pathlib", "sqlite3", "tempfile"}
    violations: list[str] = []
    for rel_path, repository_module in expectations.items():
        imports = _collect_imports(REPO_ROOT / rel_path)
        if repository_module not in imports:
            violations.append(f"{rel_path}: missing {repository_module}")
        matched = sorted(forbidden & imports)
        violations.extend(f"{rel_path}: {module}" for module in matched)
    assert not violations, "Pool app facade still owns storage I/O:\n" + "\n".join(violations)


def test_industry_chain_domain_contains_only_pure_rules():
    domain_root = REPO_ROOT / "domains" / "industry_chain"
    forbidden = {"json", "pathlib", "threading", "openpyxl", "infra", "app", "ui", "core"}
    violations = _find_prefix_violations(domain_root, forbidden)
    assert not violations, "Industry-chain domain still owns I/O or outer-layer dependencies:\n" + "\n".join(violations)


def test_lhb_domain_contains_only_pure_policy():
    domain_root = REPO_ROOT / "domains" / "lhb"
    forbidden = {
        "json",
        "sqlite3",
        "os",
        "pathlib",
        "tempfile",
        "threading",
        "infra",
        "app",
        "ui",
        "core",
    }
    violations = _find_prefix_violations(domain_root, forbidden)
    assert not violations, "LHB domain still owns persistence or outer-layer dependencies:\n" + "\n".join(violations)


def test_migrated_core_modules_are_only_compatibility_entrypoints():
    forbidden = {
        "core.ai_industry_chain_pool",
        "core.data_store",
        "core.json_cache",
        "core.lhb_pool_manager",
        "core.startup_orchestrator",
    }
    violations: list[str] = []
    for root_name in ("app", "domains", "infra", "ui"):
        violations.extend(_find_violations(REPO_ROOT / root_name, forbidden))
    assert not violations, "Production code still imports migrated core compatibility modules:\n" + "\n".join(violations)


def test_startup_orchestrator_implementation_lives_in_app_bootstrap():
    canonical_path = REPO_ROOT / "app" / "bootstrap" / "startup_orchestrator.py"
    compat_path = REPO_ROOT / "core" / "startup_orchestrator.py"
    assert canonical_path.exists(), "Startup orchestrator must live in app.bootstrap"
    compat_imports = _collect_imports(compat_path)
    assert "app.bootstrap.startup_orchestrator" in compat_imports
    compat_tree = ast.parse(compat_path.read_text(encoding="utf-8"), filename=str(compat_path))
    assert not any(isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) for node in compat_tree.body)


def test_bootstrap_uses_public_main_window_host_hooks():
    paths = [
        REPO_ROOT / "app" / "bootstrap" / "application_bootstrap.py",
        REPO_ROOT / "app" / "bootstrap" / "startup_orchestrator.py",
    ]
    violations = _find_text_snippets_in_files(
        paths,
        {
            '"_call_in_ui"',
            '"_is_closing"',
            '"_on_smart_startup_online_done"',
            '"_refresh_code_count_label_from_provider"',
            '"_set_titlebar_sync_state"',
            '"_update_network_ui"',
            '"_workspace"',
        },
    )
    assert not violations, "App bootstrap must use public host hooks instead of UI private state:\n" + "\n".join(violations)


def test_legacy_earnings_scheduler_delegates_to_owner_lifecycle_service():
    path = REPO_ROOT / "domains" / "earnings" / "scheduler.py"
    source = path.read_text(encoding="utf-8")
    imports = _collect_imports(path)
    assert "app.services.ui_earnings_service" in imports
    assert "QThread" not in source
    assert "__deprecated__ = True" in source


def test_ui_layer_does_not_import_core_pool_implementations_directly():
    violations = _find_violations(
        REPO_ROOT / "ui",
        {"core.ai_industry_chain_pool", "core.lhb_pool_manager"},
    )
    assert not violations, "UI must consume narrow app pool services:\n" + "\n".join(violations)


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
    path = REPO_ROOT / "app" / "bootstrap" / "startup_orchestrator.py"
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
            "core.data_store",
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


def test_ui_layer_does_not_read_provider_private_health_fields():
    violations = _find_text_snippets_in_files(
        _iter_python_files(REPO_ROOT / "ui"),
        {
            '"_offline"',
            "'_offline'",
            '"_build_offline_quotes"',
            '"_rt_api_call_timeout_sec"',
            '"_rt_quote_batch_size"',
            "_rt_eastmoney_cooldown_until",
            "_rt_eastmoney_last_error",
            "data_provider._",
        },
    )
    assert not violations, "UI must consume typed provider ports instead of private fields:\n" + "\n".join(violations)


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
            "find_scan_result(",
            "workspace.tab_",
        },
    )
    assert not violations, (
        "Workspace facade/services still reach into tab private state instead of public capabilities:\n"
        + "\n".join(violations)
    )


def test_stock_context_ui_bridge_uses_public_readiness_and_keeps_commands_in_facade():
    adapter_path = REPO_ROOT / "ui" / "workspaces" / "stock_context_widget_adapter.py"
    candidate_path = REPO_ROOT / "ui" / "tabs" / "stock_candidate_tab.py"
    service_path = REPO_ROOT / "ui" / "workspaces" / "stock_context_service.py"

    violations = _find_text_snippets(adapter_path, {'"_pool_load_in_progress"', "'_pool_load_in_progress'"})
    violations.extend(
        _find_text_snippets(
            candidate_path,
            {'"get_loaded_tab"', "'get_loaded_tab'", '"_workspace_background_preload_ready"'},
        )
    )
    service_source = service_path.read_text(encoding="utf-8")
    for method_name in ("refresh_watchlist_names", "prime_watchlist_state"):
        if f"def {method_name}(" in service_source:
            violations.append(f"stock_context_service.py: {method_name}")

    assert not violations, (
        "StockContext UI bridge must use public readiness contracts and keep watchlist commands in WorkspaceFacade:\n"
        + "\n".join(violations)
    )


def test_ui_background_tasks_are_owner_bound_except_shared_market_cap_batcher():
    allowed = {"ui/tabs/base_stock_refresh.py:MarketCapRefreshBatcher.flush"}
    violations: list[str] = []

    class _RawBackgroundCallVisitor(ast.NodeVisitor):
        def __init__(self, rel_path: str) -> None:
            self.rel_path = rel_path
            self.scope: list[str] = []

        def _visit_scope(self, node) -> None:
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_ClassDef = _visit_scope
        visit_FunctionDef = _visit_scope
        visit_AsyncFunctionDef = _visit_scope

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Attribute) and node.func.attr == "run_in_background":
                location = f"{self.rel_path}:{'.'.join(self.scope) or '<module>'}"
                if location not in allowed:
                    violations.append(f"{location}:{node.lineno}")
            self.generic_visit(node)

    for path in _iter_python_files(REPO_ROOT / "ui"):
        rel_path = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        _RawBackgroundCallVisitor(rel_path).visit(tree)

    assert not violations, (
        "UI owner-bound jobs must use TaskLifecycleGroup; only the process-shared market-cap batcher may submit raw:\n"
        + "\n".join(violations)
    )
