import datetime

from PyQt6.QtTest import QSignalSpy

from app.services.ui_config_service import app_config
from app.services.ui_event_service import domain_events as event_bus
from ui.services import auto_refresh_tasks as auto_refresh_task_module
from ui.services.auto_refresh_scheduler import AutoRefreshJob, AutoRefreshScheduler
from ui.services.auto_refresh_tasks import AutoRefreshTaskService


class _ImmediateRunner:
    def __init__(self):
        self.jobs = []

    def is_active_task(self, _task_id):
        return False

    def run_in_background(self, fn, *, on_success=None, on_error=None, task_id=None):
        self.jobs.append(task_id)
        try:
            result = fn()
        except Exception as exc:
            if on_error:
                on_error(str(exc))
        else:
            if on_success:
                on_success(result)
        return task_id


class _QueuedRunner:
    def __init__(self):
        self.jobs = []
        self.active = set()

    def is_active_task(self, task_id):
        return task_id in self.active

    def run_in_background(self, fn, *, on_success=None, on_error=None, task_id=None):
        self.jobs.append((task_id, fn, on_success, on_error))
        self.active.add(task_id)
        return task_id


class _TaskService:
    def __init__(self):
        self.calls = []
        self.fail = False

    def run_lhb_daily(self, trade_date):
        self.calls.append(("lhb_daily", trade_date))
        if self.fail:
            raise RuntimeError("lhb failed")
        return {"records": 3}

    def run_foreign_block_daily(self, trade_date):
        self.calls.append(("foreign_block_daily", trade_date))
        return {"records": 2}

    def run_fund_holdings_daily(self, trade_date):
        self.calls.append(("fund_holdings_daily", trade_date))
        return {"message": "ok"}

    def run_na_daily_full_0925(self, trade_date):
        self.calls.append(("na_daily_full_0925", trade_date))
        return {"records": 4}

    def run_na_daily_incremental(self, trade_date):
        self.calls.append(("na_daily_incremental", trade_date))
        return {"records": 4, "status": "success"}

    def sync_asian_market_runtime(self):
        self.calls.append(("asian_market_runtime", "sync"))
        return {"status": "started"}

    def run_asian_market_cache_sync(self, trade_date):
        self.calls.append(("asian_market_cache_sync", trade_date))
        return {"records": 0, "status": "skipped", "message": "cache fresh"}

    def run_earnings_startup_gap_fill(self, trade_date):
        self.calls.append(("earnings_startup_gap_fill", trade_date))
        return {"records": 1}

    def run_earnings_routine(self, trade_date, *, routine_time):
        self.calls.append(("earnings_routine", trade_date, routine_time))
        return {"records": 1}


def _reset_scheduler_settings():
    app_config.remove("auto_refresh_scheduler")
    app_config.sync()


def _scheduler(now, *, runner=None, task_service=None, extended_jobs=False):
    scheduler = AutoRefreshScheduler(
        task_service=task_service or _TaskService(),
        job_runner=runner or _ImmediateRunner(),
        clock=lambda: now[0],
    )
    scheduler.extended_jobs_enabled = bool(extended_jobs)
    return scheduler


def test_auto_refresh_scheduler_skips_daily_jobs_before_trigger(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 19, 59)]
    tasks = _TaskService()
    scheduler = _scheduler(now, task_service=tasks)
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()

    assert tasks.calls == []


def test_auto_refresh_scheduler_triggers_2000_trade_day_jobs_and_dedupes(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 20, 0)]
    tasks = _TaskService()
    lhb_spy = QSignalSpy(event_bus.sig_lhb_pool_updated)
    block_spy = QSignalSpy(event_bus.sig_block_trade_updated)
    status_spy = QSignalSpy(event_bus.sig_auto_refresh_status_changed)
    scheduler = _scheduler(now, task_service=tasks)
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()
    scheduler.tick()

    assert tasks.calls == [("lhb_daily", "20260420"), ("foreign_block_daily", "20260420")]
    assert len(lhb_spy) == 1
    assert len(block_spy) == 1
    assert any(args[0]["status"] == "success" for args in status_spy)


def test_auto_refresh_scheduler_triggers_fund_holdings_after_2030(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 20, 30)]
    tasks = _TaskService()
    fund_spy = QSignalSpy(event_bus.sig_fund_holdings_updated)
    scheduler = _scheduler(now, task_service=tasks)
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()

    assert ("fund_holdings_daily", "20260420") in tasks.calls
    assert len(fund_spy) == 1


