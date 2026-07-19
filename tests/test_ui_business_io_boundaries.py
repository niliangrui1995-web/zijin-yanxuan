# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = REPO_ROOT / "ui"

_STATIC_RESOURCE_IO_ALLOWLIST = {
    "ui/components/kline_window_manager.py:_load_kline_window_class:exists",
    "ui/components/stock_context_menu.py:open_codex_project_thread:exists",
    "ui/kline_chart_payload.py:_load_kline_script:read_text",
    "ui/kline_window_qt.py:_configure_kline_window_shell:exists",
    "ui/splash_screen.py:SplashScreen.__init__:exists",
}
_BUSINESS_IO_CALLS = {
    "exists",
    "getmtime",
    "glob",
    "makedirs",
    "open",
    "read_text",
    "stat",
    "write_text",
}


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            class_parent = parents.get(parent)
            if isinstance(class_parent, ast.ClassDef):
                return f"{class_parent.name}.{parent.name}"
            return parent.name
        parent = parents.get(parent)
    return "<module>"


def _direct_ui_business_io() -> set[str]:
    findings: set[str] = set()
    for path in _python_files(UI_ROOT):
        relative = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                call_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
            else:
                continue
            if call_name in _BUSINESS_IO_CALLS:
                findings.add(f"{relative}:{_enclosing_function(node, parents)}:{call_name}")
    return findings


def test_ui_business_io_is_behind_app_facades_except_static_resources():
    assert _direct_ui_business_io() == _STATIC_RESOURCE_IO_ALLOWLIST


def test_ui_json_cache_consumers_do_not_import_core_compatibility_shim():
    consumers = (
        "ui/services/auto_refresh_tasks.py",
        "ui/tabs/base_stock_refresh.py",
        "ui/tabs/foreign_block_trade_tab.py",
        "ui/tabs/watchlist_tab.py",
    )
    violations = [path for path in consumers if "core.json_cache" in _imports(REPO_ROOT / path)]
    assert violations == []


def test_foreign_block_trade_ui_uses_dedicated_cache_facade_without_cache_schema():
    path = REPO_ROOT / "ui/tabs/foreign_block_trade_tab.py"
    source = path.read_text(encoding="utf-8")
    imports = _imports(path)
    tree = ast.parse(source, filename=str(path))
    assigned_names = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            [*node.targets] if isinstance(node, ast.Assign) else [node.target]
        )
        if isinstance(target, ast.Name)
    }
    function_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "app.services.foreign_block_cache_service" in imports
    assert "app.services.ui_json_cache_service" not in imports
    assert "core.json_cache" not in imports
    assert "infra.storage.foreign_block_repository" not in imports
    assert "_BLOCK_TRADE_CACHE_FILE" not in assigned_names
    assert "_build_cache_payload" not in function_names
    assert "build_foreign_block_local_cache_payload" not in function_names


def test_auto_refresh_uses_foreign_block_cache_facade_without_private_path():
    path = REPO_ROOT / "ui/services/auto_refresh_tasks.py"
    source = path.read_text(encoding="utf-8")
    imports = _imports(path)

    assert "app.services.foreign_block_cache_service" in imports
    assert "core.json_cache" not in imports
    assert "app.services.ui_json_cache_service" not in imports
    assert "_BLOCK_TRADE_CACHE_FILE" not in source
    assert "save_json_file" not in source


def test_na_daily_ui_module_is_a_compatibility_alias_without_business_io():
    path = REPO_ROOT / "ui/services/na_daily_service.py"
    forbidden_roots = {"PyQt6", "core", "glob", "json", "os", "re"}
    imported_roots = {module.split(".", 1)[0] for module in _imports(path)}
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    implementation_nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert imported_roots.isdisjoint(forbidden_roots)
    assert implementation_nodes == []


def test_production_ui_uses_na_daily_app_service():
    violations = []
    for path in _python_files(UI_ROOT):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if "ui.services.na_daily_service" in _imports(path):
            violations.append(relative)
    assert violations == []


