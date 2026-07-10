from scripts import native_watchlist_profile
from scripts.native_watchlist_profile import (
    _event_dispatcher_summary,
    _native_platform_error,
    _parse_args,
    summarize_durations,
)


def test_native_watchlist_profile_rejects_non_native_qt_plugins():
    assert "not a native desktop platform" in _native_platform_error(
        requested="offscreen",
        actual="offscreen",
        system="win32",
    )
    assert _native_platform_error(requested="", actual="windows", system="win32") == ""


def test_native_watchlist_profile_separates_dispatch_work_from_sleep():
    summary = _event_dispatcher_summary(
        [
            {"kind": "active_dispatch", "phase": "watchlist_activation", "elapsed_ms": 144.0},
            {"kind": "blocked_wait", "phase": "watchlist_activation", "elapsed_ms": 612.0},
            {"kind": "active_dispatch", "phase": "watchlist_settle", "elapsed_ms": 12.0},
        ]
    )

    activation = summary["phases"]["watchlist_activation"]
    assert activation["active_dispatch"]["max_ms"] == 144.0
    assert activation["blocked_wait"]["max_ms"] == 612.0
    assert summary["largest_active_dispatch_segments"][0]["elapsed_ms"] == 144.0


def test_native_watchlist_profile_duration_summary_reports_tail_thresholds():
    summary = summarize_durations([1.0, 50.0, 100.0, 200.0])

    assert summary["count"] == 4
    assert summary["max_ms"] == 200.0
    assert summary["over_50ms"] == 3
    assert summary["over_100ms"] == 2


def test_native_watchlist_profile_cli_has_bounded_default_sampling_window():
    args = _parse_args([])

    assert args.warmup_ms == 500
    assert args.settle_ms == 3500
    assert args.load_timeout_ms == 8000
    assert args.heartbeat_ms == 25
    assert args.no_cprofile is False


def test_native_watchlist_profile_cleans_isolated_database_on_exit(tmp_path, monkeypatch):
    callbacks = []
    database_path = tmp_path / "profile.db"
    database_path.write_bytes(b"db")
    (tmp_path / "profile.db-wal").write_bytes(b"wal")
    monkeypatch.setattr(native_watchlist_profile.atexit, "register", callbacks.append)

    native_watchlist_profile._register_profile_database_cleanup(database_path)
    callbacks[0]()

    assert not database_path.exists()
    assert not (tmp_path / "profile.db-wal").exists()
