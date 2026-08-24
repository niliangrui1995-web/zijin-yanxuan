# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import subprocess
import sys


def _run_isolated(source: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-c", source],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_app_services_package_import_is_lazy():
    result = _run_isolated(
        "import json, sys; import app.services as package; "
        "print(json.dumps({'heavy': [name for name in "
        "('pandas', 'polars', 'yfinance', 'app.services.asian_market_service') if name in sys.modules], "
        "'has_version': isinstance(package.APP_VERSION, str)}))"
    )

    assert result == {"heavy": [], "has_version": True}


def test_runtime_event_and_global_store_entrypoints_defer_qt_and_sqlite():
    result = _run_isolated(
        "import importlib, json, os, sys, tempfile; from pathlib import Path; "
        "tempdir = tempfile.TemporaryDirectory(); db_path = Path(tempdir.name) / 'must_not_exist.db'; "
        "os.environ['VCP_HUNTER_DB_PATH'] = str(db_path); "
        "events = importlib.import_module('domains.runtime.domain_events'); "
        "legacy_events = importlib.import_module('core.domain_events'); "
        "store = importlib.import_module('core.global_store'); "
        "events.domain_events.test_override = 'event'; store.global_store.test_override = 'store'; "
        "print(json.dumps({'pyqt': any(name == 'PyQt6' or name.startswith('PyQt6.') for name in sys.modules), "
        "'data_store': 'infra.storage.data_store' in sys.modules, 'database_created': db_path.exists(), "
        "'events_created': events._domain_events is not None, 'store_created': store._global_store is not None, "
        "'qt_event_module': 'domains.runtime.qt_domain_events' in sys.modules, "
        "'qt_store_module': 'core.qt_global_store' in sys.modules, "
        "'legacy_module_is_target': legacy_events is events, "
        "'legacy_proxy_matches': legacy_events.domain_events is events.domain_events, "
        "'overrides': [events.domain_events.test_override, store.global_store.test_override]}))"
    )

    assert result == {
        "pyqt": False,
        "data_store": False,
        "database_created": False,
        "events_created": False,
        "store_created": False,
        "qt_event_module": False,
        "qt_store_module": False,
        "legacy_module_is_target": False,
        "legacy_proxy_matches": True,
        "overrides": ["event", "store"],
    }


def test_market_data_package_import_loads_only_requested_port_module():
    result = _run_isolated(
        "import json, sys; import infra.market_data as package; port = package.RealtimeQuotePort; "
        "print(json.dumps({'heavy': [name for name in "
        "('pandas', 'polars', 'yfinance', 'infra.market_data.market_data_warehouse', "
        "'infra.market_data.tdx_data_provider') if name in sys.modules], "
        "'port_module': port.__module__}))"
    )

    assert result == {"heavy": [], "port_module": "infra.market_data.provider_ports"}


def test_earnings_package_import_defers_engine_and_scheduler_stacks():
    result = _run_isolated(
        "import json, sys; import domains.earnings as package; "
        "print(json.dumps({'heavy': [name for name in "
        "('akshare', 'pandas', 'openpyxl', 'domains.earnings.engine', "
        "'domains.earnings.scheduler') if name in sys.modules], "
        "'exports': sorted(package.__all__)}))"
    )

    assert result == {
        "heavy": [],
        "exports": ["EarningsEngine"],
    }


def test_earnings_service_shell_defers_engine_and_dataframe_stack():
    result = _run_isolated(
        "import json, sys; "
        "from app.services.ui_earnings_service import EarningsRefreshService; "
        "service = EarningsRefreshService(); "
        "print(json.dumps({'heavy': [name for name in "
        "('akshare', 'pandas', 'openpyxl', 'domains.earnings.engine', "
        "'domains.earnings.scheduler') if name in sys.modules], "
        "'engine_created': service._engine is not None}))"
    )

    assert result == {"heavy": [], "engine_created": False}


def test_earnings_engine_import_construct_and_row_probe_stay_lightweight():
    result = _run_isolated(
        "import json, sys, tempfile, types; "
        "store = types.SimpleNamespace(load_earnings_state=lambda: {}, "
        "fetch_one=lambda *args, **kwargs: {}); "
        "module = types.ModuleType('infra.storage.data_store'); module.data_store = store; "
        "sys.modules['infra.storage.data_store'] = module; "
        "from domains.earnings.engine import EarningsEngine; "
        "engine = EarningsEngine(cache_file=tempfile.mkdtemp() + '/earnings.json'); "
        "rows = engine.get_cached_record_rows(); "
        "print(json.dumps({'heavy': [name for name in "
        "('akshare', 'pandas', 'numpy', 'openpyxl') if name in sys.modules], "
        "'rows': rows, 'engine': type(engine).__name__}))"
    )

    assert result == {"heavy": [], "rows": [], "engine": "EarningsEngine"}


def test_earnings_metrics_import_defers_numeric_stack():
    result = _run_isolated(
        "import json, sys; import domains.earnings.metrics as metrics; "
        "print(json.dumps({'heavy': [name for name in ('pandas', 'numpy') if name in sys.modules], "
        "'module': metrics.__name__}))"
    )

    assert result == {"heavy": [], "module": "domains.earnings.metrics"}


def test_legacy_earnings_engine_alias_does_not_pull_scheduler_or_dataframe_stack():
    result = _run_isolated(
        "import json, sys; import earnings.engine as engine; "
        "print(json.dumps({'heavy': [name for name in "
        "('akshare', 'pandas', 'numpy', 'openpyxl', 'domains.earnings.scheduler') "
        "if name in sys.modules], 'module': engine.__name__}))"
    )

    assert result == {"heavy": [], "module": "earnings.engine"}


def test_main_window_module_import_defers_market_and_earnings_stacks():
    result = _run_isolated(
        "import json, os, sys; os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen'); "
        "from PyQt6.QtCore import QCoreApplication, Qt; "
        "from PyQt6.QtWidgets import QApplication; "
        "QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts); "
        "app = QApplication.instance() or QApplication([]); "
        "from ui.main_window_qt import MainWindowQT; "
        "print(json.dumps({'heavy': [name for name in "
        "('akshare', 'pandas', 'polars', 'pyarrow', 'yfinance', "
        "'app.services.asian_market_service', 'domains.earnings.engine', "
        "'infra.market_data.tdx_data_provider') if name in sys.modules], "
        "'window_class': MainWindowQT.__name__}))"
    )

    assert result == {"heavy": [], "window_class": "MainWindowQT"}
