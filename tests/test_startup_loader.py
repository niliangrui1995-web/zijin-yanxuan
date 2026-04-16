import subprocess
import types

from PyQt6.QtCore import QObject

from ui.startup_loader import ASIAN_DATA_SYNC_TIMEOUT_SEC, StartupLoader


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


def test_startup_loader_asian_sync_uses_subprocess_timeout(monkeypatch):
    loader = StartupLoader(_DummyMainWindow())
    run_calls = []

    def run_now(fn, *args, **kwargs):
        fn()
        return kwargs.get("task_id", "")

    def fake_exists(path):
        if path.endswith("asian_klines_latest.json"):
            return False
        if path.endswith("asian_kline_fetcher.py"):
            return True
        return True

    def fake_run(*args, **kwargs):
        run_calls.append({"args": args, "kwargs": kwargs})
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr("ui.startup_loader.task_manager.run_in_background", run_now)
    monkeypatch.setattr("ui.startup_loader.os.path.exists", fake_exists)
    monkeypatch.setattr("ui.startup_loader.subprocess.run", fake_run)

    loader.deferred_data_load()

    assert run_calls, "expected asian sync subprocess to run"
    cmd = run_calls[0]["args"][0]
    assert cmd[1:5] == ["-m", "vcp.fetchers.asian_kline_fetcher", "--strict-sync", "--output-dir"]
    assert run_calls[0]["kwargs"]["timeout"] == ASIAN_DATA_SYNC_TIMEOUT_SEC
    assert run_calls[0]["kwargs"]["cwd"].endswith("紫金研选")
    assert run_calls[0]["kwargs"]["stdout"] == subprocess.PIPE
    assert run_calls[0]["kwargs"]["stderr"] == subprocess.PIPE
    assert run_calls[0]["kwargs"]["text"] is True


def test_startup_loader_asian_sync_logs_succinct_failure_message(monkeypatch):
    loader = StartupLoader(_DummyMainWindow())
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

    def run_now(fn, *args, **kwargs):
        fn()
        return kwargs.get("task_id", "")

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

    monkeypatch.setattr("ui.startup_loader.task_manager.run_in_background", run_now)
    monkeypatch.setattr("ui.startup_loader.os.path.exists", fake_exists)
    monkeypatch.setattr("ui.startup_loader.subprocess.run", fake_run)
    monkeypatch.setattr("ui.startup_loader.log", _FakeLog())

    loader.deferred_data_load()

    assert records["warning"] == [
        "[启动] 亚洲市场静默同步失败，已跳过本次更新（退出码 1：连接雅虎接口失败 | HTTP 429 Too Many Requests）"
    ]
    assert records["debug"] == [
        "[启动] 亚洲市场静默同步原始输出: 连接雅虎接口失败\nHTTP 429 Too Many Requests"
    ]


def test_startup_loader_offline_network_log_is_visible_info(monkeypatch):
    loader = StartupLoader(_DummyMainWindow())
    records = {"info": [], "debug": [], "error": []}

    class _FakeLog:
        def info(self, message):
            records["info"].append(message)

        def debug(self, message):
            records["debug"].append(message)

        def error(self, message):
            records["error"].append(message)

    def run_now(fn, *args, **kwargs):
        fn()
        return kwargs.get("task_id", "")

    monkeypatch.setattr("ui.startup_loader.task_manager.run_in_background", run_now)
    monkeypatch.setattr("ui.startup_loader.log", _FakeLog())

    loader.smart_startup()

    assert records["info"] == ["[智能启动] 网络不可用，保持离线模式"]
    assert records["error"] == []
    assert records["debug"] == []


def test_startup_loader_shutdown_abandons_background_tasks(monkeypatch):
    loader = StartupLoader(_DummyMainWindow())
    abandoned = []

    monkeypatch.setattr(
        "ui.startup_loader.task_manager.abandon_task",
        lambda task_id: abandoned.append(task_id) or True,
    )

    loader.shutdown()

    assert abandoned == ["deferred_load", "asian_data_sync_bg", "smart_startup"]
