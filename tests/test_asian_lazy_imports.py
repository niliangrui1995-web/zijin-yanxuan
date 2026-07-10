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


def test_asian_runtime_shell_and_worker_modules_defer_yfinance_import():
    result = _run_isolated(
        "import json, sys; "
        "from ui.services.asian_market_runtime_service import AsianMarketRuntimeService; "
        "service = AsianMarketRuntimeService(); "
        "runtime_state = {"
        "'yfinance': any(name == 'yfinance' or name.startswith('yfinance.') for name in sys.modules), "
        "'workers': 'ui.tabs.asian_market_workers' in sys.modules, "
        "'worker_created': service.current_worker() is not None}; "
        "import ui.tabs.asian_market_workers as workers; "
        "worker_state = {"
        "'yfinance': any(name == 'yfinance' or name.startswith('yfinance.') for name in sys.modules), "
        "'lazy_module_empty': workers.yf._module is None}; "
        "import vcp.fetchers.yf_session; "
        "session_state = {"
        "'yfinance': any(name == 'yfinance' or name.startswith('yfinance.') for name in sys.modules)}; "
        "print(json.dumps({'runtime': runtime_state, 'worker': worker_state, 'session': session_state}))"
    )

    assert result == {
        "runtime": {"yfinance": False, "workers": False, "worker_created": False},
        "worker": {"yfinance": False, "lazy_module_empty": True},
        "session": {"yfinance": False},
    }
