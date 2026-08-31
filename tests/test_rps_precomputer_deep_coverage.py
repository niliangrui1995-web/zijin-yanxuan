from __future__ import annotations

import builtins
import math
import sys
import threading
from contextlib import nullcontext
from dataclasses import replace
from types import ModuleType, SimpleNamespace

import pytest

from app.services.f5_job_contract import F5JobRequest, F5JobStatus, F5Phase
from core import rps_precomputer as module
from core.f5_resource_guard import F5MemoryPressureError
from core.rps_precomputer import RPSPrecomputer


class _DateColumn:
    @staticmethod
    def max():
        return "20260101"


class _Frame:
    columns = ("datetime",)

    def __init__(self, rows=60):
        self.rows = rows

    def __len__(self):
        return self.rows

    def __getitem__(self, key):
        assert key == "datetime"
        return _DateColumn()


class _Provider:
    def __init__(self):
        self.cache_lock = threading.RLock()
        self.cache_data = {"old": _Frame(1)}
        self.code2name = {}
        self.tdx_vipdoc = "D:/HT/vipdoc"
        self.online_modes = []

    def ensure_adjustment_metadata(self, *, force=False):
        self.adjustment_force = force

    @staticmethod
    def load_cache_from_disk():
        return ""

    @staticmethod
    def _get_codes_from_vipdoc():
        return {"000001": "A", "600001": "B", "short": "C"}

    @staticmethod
    def is_online():
        return True

    def set_online_mode(self, value):
        self.online_modes.append(value)

    def sync_market_data(
        self,
        codes,
        *,
        force_refresh,
        max_workers,
        progress_callback,
        cancellation_checker,
        snapshot_writer,
        snapshot_date,
        load_cached_snapshot_if_empty=True,
    ):
        assert force_refresh is True
        assert max_workers == module.F5_LOCAL_REREAD_MAX_WORKERS
        assert load_cached_snapshot_if_empty is False
        assert cancellation_checker() is False
        progress_callback(1, len(codes), "")
        progress_callback(len(codes), len(codes), "done")
        self.cache_data = {"000001": _Frame(60), "600001": _Frame(80), "short": _Frame(2), "none": None}
        snapshot_writer(self.cache_data, snapshot_date)


class _Engine:
    def __init__(self):
        self.installed = None

    def build_rps_matrix(self, all_data, start, end):
        assert set(all_data) == {"000001", "600001"}
        assert start == end
        return {
            end: {
                "rps120": {"000001": 90, "600001": float("nan")},
                "rps250": {"000001": 80, "600001": float("nan")},
            }
        }

    def set_precomputed_rps(self, *args):
        self.installed = args


class _SnapshotStore:
    def __init__(self, output_dir):
        self.parquet_path = str(module.Path(output_dir) / "market.parquet")

    def stage_market_dataset(self, cache_data, _trade_date):
        parquet_path = module.Path(self.parquet_path)
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        parquet_path.write_bytes(b"unit-test-market-parquet")
        return SimpleNamespace(
            ok=True,
            error="",
            parquet_path=self.parquet_path,
            schema_version=1,
            symbol_count=len(cache_data),
            row_count=sum(len(frame) for frame in cache_data.values() if frame is not None),
        )


def _request(tmp_path) -> F5JobRequest:
    return F5JobRequest.build(
        project_root=str(tmp_path),
        data_dir=str(tmp_path / "data"),
        cache_dir=str(tmp_path / "cache"),
        tdx_vipdoc="D:/HT/vipdoc",
        requested_date="20260101",
    )


@pytest.fixture(autouse=True)
def _stub_snapshot_store(monkeypatch):
    monkeypatch.setattr(module, "F5MarketSnapshotStore", _SnapshotStore)
    monkeypatch.setattr(module, "ensure_f5_commit_headroom", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_latest_completed_cn_trade_date", lambda: "20260101")
    monkeypatch.setattr(
        module,
        "inspect_vipdoc_daily_source",
        lambda _path: SimpleNamespace(
            effective_trade_date="20260101",
            symbol_count=3,
            dated_symbol_count=3,
            unstable=False,
            signature="a" * 64,
            source_path="D:/HT/vipdoc",
            to_dict=lambda: {"effective_trade_date": "20260101"},
        ),
    )


def test_memory_usage_helper_handles_optional_psutil(monkeypatch):
    fake_psutil = ModuleType("psutil")
    fake_psutil.Process = lambda: SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=5 * 1024**2))
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    assert module._get_memory_usage_mb() == 5

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "psutil", raising=False)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert module._get_memory_usage_mb() == -1


