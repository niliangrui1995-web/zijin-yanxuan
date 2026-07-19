# -*- coding: utf-8 -*-
from types import SimpleNamespace

from ui.kline_render_bridge import (
    build_apply_snapshot_script,
    build_runtime_active_script,
    build_snapshot_render_state_script,
    prepared_matches_current_load,
    snapshot_ack_is_queued,
    snapshot_ack_matches,
    snapshot_render_ack_matches,
)
from ui.kline_runtime_lifecycle import KLineRuntimeLifecycleController


def _snapshot():
    lifecycle = KLineRuntimeLifecycleController()
    return lifecycle.record_snapshot_json(
        '{"windowId":"window-a","generation":2,"code":"000001","points":250,"data":{}}',
        window_id="window-a",
        code="000001",
        generation=2,
        points=250,
        version=3,
    )


def test_snapshot_script_embeds_the_already_serialized_envelope_once():
    payload_json = '{"windowId":"window-a","data":{"title":"A"}}'
    script = build_apply_snapshot_script(payload_json)

    assert script.count(payload_json) == 1
    assert "JSON.parse" not in script
    assert "window.applySnapshot" in script
    assert "replaceKlineData" not in script


def test_snapshot_ack_requires_exact_owner_generation_code_points_and_real_apply():
    snapshot = _snapshot()
    good = {
        "ok": True,
        "applied": True,
        "windowId": "window-a",
        "generation": 2,
        "code": "000001",
        "points": 250,
        "snapshotVersion": 3,
    }

    assert snapshot_ack_matches(snapshot, good) is True
    assert snapshot_ack_matches(snapshot, dict(good, duplicate=True, applied=False)) is True
    assert snapshot_ack_matches(snapshot, dict(good, queued=True, applied=False)) is False
    assert snapshot_ack_matches(snapshot, dict(good, windowId="window-b")) is False
    assert snapshot_ack_matches(snapshot, dict(good, generation=1)) is False
    assert snapshot_ack_matches(snapshot, dict(good, code="000002")) is False
    assert snapshot_ack_matches(snapshot, dict(good, points=249)) is False
    assert snapshot_ack_matches(snapshot, dict(good, snapshotVersion=2)) is False
    assert snapshot_ack_matches(snapshot, None) is False
    assert snapshot_ack_is_queued(snapshot, dict(good, queued=True, applied=False)) is True
    assert snapshot_ack_is_queued(snapshot, dict(good, queued=False, applied=False)) is False


def test_render_state_query_and_ack_require_the_same_strict_snapshot_identity():
    snapshot = _snapshot()
    script = build_snapshot_render_state_script(snapshot)
    good = {
        "ok": True,
        "rendered": True,
        "windowId": "window-a",
        "generation": 2,
        "code": "000001",
        "points": 250,
        "snapshotVersion": 3,
    }

    assert "window.getSnapshotRenderState" in script
    for value in ("window-a", "000001", "250", "3"):
        assert value in script
    assert snapshot_render_ack_matches(snapshot, good) is True
    assert snapshot_render_ack_matches(snapshot, dict(good, rendered=False)) is False
    assert snapshot_render_ack_matches(snapshot, dict(good, generation=3)) is False


def test_prepared_render_must_match_the_controller_current_identity():
    current = SimpleNamespace(window_id="window-a", generation=2, code="000001")
    controller = SimpleNamespace(current_identity=current, closed=False)
    prepared = SimpleNamespace(owner_id="window-a", generation=2, code="000001")

    assert prepared_matches_current_load(controller, prepared) is True
    assert prepared_matches_current_load(controller, SimpleNamespace(owner_id="window-b", generation=2, code="000001")) is False
    controller.closed = True
    assert prepared_matches_current_load(controller, prepared) is False


def test_runtime_active_script_has_plain_boolean_payload():
    assert "true" in build_runtime_active_script(True)
    assert "false" in build_runtime_active_script(False)
    assert "window.setRuntimeActive" in build_runtime_active_script(True)
