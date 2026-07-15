from __future__ import annotations

import builtins
import datetime
import math
import sys
import threading
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace

import pytest

from core import rps_precomputer as module
from core.rps_precomputer import RPSPrecomputer


class _Frame:
    def __init__(self, rows=60):
        self.rows = rows

    def __len__(self):
        return self.rows


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

    def sync_market_data(self, codes, *, force_refresh, max_workers, progress_callback):
        assert force_refresh is True
        assert max_workers == module.F5_LOCAL_REREAD_MAX_WORKERS
        progress_callback(1, len(codes), "")
        progress_callback(len(codes), len(codes), "done")
        self.cache_data = {"000001": _Frame(60), "600001": _Frame(80), "short": _Frame(2), "none": None}


class _Engine:
    def __init__(self):
        self.installed = None

    def build_rps_matrix(self, all_data, start, end):
        assert set(all_data) == {"000001", "600001"}
        assert start == end
        return {end: {"rps120": {"000001": 90, "600001": float("nan")}, "rps250": {"000001": 80}}}

    def set_precomputed_rps(self, *args):
        self.installed = args


def test_rps_helper_edges(monkeypatch):
    assert module._should_emit_ui_status("") is False
    assert module._should_emit_ui_status("===") is False
    assert module._should_emit_ui_status("=x=") is True
    assert module._provider_saved_stage1_cache(
        SimpleNamespace(_last_market_data_parquet_saved_date="20260101"), "20260101"
    )
    assert not module._provider_saved_stage1_cache(SimpleNamespace(), "20260101")

    messages = []
    module._emit_status(None, "visible")
    module._emit_status(messages.append, "===")
    module._emit_status(messages.append, "visible")
    assert messages == ["visible"]
    module._handle_stage1_progress(0, 0, "", messages.append)
    module._handle_stage1_progress(1, 3, "", messages.append, {})
    module._handle_stage1_progress(3, 3, "", messages.append)
    assert any("3/3" in message for message in messages)

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


@pytest.mark.parametrize("outcome", [True, False, RuntimeError("bad")])
def test_save_stage1_checkpoint_outcomes(monkeypatch, outcome):
    fake = ModuleType("vcp.polars_engine")
    if isinstance(outcome, Exception):
        fake.save_cache_parquet = lambda *_args: (_ for _ in ()).throw(outcome)
    else:
        fake.save_cache_parquet = lambda *_args: outcome
    monkeypatch.setitem(sys.modules, "vcp.polars_engine", fake)
    module._save_stage1_checkpoint({"a": 1}, "20260101")


def test_full_f5_pipeline_success(monkeypatch):
    provider = _Provider()
    engine = _Engine()
    saved = []
    removed = []
    cleaned = []
    statuses = []
    done = []
    sector_calls = []

    monkeypatch.setattr(module, "ensure_cache_dir", lambda: None)
    monkeypatch.setattr(module, "system_log_backpressure", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(module, "_get_memory_usage_mb", lambda: 123)
    monkeypatch.setattr(module.gc, "collect", lambda: 0)
    monkeypatch.setattr(module, "save_json_file", lambda path, payload: saved.append((path, payload)))
    monkeypatch.setattr(module, "remove_cache_file", removed.append)
    monkeypatch.setattr(module, "_save_stage1_checkpoint", lambda data, day: saved.append(("stage1", day, data)))
    import core.cache_policy as cache_policy
    import vcp.sector as sector

    monkeypatch.setattr(cache_policy, "cleanup_stale_caches", cleaned.append)
    fake_sector = SimpleNamespace(
        build_sector_rps=lambda all_data, day: sector_calls.append((set(all_data), day)) or {"AI": {5: 99}}
    )
    monkeypatch.setattr(sector.SectorManager, "get_instance", lambda root: fake_sector)

    RPSPrecomputer.run_f5_pipeline(
        provider,
        engine,
        cancelled_checker=lambda: False,
        set_status_callback=statuses.append,
        done_callback=lambda *args: done.append(args),
    )

    assert provider.adjustment_force is True
    assert provider.online_modes == [False, True]
    assert engine.installed and engine.installed[1]["000001"] == 90
    assert math.isnan(engine.installed[1]["600001"])
    assert any(path == module.RPS_CACHE_FILE for path, *_rest in saved)
    assert any(path == module.SECTOR_RPS_CACHE_FILE for path, *_rest in saved)
    assert sector_calls and sector_calls[0][0] == {"000001", "600001"}
    assert cleaned == [module.PROJECT_ROOT]
    assert done and done[0][0] == 4
    assert any("RPS" in message for message in statuses)


def test_f5_pipeline_stage1_failure_still_cleans_and_finishes(monkeypatch):
    provider = _Provider()
    provider.sync_market_data = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("sync failed"))
    done = []
    cleaned = []
    monkeypatch.setattr(module, "ensure_cache_dir", lambda: None)
    monkeypatch.setattr(module, "system_log_backpressure", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(module, "_get_memory_usage_mb", lambda: -1)
    monkeypatch.setattr(module, "_save_stage1_checkpoint", lambda *_args: None)
    import core.cache_policy as cache_policy

    monkeypatch.setattr(cache_policy, "cleanup_stale_caches", cleaned.append)
    RPSPrecomputer.run_f5_pipeline(
        provider,
        SimpleNamespace(),
        cancelled_checker=lambda: False,
        set_status_callback=None,
        done_callback=lambda *args: done.append(args),
    )
    assert provider.online_modes == [False, True]
    assert cleaned == [module.PROJECT_ROOT]
    assert done


def test_f5_pipeline_handles_stage2_sector_and_cleanup_failures(monkeypatch):
    today = datetime.date.today().strftime("%Y%m%d")
    provider = _Provider()
    provider.load_cache_from_disk = lambda: today
    provider.cache_data = {f"{index:04d}": _Frame(60) for index in range(2001)}
    provider._get_codes_from_vipdoc = lambda: {"000001": "A"}
    engine = SimpleNamespace(build_rps_matrix=lambda *_args: (_ for _ in ()).throw(RuntimeError("matrix")))
    monkeypatch.setattr(module, "ensure_cache_dir", lambda: None)
    monkeypatch.setattr(module, "system_log_backpressure", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(module, "_get_memory_usage_mb", lambda: -1)
    monkeypatch.setattr(module.gc, "collect", lambda: 0)
    monkeypatch.setattr(module, "save_json_file", lambda *_args: (_ for _ in ()).throw(OSError("sector save")))
    import core.cache_policy as cache_policy

    monkeypatch.setattr(cache_policy, "cleanup_stale_caches", lambda _root: (_ for _ in ()).throw(OSError("cleanup")))
    RPSPrecomputer.run_f5_pipeline(provider, engine, lambda: False, None, None)