def test_auto_refresh_scheduler_skips_trade_day_gated_jobs_on_non_trade_day(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 19, 20, 30)]
    tasks = _TaskService()
    scheduler = _scheduler(now, task_service=tasks)
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": False),
    )

    scheduler.tick()

    assert tasks.calls == [("fund_holdings_daily", "20260419")]


def test_auto_refresh_scheduler_does_not_resubmit_running_job(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 20, 10)]
    runner = _QueuedRunner()
    tasks = _TaskService()
    scheduler = _scheduler(now, runner=runner, task_service=tasks)
    scheduler.DAILY_JOBS = (AutoRefreshJob("lhb_daily", 20, 0, True),)
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()
    scheduler.tick()

    lhb_jobs = [job for job in runner.jobs if job[0] == "auto_refresh_lhb_daily"]
    assert len(lhb_jobs) == 1


def test_auto_refresh_scheduler_catches_up_when_started_after_2000(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 20, 10)]
    tasks = _TaskService()
    scheduler = _scheduler(now, task_service=tasks)
    scheduler.DAILY_JOBS = (AutoRefreshJob("lhb_daily", 20, 0, True),)
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()

    assert tasks.calls == [("lhb_daily", "20260420")]


def test_auto_refresh_scheduler_failed_job_uses_retry_backoff(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 20, 10)]
    tasks = _TaskService()
    tasks.fail = True
    status_spy = QSignalSpy(event_bus.sig_auto_refresh_status_changed)
    scheduler = _scheduler(now, task_service=tasks)
    scheduler.DAILY_JOBS = (AutoRefreshJob("lhb_daily", 20, 0, True),)
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()
    scheduler.tick()
    now[0] = datetime.datetime(2026, 4, 20, 20, 16)
    scheduler.tick()

    assert tasks.calls == [
        ("lhb_daily", "20260420"),
        ("lhb_daily", "20260420"),
    ]
    assert any(args[0]["status"] == "failed" and args[0]["error"] == "lhb failed" for args in status_spy)


def test_auto_refresh_scheduler_uses_30_second_global_timer():
    assert AutoRefreshScheduler.CHECK_INTERVAL_MS == 30_000


def test_auto_refresh_scheduler_triggers_na_daily_full_after_0925(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 9, 24)]
    tasks = _TaskService()
    scheduler = _scheduler(now, task_service=tasks, extended_jobs=True)
    scheduler.DAILY_JOBS = ()
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_market_active",
        classmethod(lambda cls, market="CN": False),
    )

    scheduler.tick()
    now[0] = datetime.datetime(2026, 4, 20, 9, 25)
    scheduler.tick()
    scheduler.tick()

    assert ("na_daily_full_0925", "20260420") in tasks.calls
    assert [call[0] for call in tasks.calls].count("na_daily_full_0925") == 1


def test_auto_refresh_scheduler_runs_na_incremental_only_when_market_active(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 10, 0)]
    tasks = _TaskService()
    scheduler = _scheduler(now, task_service=tasks, extended_jobs=True)
    scheduler.DAILY_JOBS = ()
    market_active = [False]
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_market_active",
        classmethod(lambda cls, market="CN": market_active[0]),
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()
    market_active[0] = True
    scheduler.tick()

    assert ("na_daily_incremental", "20260420") in tasks.calls


def test_auto_refresh_scheduler_triggers_asian_cache_sync_after_close(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 16, 29)]
    tasks = _TaskService()
    scheduler = _scheduler(now, task_service=tasks, extended_jobs=True)
    scheduler.DAILY_JOBS = ()
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_market_active",
        classmethod(lambda cls, market="CN": False),
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()
    now[0] = datetime.datetime(2026, 4, 20, 16, 30)
    scheduler.tick()
    scheduler.tick()

    assert ("asian_market_cache_sync", "20260420") in tasks.calls
    assert [call[0] for call in tasks.calls].count("asian_market_cache_sync") == 1


def test_auto_refresh_scheduler_runs_latest_earnings_routine_once(monkeypatch):
    _reset_scheduler_settings()
    now = [datetime.datetime(2026, 4, 20, 8, 29)]
    tasks = _TaskService()
    scheduler = _scheduler(now, task_service=tasks, extended_jobs=True)
    scheduler.DAILY_JOBS = ()
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_market_active",
        classmethod(lambda cls, market="CN": False),
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_scheduler.MarketCalendar.is_trade_day",
        classmethod(lambda cls, day, market="CN": True),
    )

    scheduler.tick()
    now[0] = datetime.datetime(2026, 4, 20, 8, 30)
    scheduler.tick()
    scheduler.tick()

    assert ("earnings_routine", "20260420", "08:30") in tasks.calls
    assert [call[0] for call in tasks.calls].count("earnings_routine") == 1


