from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_real_paint_starts_only_lightweight_runtime_shells():
    project_root = Path(__file__).resolve().parents[1]
    source = r'''
import datetime
import json
import os
import sys
import tempfile
import time
import uuid

os.environ["QT_QPA_PLATFORM"] = "offscreen"
database_path = tempfile.mktemp(suffix=".db")
os.environ["VCP_HUNTER_DB_PATH"] = database_path
os.environ["VCP_HUNTER_LOG_DIR"] = tempfile.mkdtemp(prefix="post-paint-logs-")
os.environ["VCP_HUNTER_SETTINGS_ORGANIZATION"] = "VCPHunterDiagnostics"
os.environ["VCP_HUNTER_SETTINGS_APPLICATION"] = f"PostPaint_{uuid.uuid4().hex}"

from PyQt6.QtCore import QCoreApplication, Qt
from PyQt6.QtWidgets import QApplication

QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication.instance() or QApplication([])

import ui.main_window_qt as main_window_module
import app.services.earnings_refresh_process_service as earnings_process_module
from ui.services.auto_refresh_scheduler import MarketCalendar
from ui.services.auto_refresh_tasks import AutoRefreshTaskService

fixed_now = datetime.datetime(2026, 4, 20, 1, 0)
MarketCalendar.now = classmethod(lambda cls, market="CN": fixed_now)
MarketCalendar.today = classmethod(lambda cls, market="CN": fixed_now.date())
MarketCalendar.is_market_active = classmethod(lambda cls, market="CN", now=None: False)
MarketCalendar.is_trade_day = classmethod(lambda cls, day=None, market="CN": False)
MarketCalendar.get_latest_trade_date = classmethod(
    lambda cls, market="CN", ref_date=None: fixed_now.date()
)
MarketCalendar.get_recent_trade_dates = classmethod(
    lambda cls, n, ref_date=None, market="CN": []
)

def fake_earnings_refresh(mode, *, routine_time=""):
    job_key = "earnings_startup_gap_fill" if mode == "startup-gap-fill" else "earnings_routine"
    return {"status": "success", "job_key": job_key, "records": 0}

earnings_process_module.run_earnings_refresh = fake_earnings_refresh
AutoRefreshTaskService.prepare_asian_market_runtime = lambda self: {"target_codes": []}
AutoRefreshTaskService.sync_asian_market_runtime = (
    lambda self, prepared=None: {"job_key": "asian_market_runtime", "status": "skipped"}
)

class Provider:
    cache_data = {}
    code2name = {}

    def ensure_code_name_map(self, codes=None, *, refresh_missing=False):
        return {}

    def fetch_realtime_quotes_batch(self, codes):
        return {}

    def test_network(self, timeout=3):
        return False

    def set_online_mode(self, online):
        return None

class Orchestrator:
    def __init__(self):
        self.scheduled = 0

    def schedule_startup(self):
        self.scheduled += 1

    def shutdown(self):
        return None

orchestrator = Orchestrator()
main_window_module.create_data_provider = lambda *, offline=True: Provider()
main_window_module.create_scan_engine = lambda: object()
main_window_module.create_startup_orchestrator = lambda _window: orchestrator
main_window_module.apply_windows_frameless_taskbar_fix = lambda _window: None
main_window_module.enable_windows_native_shadow = lambda _window: None
main_window_module.enable_windows_system_backdrop = lambda *_args, **_kwargs: None

blocked_prefixes = (
    "akshare",
    "core.lhb_pool_manager",
    "domains.earnings.engine",
    "domains.fund_holdings.store",
    "numpy",
    "openpyxl",
    "pandas",
    "yfinance",
)

window = main_window_module.MainWindowQT(
    startup_enabled=True,
    auto_refresh_enabled=True,
    background_prewarm=False,
    kline_prewarm_enabled=False,
    central_quotes_enabled=False,
    restore_last_tab_enabled=False,
)
prepaint_modules = set(sys.modules)
prepaint_blocked = sorted(
    name
    for name in prepaint_modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in blocked_prefixes)
)

window.show()
deadline = time.perf_counter() + 2.0
while not window._post_paint_runtime_started and time.perf_counter() < deadline:
    app.processEvents()
    time.sleep(0.002)
for _ in range(8):
    app.processEvents()

postpaint_modules = set(sys.modules)
new_blocked = sorted(
    name
    for name in postpaint_modules - prepaint_modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in blocked_prefixes)
)
result = {
    "database_path": database_path,
    "first_paint": window._first_paint_recorded,
    "post_paint_started": window._post_paint_runtime_started,
    "startup_scheduled": orchestrator.scheduled,
    "scheduler_active": bool(window.auto_refresh_scheduler and window.auto_refresh_scheduler.timer.isActive()),
    "prepaint_blocked": prepaint_blocked,
    "new_blocked": new_blocked,
}
window.close()
app.processEvents()
print("RESULT=" + json.dumps(result, sort_keys=True), flush=True)
'''
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    result_line = next(
        (line for line in completed.stdout.splitlines() if line.startswith("RESULT=")),
        "",
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert result_line, completed.stdout
    result = json.loads(result_line.removeprefix("RESULT="))
    Path(result["database_path"]).unlink(missing_ok=True)

    assert result == {
        "database_path": result["database_path"],
        "first_paint": True,
        "post_paint_started": True,
        "startup_scheduled": 1,
        "scheduler_active": True,
        "prepaint_blocked": [],
        "new_blocked": [],
    }
