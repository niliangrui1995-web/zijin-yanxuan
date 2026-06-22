import datetime

from ui.services.auto_refresh_scheduler import AutoRefreshScheduler
from ui.services.rt_monitor_service import RtMonitorService


class _Provider:
    def __init__(self, cache_data=None):
        self.cache_data = cache_data or {}
        self.load_calls = 0
        self.online = True

    def load_cache_from_disk(self):
        self.load_calls += 1
        self.cache_data = {f"{idx:06d}": object() for idx in range(120)}
        return "20260618"

    def is_online(self):
        return self.online

    def test_network(self, timeout=5):
        return self.online

    def set_online_mode(self, online):
        self.online = bool(online)


class _Engine:
    def __init__(self, bundle=None):
        self.bundle = bundle

    def get_precomputed_rps(self):
        return self.bundle


def _cache(count=120):
    return {f"{idx:06d}": object() for idx in range(count)}


def _rps_bundle():
    return {"rps120": {}, "rps250": {}}


def test_rt_monitor_auto_start_waits_for_startup_cache_without_loading_disk(monkeypatch, qt_application):
    provider = _Provider(cache_data={})
    service = RtMonitorService(provider, _Engine(_rps_bundle()))
    started = []
    monkeypatch.setattr(service, "_start_worker", lambda: started.append(True))

    assert service.start(auto=True) is False

    assert provider.load_calls == 0
    assert started == []


def test_rt_monitor_auto_start_waits_for_precomputed_rps(monkeypatch, qt_application):
    provider = _Provider(cache_data=_cache())
    service = RtMonitorService(provider, _Engine(None))
    started = []
    monkeypatch.setattr(service, "_start_worker", lambda: started.append(True))

    assert service.start(auto=True) is False

    assert provider.load_calls == 0
    assert started == []


def test_rt_monitor_auto_start_runs_after_cache_and_rps_are_ready(monkeypatch, qt_application):
    provider = _Provider(cache_data=_cache())
    service = RtMonitorService(provider, _Engine(_rps_bundle()))
    started = []
    monkeypatch.setattr(service, "_start_worker", lambda: started.append(True))

    assert service.start(auto=True) is True

    assert provider.load_calls == 0
    assert started == [True]


def test_auto_refresh_scheduler_start_does_not_boot_rt_monitor_before_prewarm(monkeypatch, qt_application):
    provider = _Provider(cache_data={})
    service = RtMonitorService(provider, _Engine(None))
    started = []
    monkeypatch.setattr(service, "_start_worker", lambda: started.append(True))
    monkeypatch.setattr(
        "ui.services.rt_monitor_service.MarketCalendar.is_market_active",
        classmethod(lambda cls, market="CN": True),
    )
    scheduler = AutoRefreshScheduler(
        rt_monitor_service=service,
        task_service=object(),
        clock=lambda: datetime.datetime(2026, 6, 22, 14, 30),
    )
    scheduler.DAILY_JOBS = ()
    scheduler.extended_jobs_enabled = False

    scheduler.start()
    scheduler.stop()

    assert provider.load_calls == 0
    assert started == []
