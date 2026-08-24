from __future__ import annotations

import json
import tomllib
from pathlib import Path

import conftest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pytest_config_registers_strict_ci_markers_and_project_deprecation_guard():
    options = _pyproject()["tool"]["pytest"]["ini_options"]
    markers = {item.split(":", 1)[0] for item in options["markers"]}

    assert options["testpaths"] == ["tests"]
    assert "--strict-markers" in options["addopts"]
    assert {"arch", "service", "runtime", "perf", "smoke", "windows", "slow"} <= markers
    assert options["filterwarnings"] == [
        "error::DeprecationWarning:(app|core|domains|infra|ui|vcp)(\\..*)?$"
    ]


def test_ruff_and_coverage_configuration_use_precise_incremental_guardrails():
    tool_config = _pyproject()["tool"]
    ruff = tool_config["ruff"]
    lint = ruff["lint"]
    coverage = tool_config["coverage"]

    assert lint["ignore"] == ["E501"]
    assert {"B006", "UP006", "SIM101"} <= set(lint["extend-select"])
    assert "artifacts" in ruff["exclude"]
    assert set(lint["per-file-ignores"]) == {
        "scripts/capture_ui_audit_screenshots.py",
        "scripts/kline_webengine_lifecycle_smoke.py",
        "scripts/perf_budget_check.py",
        "scripts/perf_memory_probe.py",
        "scripts/perf_round4_probe.py",
        "scripts/perf_round5_probe.py",
        "scripts/runtime_health_stability_suite.py",
        "scripts/soak_leak_probe.py",
        "ui/tabs/foreign_block_trade_tab.py",
        "ui/tabs/watchlist_tab.py",
    }
    assert all(value == ["E402"] for value in lint["per-file-ignores"].values())

    assert coverage["run"]["branch"] is True
    assert coverage["run"]["relative_files"] is True
    assert set(coverage["run"]["source"]) == {"app", "core", "domains", "infra", "ui", "vcp"}
    assert "pragma: no cover" in coverage["report"]["exclude_lines"]
    assert "if TYPE_CHECKING:" in coverage["report"]["exclude_lines"]


def test_pyright_configuration_starts_with_a_passing_ui_type_check_slice():
    config = json.loads((REPO_ROOT / "pyrightconfig.json").read_text(encoding="utf-8"))

    assert {
        "ui/kline_pool_state.py",
        "ui/kline_typing.py",
        "ui/kline_window_recovery.py",
    } <= set(config["include"])


def test_marker_routing_preserves_the_previous_ci_file_groups():
    expected_paths = {
        "arch": {
            "tests/test_architecture_boundaries.py",
            "tests/test_app_config.py",
            "tests/test_application_bootstrap.py",
            "tests/test_domain_entrypoints.py",
            "tests/test_event_bus_layers.py",
            "tests/test_log_tab.py",
            "tests/test_market_data_ports.py",
            "tests/test_main_window_f5_done.py",
            "tests/test_main_window_network.py",
            "tests/test_workspace_quote_codes.py",
            "tests/test_kline_open_service.py",
            "tests/test_main_window_shell.py",
            "tests/test_observability.py",
            "tests/test_service_toggle_registry.py",
            "tests/test_startup_orchestrator.py",
        },
        "service": {
            "tests/test_central_quote_polling_service.py",
            "tests/test_central_quotes_worker.py",
            "tests/test_earnings_scheduler_startup.py",
            "tests/test_earnings_tab_trade_window.py",
            "tests/test_provider_services.py",
            "tests/test_engine_services.py",
            "tests/test_background_job_runner.py",
            "tests/test_task_manager.py",
        },
        "runtime": {
            "tests/test_runtime_env.py",
            "tests/test_runtime_env_self_check.py",
            "tests/test_yf_session.py",
        },
        "perf": {
            "tests/test_perf_probe_scripts.py",
            "tests/test_perf_budget_check.py",
            "tests/test_runtime_health.py",
            "tests/test_tab_data_lineage_service.py",
            "tests/test_lhb_tab.py",
            "tests/test_fund_holdings_tab.py",
            "tests/test_market_data_warehouse_manifest.py",
            "tests/test_market_data_warehouse.py",
            "tests/test_kline_webengine_lifecycle_smoke.py",
            "tests/test_qt_webengine_preflight.py",
        },
        "windows": {
            "tests/test_windows_autostart.py",
            "tests/test_build_windows_script.py",
            "tests/test_single_instance.py",
        },
    }

    assert conftest._CI_MARKER_PATHS == {name: frozenset(paths) for name, paths in expected_paths.items()}
    for marker, paths in expected_paths.items():
        for path in paths:
            assert marker in conftest._ci_markers_for_path(REPO_ROOT / path)