def test_core_json_cache_is_only_an_infra_compatibility_shim():
    path = REPO_ROOT / "core/json_cache.py"
    imported_roots = {module.split(".", 1)[0] for module in _imports(path)}
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    implementation_nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert imported_roots.isdisjoint({"json", "os"})
    assert "infra" in imported_roots
    assert implementation_nodes == []


def test_ui_does_not_own_market_fetch_process_or_http_clients():
    forbidden_roots = {"akshare", "requests", "subprocess"}
    import_violations = []
    for path in _python_files(UI_ROOT):
        imported_roots = {module.split(".", 1)[0] for module in _imports(path)}
        if imported_roots & forbidden_roots:
            import_violations.append(path.relative_to(REPO_ROOT).as_posix())

    foreign_path = REPO_ROOT / "ui/tabs/foreign_block_trade_tab.py"
    foreign_source = foreign_path.read_text(encoding="utf-8")
    foreign_process_markers = {
        "_AKSHARE_FETCH_SNIPPET",
        "_run_domestic_akshare",
        "build_domestic_process_env",
        "run_process",
        "windows_no_window_kwargs",
    }

    assert import_violations == []
    assert [marker for marker in foreign_process_markers if marker in foreign_source] == []


def test_production_layers_do_not_depend_on_lhb_ui_worker_fetchers():
    violations = []
    for root in (REPO_ROOT / "app", UI_ROOT):
        for path in _python_files(root):
            if path == REPO_ROOT / "ui/workers/lhb_worker.py":
                continue
            if "ui.workers.lhb_worker" in _imports(path):
                violations.append(path.relative_to(REPO_ROOT).as_posix())

    assert violations == []


def test_lhb_ui_worker_is_only_a_compatibility_alias():
    path = REPO_ROOT / "ui/workers/lhb_worker.py"
    imports = _imports(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    implementation_nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    assert "app.services.lhb_market_data_service" in imports
    assert "akshare" not in imports
    assert implementation_nodes == []


def test_foreign_block_ui_fetches_through_narrow_app_facade():
    path = REPO_ROOT / "ui/tabs/foreign_block_trade_tab.py"
    imports = _imports(path)

    assert "app.services.foreign_block_market_data_service" in imports
    assert "infra.market_data.foreign_block_provider" not in imports


def test_market_data_app_facades_own_the_infra_provider_dependencies():
    lhb_imports = _imports(REPO_ROOT / "app/services/lhb_market_data_service.py")
    foreign_imports = _imports(REPO_ROOT / "app/services/foreign_block_market_data_service.py")
    auto_refresh_imports = _imports(REPO_ROOT / "ui/services/auto_refresh_tasks.py")

    assert "infra.market_data.lhb_provider" in lhb_imports
    assert "infra.market_data.foreign_block_provider" in foreign_imports
    assert "app.services.foreign_block_market_data_service" in auto_refresh_imports
    assert "ui.tabs.foreign_block_trade_tab" not in auto_refresh_imports


def test_auto_refresh_does_not_own_earnings_process_protocol():
    path = REPO_ROOT / "ui/services/auto_refresh_tasks.py"
    source = path.read_text(encoding="utf-8")
    imports = _imports(path)
    process_protocol_markers = {
        "_parse_earnings_refresh_stdout",
        "_run_earnings_refresh_subprocess",
        "run_python_module",
        "EARNINGS_REFRESH_PROCESS_TIMEOUT_SEC",
    }

    assert [marker for marker in process_protocol_markers if marker in source] == []
    assert "app.services.earnings_refresh_process_service" in imports


def test_lhb_tab_owns_background_work_through_lifecycle_group():
    path = REPO_ROOT / "ui/tabs/lhb_tab.py"
    source = path.read_text(encoding="utf-8")
    imports = _imports(path)

    assert "task_manager.run_in_background" not in source
    assert "app.services.ui_task_lifecycle_service" in imports
    assert "task_lifecycle_for" in source
    assert "task_lifecycle_for(self, runner=task_manager).shutdown" in source
