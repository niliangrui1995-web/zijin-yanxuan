import subprocess
import types

from PyQt6.QtCore import QObject
from PyQt6.QtTest import QSignalSpy

from core.event_bus import event_bus
from core.startup_orchestrator import (
    ASIAN_DATA_SYNC_TASK_ID,
    ASIAN_DATA_SYNC_TIMEOUT_SEC,
    DEFERRED_LOAD_TASK_ID,
    SMART_STARTUP_TASK_ID,
    StartupOrchestrator,
)


class _DummyLabel:
    def __init__(self):
        self.value = ""

    def setText(self, text):
        self.value = text


class _DummyCacheManager:
    def load_rt_cache(self, *_args, **_kwargs):
        return None

    def try_load_rps_from_disk(self, *_args, **_kwargs):
        return None


class _DummyDataProvider:
    def __init__(self):
        self.cache_data = {}

    def load_cache_from_disk(self):
        return ""

    def test_network(self, timeout=3):
        return False


class _DummyMainWindow(QObject):
    def __init__(self):
        super().__init__()
        self._is_closing = False
        self.data_provider = _DummyDataProvider()
        self.cache_manager = _DummyCacheManager()
        self.engine = object()
        self.table_rt = object()
        self.lbl_status = _DummyLabel()
        self.lbl_code_count = _DummyLabel()
        self.tab_watchlist = None

    def _call_in_ui(self, callback):
        callback()


class _InlineJobRunner:
    def __init__(self):
        self.abandoned = []

    def run(self, task_id, fn, *args, **kwargs):
        fn()
        return task_id

    def abandon(self, task_id):
        self.abandoned.append(task_id)
        return True


def test_startup_orchestrator_asian_sync_uses_subprocess_timeout(monkeypatch):
    runner = _InlineJobRunner()
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=runner)
    run_calls = []

    def fake_exists(path):
        if path.endswith("asian_klines_latest.json"):
            return False
        if path.endswith("asian_kline_fetcher.py"):
            return True
        return True

    def fake_run(*args, **kwargs):
        run_calls.append({"args": args, "kwargs": kwargs})
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr("core.startup_orchestrator.os.path.exists", fake_exists)
    monkeypatch.setattr("core.startup_orchestrator.subprocess.run", fake_run)

    orchestrator.deferred_data_load()

    assert run_calls, "expected asian sync subprocess to run"
    cmd = run_calls[0]["args"][0]
    assert cmd[1:5] == ["-m", "vcp.fetchers.asian_kline_fetcher", "--strict-sync", "--output-dir"]
    assert run_calls[0]["kwargs"]["timeout"] == ASIAN_DATA_SYNC_TIMEOUT_SEC
    assert run_calls[0]["kwargs"]["cwd"].endswith("紫金研选")
    assert run_calls[0]["kwargs"]["stdout"] == subprocess.PIPE
    assert run_calls[0]["kwargs"]["stderr"] == subprocess.PIPE
    assert run_calls[0]["kwargs"]["text"] is True


def test_startup_orchestrator_deferred_load_emits_cache_bootstrap_ready(monkeypatch):
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    spy = QSignalSpy(event_bus.sig_cache_bootstrap_ready)

    def fake_exists(path):
        return not path.endswith("asian_kline_fetcher.py")

    monkeypatch.setattr("core.startup_orchestrator.os.path.exists", fake_exists)

    orchestrator.deferred_data_load()

    assert len(spy) == 1


def test_startup_orchestrator_asian_sync_logs_succinct_failure_message(monkeypatch):
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    records = {"warning": [], "debug": []}

    class _FakeLog:
        def warning(self, message):
            records["warning"].append(message)

        def debug(self, message):
            records["debug"].append(message)

        def info(self, _message):
            return None

        def error(self, _message):
            return None

    def fake_exists(path):
        if path.endswith("asian_klines_latest.json"):
            return False
        if path.endswith("asian_kline_fetcher.py"):
            return True
        return True

    def fake_run(*_args, **_kwargs):
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=["python", "asian_kline_fetcher.py"],
            stderr="连接雅虎接口失败\nHTTP 429 Too Many Requests",
        )

    monkeypatch.setattr("core.startup_orchestrator.os.path.exists", fake_exists)
    monkeypatch.setattr("core.startup_orchestrator.subprocess.run", fake_run)
    monkeypatch.setattr("core.startup_orchestrator.log", _FakeLog())

    orchestrator.deferred_data_load()

    assert records["warning"] == [
        "[启动] 亚洲市场静默同步失败，已跳过本次更新（退出码 1：连接雅虎接口失败 | HTTP 429 Too Many Requests）"
    ]
    assert records["debug"] == [
        "[启动] 亚洲市场静默同步原始输出: 连接雅虎接口失败\nHTTP 429 Too Many Requests"
    ]


def test_startup_orchestrator_offline_network_log_is_visible_info(monkeypatch):
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=_InlineJobRunner())
    records = {"info": [], "debug": [], "error": []}

    class _FakeLog:
        def info(self, message):
            records["info"].append(message)

        def debug(self, message):
            records["debug"].append(message)

        def error(self, message):
            records["error"].append(message)

    monkeypatch.setattr("core.startup_orchestrator.log", _FakeLog())

    orchestrator.smart_startup()

    assert records["info"] == ["[智能启动] 网络不可用，保持离线模式"]
    assert records["error"] == []
    assert records["debug"] == []


def test_startup_orchestrator_shutdown_abandons_background_tasks():
    runner = _InlineJobRunner()
    orchestrator = StartupOrchestrator(_DummyMainWindow(), job_runner=runner)

    orchestrator.shutdown()

    assert runner.abandoned == [
        DEFERRED_LOAD_TASK_ID,
        ASIAN_DATA_SYNC_TASK_ID,
        SMART_STARTUP_TASK_ID,
    ]