def test_auto_refresh_task_service_runs_fund_holdings_sync(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "ui.services.auto_refresh_tasks.fund_holdings_sync_service.sync_latest_all",
        lambda: calls.append("sync") or {"message": "done"},
    )

    result = AutoRefreshTaskService().run_fund_holdings_daily("20260420")

    assert calls == ["sync"]
    assert result["trade_date"] == "20260420"
    assert result["message"] == "done"


def test_auto_refresh_task_service_writes_lhb_pool_cache(monkeypatch):
    calls = []

    class FakePoolManager:
        last_auto_fetch_date = ""

        def add_day(self, date_text, records):
            calls.append(("add_day", date_text, records))

        def prune(self, trade_dates):
            calls.append(("prune", tuple(trade_dates)))

        def save(self):
            calls.append(("save",))

        def get_cached_dates(self):
            return ["20260420"]

    monkeypatch.setattr(
        "ui.workers.lhb_worker.fetch_lhb_pool_for_date",
        lambda date_text, emit_success_log=False, return_meta=True: {
            "records": [{"code": "300308"}, {"code": "600000"}],
            "status": "ok",
        },
    )
    monkeypatch.setattr(
        auto_refresh_task_module,
        "filter_rows_to_ai_chain_codes",
        lambda rows, **kwargs: [row for row in rows if row.get("code") == "300308"],
    )
    monkeypatch.setattr("ui.services.auto_refresh_tasks.LhbPoolManager", FakePoolManager)
    monkeypatch.setattr(
        "ui.services.auto_refresh_tasks.MarketCalendar.get_recent_trade_dates",
        classmethod(lambda cls, n, ref_date=None: ["20260420"]),
    )

    result = AutoRefreshTaskService().run_lhb_daily("20260420")

    assert calls == [
        ("add_day", "20260420", [{"code": "300308"}]),
        ("prune", ("20260420",)),
        ("save",),
    ]
    assert result["records"] == 1
    assert result["cached_trade_days"] == 1


def test_auto_refresh_task_service_writes_foreign_block_cache(monkeypatch):
    saved = []
    monkeypatch.setattr(
        "ui.services.auto_refresh_tasks._fetch_foreign_block_records",
        lambda *, days_to_fetch: {"records": [{"raw": 1}], "timeout_chunks": [], "failed_chunks": []},
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_tasks._build_foreign_block_rows",
        lambda records: [{"代码": "300750", "交易日期": "20260420"}],
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_tasks._latest_foreign_block_trade_date",
        lambda rows: "20260420",
    )
    monkeypatch.setattr(
        "ui.services.auto_refresh_tasks._save_foreign_block_cache",
        lambda rows, *, days_to_fetch, latest_trade_date: saved.append((rows, days_to_fetch, latest_trade_date)),
    )

    result = AutoRefreshTaskService().run_foreign_block_daily("20260420")

    assert saved == [([{"代码": "300750", "交易日期": "20260420"}], 30, "20260420")]
    assert result["records"] == 1
    assert result["latest_trade_date"] == "20260420"


def test_auto_refresh_foreign_block_rows_filter_to_ai_chain_pool(monkeypatch):
    monkeypatch.setattr(
        auto_refresh_task_module,
        "filter_rows_to_ai_chain_codes",
        lambda rows, **kwargs: [row for row in rows if row.get("代码") == "300308"],
    )

    rows = auto_refresh_task_module._build_foreign_block_rows(
        [
            {
                "交易日期": "20260420",
                "证券代码": "300308",
                "证券简称": "中际旭创",
                "买方营业部": "高盛上海营业部",
                "卖方营业部": "普通营业部",
                "收盘价": 120,
                "成交价格": 118,
                "折溢率": -0.02,
                "成交量": 10000,
                "成交金额": 1180000,
            },
            {
                "交易日期": "20260420",
                "证券代码": "600000",
                "证券简称": "浦发银行",
                "买方营业部": "高盛上海营业部",
                "卖方营业部": "普通营业部",
                "收盘价": 10,
                "成交价格": 9.8,
                "折溢率": -0.02,
                "成交量": 10000,
                "成交金额": 98000,
            },
        ]
    )

    assert [row["代码"] for row in rows] == ["300308"]
