from __future__ import annotations

import pytest

from ui.kline_runtime_lifecycle import KLineRuntimeLifecycleController


def _payload(*, close: float, version: int) -> dict:
    return {
        "title": "Ping An Bank",
        "snapshotVersion": version,
        "data": {
            "code": "000001",
            "dates": ["2026-07-16"],
            "klines": [[10.0, close, 9.8, 10.5]],
        },
    }


def test_snapshot_is_deeply_immutable_and_decodes_to_fresh_payloads():
    controller = KLineRuntimeLifecycleController()
    source = _payload(close=10.2, version=1)

    snapshot = controller.record_snapshot(
        source,
        code="000001",
        generation=4,
        points=1,
        version=1,
    )
    source["data"]["dates"].append("2026-07-17")
    first = snapshot.payload()
    first["data"]["dates"].append("mutated")

    assert snapshot.code == "000001"
    assert snapshot.generation == 4
    assert snapshot.points == 1
    assert snapshot.version == 1
    assert controller.latest_snapshot == snapshot
    assert snapshot.payload()["data"]["dates"] == ["2026-07-16"]


def test_snapshot_json_entry_preserves_prepared_payload_and_window_identity():
    controller = KLineRuntimeLifecycleController()
    prepared_json = '{"title":"A \\u003ctitle","data":{"code":"000001","dates":[]}}'

    snapshot = controller.record_snapshot_json(
        prepared_json,
        window_id="window-7",
        code="000001",
        generation=9,
        points=0,
        version=12,
    )

    assert snapshot is not None
    assert snapshot.window_id == "window-7"
    assert snapshot.payload_json == prepared_json
    assert snapshot.payload()["title"] == "A <title"


def test_snapshot_json_entry_rejects_non_string_payload():
    controller = KLineRuntimeLifecycleController()

    with pytest.raises(TypeError, match="payload_json must be a string"):
        controller.record_snapshot_json(
            b"{}",  # type: ignore[arg-type]
            window_id="window-7",
            code="000001",
            generation=9,
            points=0,
            version=12,
        )


def test_hidden_and_minimized_runtime_replays_only_latest_pending_snapshot():
    controller = KLineRuntimeLifecycleController()
    controller.set_visibility(hidden=True)

    first = controller.record_snapshot(
        _payload(close=10.1, version=1),
        code="000001",
        generation=1,
        points=1,
        version=1,
    )
    latest = controller.record_snapshot(
        _payload(close=10.3, version=2),
        code="000001",
        generation=1,
        points=1,
        version=2,
    )

    assert controller.take_pending_submission() is None
    assert controller.set_visibility(hidden=False, minimized=True) is None
    assert controller.set_visibility(minimized=False) == latest
    assert controller.take_pending_submission() is None
    assert first != latest


def test_recovery_is_single_shot_and_replays_latest_snapshot_when_active():
    controller = KLineRuntimeLifecycleController()
    snapshot = controller.record_snapshot(
        _payload(close=10.2, version=1),
        code="000001",
        generation=1,
        points=1,
        version=1,
    )
    assert controller.take_pending_submission() == snapshot

    first = controller.request_recovery("browser-1")
    repeated = controller.request_recovery("browser-1")
    replacement_crash = controller.request_recovery("browser-2")

    assert first.allowed is True
    assert first.reason == "recovery_scheduled"
    assert controller.take_pending_submission() == snapshot
    assert repeated.allowed is False
    assert repeated.reason == "recovery_already_used"
    assert replacement_crash.allowed is False
    assert replacement_crash.reason == "recovery_already_used"


def test_hidden_recovery_waits_and_close_discards_pending_work():
    controller = KLineRuntimeLifecycleController()
    controller.set_visibility(hidden=True)
    controller.record_snapshot(
        _payload(close=10.1, version=1),
        code="000001",
        generation=1,
        points=1,
        version=1,
    )

    assert controller.request_recovery(object()).allowed is True
    latest = controller.record_snapshot(
        _payload(close=10.4, version=2),
        code="000001",
        generation=1,
        points=1,
        version=2,
    )
    assert controller.take_pending_submission() is None
    assert controller.set_visibility(hidden=False) == latest

    controller.set_visibility(hidden=True)
    controller.record_snapshot(
        _payload(close=10.5, version=3),
        code="000001",
        generation=1,
        points=1,
        version=3,
    )
    controller.begin_close()

    assert controller.runtime_active is False
    assert controller.take_pending_submission() is None
    assert controller.record_snapshot(
        _payload(close=10.6, version=4),
        code="000001",
        generation=1,
        points=1,
        version=4,
    ) is None
    assert controller.request_recovery("late-browser").reason == "closing"