def test_full_f5_job_success_builds_job_local_activation_bundle(monkeypatch, tmp_path):
    provider = _Provider()
    engine = _Engine()
    saved = []
    removed = []
    events = []
    sector_calls = []
    request = _request(tmp_path)

    monkeypatch.setattr(module, "ensure_cache_dir", lambda: None)
    monkeypatch.setattr(module, "system_log_backpressure", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(module, "_get_memory_usage_mb", lambda: 123)
    monkeypatch.setattr(module.gc, "collect", lambda: 0)
    real_save_json_file = module.save_json_file

    def _record_save(path, payload):
        saved.append((path, payload))
        real_save_json_file(path, payload)

    monkeypatch.setattr(module, "save_json_file", _record_save)
    monkeypatch.setattr(module, "remove_cache_file", removed.append)
    import vcp.sector as sector

    fake_sector = SimpleNamespace(
        build_sector_rps=lambda all_data, day: sector_calls.append((set(all_data), day)) or {"AI": {5: 99}}
    )
    monkeypatch.setattr(sector.SectorManager, "get_instance", lambda root: fake_sector)

    result = RPSPrecomputer.run_f5_job(
        request,
        data_provider=provider,
        engine=engine,
        cancelled_checker=lambda: False,
        event_callback=events.append,
    )

    assert result.status is F5JobStatus.READY_TO_ACTIVATE
    assert provider.adjustment_force is True
    assert provider.online_modes == [False, True]
    assert engine.installed is None
    rps_path = str(module.Path(request.snapshot_dir) / "rps.json")
    sector_path = str(module.Path(request.snapshot_dir) / "sector_rps.json")
    source_path = str(module.Path(request.job_dir) / "vipdoc_source.json")
    assert {path for path, _payload in saved} == {rps_path, sector_path, source_path}
    rps_payload = next(payload for path, payload in saved if path == rps_path)
    assert rps_payload["rps120"]["000001"] == 90
    assert math.isnan(rps_payload["rps120"]["600001"])
    assert sector_calls and sector_calls[0][0] == {"000001", "600001"}
    assert removed == [rps_path.replace(".json", ".pkl"), sector_path.replace(".json", ".pkl")]
    assert result.artifacts is not None
    assert result.artifacts.snapshot_id == request.run_id
    assert result.artifacts.effective_trade_date == "20260101"
    assert events[0].phase is F5Phase.PREPARE
    assert events[-1].phase is F5Phase.VALIDATE
    assert any(event.phase is F5Phase.MARKET_SYNC and event.completed == 3 for event in events)


def test_f5_job_stage1_failure_is_terminal_and_restores_online_mode(monkeypatch, tmp_path):
    provider = _Provider()
    provider.sync_market_data = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("sync failed"))
    events = []
    monkeypatch.setattr(module, "ensure_cache_dir", lambda: None)
    monkeypatch.setattr(module, "system_log_backpressure", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(module, "_get_memory_usage_mb", lambda: -1)

    result = RPSPrecomputer.run_f5_job(
        _request(tmp_path),
        data_provider=provider,
        engine=SimpleNamespace(),
        cancelled_checker=lambda: False,
        event_callback=events.append,
    )

    assert provider.online_modes == [False, True]
    assert result.status is F5JobStatus.FAILED
    assert result.artifacts is None
    assert "sync failed" in result.error_message
    assert events[-1].phase is F5Phase.MARKET_SYNC


def test_f5_rejects_stale_vipdoc_before_parsing_adjustments_or_market(monkeypatch, tmp_path):
    provider = _Provider()
    vipdoc = tmp_path / "vipdoc"
    vipdoc.mkdir()
    provider.market_data_warehouse = SimpleNamespace(
        current_status=lambda **_kwargs: SimpleNamespace(ok=True, trade_date="20260102")
    )
    request = replace(_request(tmp_path), tdx_vipdoc=str(vipdoc))
    monkeypatch.setattr(
        module,
        "inspect_vipdoc_daily_source",
        lambda _path: SimpleNamespace(
            effective_trade_date="20260101",
            symbol_count=3,
            dated_symbol_count=3,
            unstable=False,
            signature="a" * 64,
            source_path=str(vipdoc),
            to_dict=lambda: {"effective_trade_date": "20260101"},
        ),
    )

    result = RPSPrecomputer.run_f5_job(request, data_provider=provider, engine=_Engine())

    assert result.status is F5JobStatus.FAILED
    assert result.error_code == "vipdoc_source_stale"
    assert not hasattr(provider, "adjustment_force")
    assert provider.online_modes == []


def test_f5_rejects_vipdoc_behind_latest_completed_trade_date(monkeypatch, tmp_path):
    provider = _Provider()
    vipdoc = tmp_path / "vipdoc"
    vipdoc.mkdir()
    request = replace(_request(tmp_path), tdx_vipdoc=str(vipdoc))
    monkeypatch.setattr(module, "_latest_completed_cn_trade_date", lambda: "20260102", raising=False)

    result = RPSPrecomputer.run_f5_job(request, data_provider=provider, engine=_Engine())

    assert result.status is F5JobStatus.FAILED
    assert result.error_code == "vipdoc_source_stale"
    assert not hasattr(provider, "adjustment_force")
    assert provider.online_modes == []


def test_f5_rejects_snapshot_when_vipdoc_changes_while_full_read_is_running(monkeypatch, tmp_path):
    provider = _Provider()
    vipdoc = tmp_path / "vipdoc"
    vipdoc.mkdir()
    request = replace(_request(tmp_path), tdx_vipdoc=str(vipdoc))
    reports = iter(
        (
            SimpleNamespace(
                effective_trade_date="20260101",
                symbol_count=3,
                dated_symbol_count=3,
                unstable=False,
                signature="a" * 64,
                source_path=str(vipdoc),
                to_dict=lambda: {"signature": "a" * 64},
            ),
            SimpleNamespace(
                effective_trade_date="20260101",
                symbol_count=3,
                dated_symbol_count=3,
                unstable=False,
                signature="b" * 64,
                source_path=str(vipdoc),
                to_dict=lambda: {"signature": "b" * 64},
            ),
        )
    )
    monkeypatch.setattr(module, "inspect_vipdoc_daily_source", lambda _path: next(reports))
    engine = SimpleNamespace(build_rps_matrix=lambda *_args: (_ for _ in ()).throw(AssertionError("must not build RPS")))

    result = RPSPrecomputer.run_f5_job(request, data_provider=provider, engine=engine)

    assert result.status is F5JobStatus.FAILED
    assert result.error_code == "vipdoc_source_changed_during_f5"
    assert provider.online_modes == [False, True]


def test_f5_pipeline_terminalizes_memory_error_without_relying_on_worker_wrapper(monkeypatch, tmp_path):
    provider = _Provider()
    provider.ensure_adjustment_metadata = lambda **_kwargs: (_ for _ in ()).throw(MemoryError("allocation failed"))

    result = RPSPrecomputer.run_f5_job(_request(tmp_path), data_provider=provider, engine=_Engine())

    assert result.status is F5JobStatus.FAILED
    assert result.error_code == "worker_memory_exhausted"
    assert "MemoryError" in result.error_message


def test_f5_rechecks_commit_headroom_while_market_sync_is_running(monkeypatch, tmp_path):
    provider = _Provider()
    checks = []
    pressure = F5MemoryPressureError(
        stage="F5 本地日线全量重读",
        headroom_bytes=128 * 1024**2,
        minimum_bytes=3 * 1024**3,
    )

    def _guard(*_args, **_kwargs):
        checks.append(True)
        if len(checks) >= 4:
            raise pressure

    monkeypatch.setattr(module, "ensure_f5_commit_headroom", _guard)

    result = RPSPrecomputer.run_f5_job(_request(tmp_path), data_provider=provider, engine=_Engine())

    assert result.status is F5JobStatus.FAILED
    assert result.error_code == "insufficient_memory_headroom"
    assert len(checks) >= 4


def test_f5_rechecks_commit_headroom_before_writing_full_market_snapshot(monkeypatch, tmp_path):
    provider = _Provider()
    pressure = F5MemoryPressureError(
        stage="F5 市场快照写入",
        headroom_bytes=128 * 1024**2,
        minimum_bytes=3 * 1024**3,
    )

    def _guard(_minimum_bytes, *, stage):
        if stage == "F5 市场快照写入":
            raise pressure

    monkeypatch.setattr(module, "ensure_f5_commit_headroom", _guard)

    result = RPSPrecomputer.run_f5_job(_request(tmp_path), data_provider=provider, engine=_Engine())

    assert result.status is F5JobStatus.FAILED
    assert result.error_code == "insufficient_memory_headroom"


def test_f5_job_empty_rps_is_failed_not_ready_to_activate(monkeypatch, tmp_path):
    provider = _Provider()
    monkeypatch.setattr(module, "ensure_cache_dir", lambda: None)
    monkeypatch.setattr(module, "system_log_backpressure", lambda *_args, **_kwargs: nullcontext())
    engine = SimpleNamespace(build_rps_matrix=lambda *_args: {})

    result = RPSPrecomputer.run_f5_job(
        _request(tmp_path),
        data_provider=provider,
        engine=engine,
        cancelled_checker=lambda: False,
    )

    assert result.status is F5JobStatus.FAILED
    assert "empty" in result.error_message
    assert result.artifacts is None


def test_f5_job_rps_exception_is_returned_as_terminal_failure(monkeypatch, tmp_path):
    provider = _Provider()
    provider.load_cache_from_disk = lambda: "20260101"
    provider.cache_data = {f"{index:04d}": _Frame(60) for index in range(2001)}
    provider._get_codes_from_vipdoc = lambda: {"000001": "A"}
    engine = SimpleNamespace(build_rps_matrix=lambda *_args: (_ for _ in ()).throw(RuntimeError("matrix")))
    monkeypatch.setattr(module, "ensure_cache_dir", lambda: None)
    monkeypatch.setattr(module, "system_log_backpressure", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(module, "_get_memory_usage_mb", lambda: -1)
    monkeypatch.setattr(module.gc, "collect", lambda: 0)

    result = RPSPrecomputer.run_f5_job(
        _request(tmp_path),
        data_provider=provider,
        engine=engine,
        cancelled_checker=lambda: False,
    )

    assert result.status is F5JobStatus.FAILED
    assert result.error_code == "f5_pipeline_failed"
    assert result.error_message == "matrix"
