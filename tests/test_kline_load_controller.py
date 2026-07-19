# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from ui.kline_load_controller import KlineLoadController


def test_kline_load_controller_generates_stable_window_uuid():
    controller = KlineLoadController()

    assert len(controller.window_id) == 32
    assert controller.window_id == controller.window_id


def test_kline_load_controller_isolates_windows_and_generation_task_ids():
    first = KlineLoadController(window_id="window-a")
    second = KlineLoadController(window_id="window-b")

    first_identity = first.begin("000001")
    second_identity = second.begin("000001")

    assert first_identity.generation == second_identity.generation == 1
    assert first.task_id("history") == "kline:window-a:1:history"
    assert second.task_id("history") == "kline:window-b:1:history"
    assert first.task_id("history") != second.task_id("history")


def test_kline_load_controller_tracks_latest_identity_and_frame_owner():
    controller = KlineLoadController(window_id="window-a")
    old_identity = controller.begin("000001")

    assert controller.claim_frame(old_identity) is True
    assert controller.owns_current_frame("000001", 1) is True

    current_identity = controller.begin("000001")

    assert current_identity.generation == 2
    assert controller.is_current(old_identity) is False
    assert controller.claim_frame(old_identity) is False
    assert controller.owns_current_frame("000001", 1) is False
    assert controller.claim_frame(current_identity) is True
    assert controller.owns_current_frame("000001", 2) is True
    assert controller.task_id("realtime", identity=old_identity) == "kline:window-a:1:realtime"


def test_kline_load_controller_serializes_all_stages_and_keeps_latest_pending():
    controller = KlineLoadController(window_id="window-a")
    first = controller.begin("000001")

    first_ticket, should_start = controller.request_task(first, "history")
    assert should_start is True
    assert controller.running_task == first_ticket
    assert controller.pending_task is None

    second = controller.begin("000002")
    second_ticket, should_start = controller.request_task(second, "render")
    assert should_start is False
    assert controller.running_task == first_ticket
    assert controller.pending_task == second_ticket

    latest = controller.begin("000003")
    latest_ticket, should_start = controller.request_task(latest, "realtime-quote")
    assert should_start is False
    assert controller.running_task == first_ticket
    assert controller.pending_task == latest_ticket

    assert controller.settle_task(second_ticket) is None
    assert controller.settle_task(first_ticket) == latest_ticket
    assert controller.running_task == latest_ticket
    assert controller.pending_task is None

    assert controller.settle_task(latest_ticket) is None
    assert controller.running_task is None


def test_kline_load_controller_close_clears_pending_but_retires_running_ticket():
    controller = KlineLoadController(window_id="window-a")
    running = controller.begin("000001")
    running_ticket, should_start = controller.request_task(running, "history")
    assert should_start is True
    pending = controller.begin("000002")
    pending_ticket, should_start = controller.request_task(pending, "render")
    assert should_start is False
    assert pending_ticket != running_ticket

    controller.close()

    assert controller.running_task == running_ticket
    assert controller.pending_task is None
    assert controller.settle_task(running_ticket) is None
    assert controller.running_task is None


def test_kline_load_controller_rejects_reopen_until_running_task_stops():
    controller = KlineLoadController(window_id="window-a")
    running = controller.begin("000001")
    running_ticket, should_start = controller.request_task(running, "history")
    assert should_start is True
    controller.close()

    with pytest.raises(RuntimeError, match="running"):
        controller.reopen_lease()

    controller.settle_task(running_ticket)
    controller.reopen_lease()
    assert controller.closed is False


def test_kline_load_controller_close_invalidates_identity_and_frame():
    controller = KlineLoadController(window_id="window-a")
    identity = controller.begin("2330.TW")
    assert controller.claim_frame(identity) is True

    controller.close()

    assert controller.closed is True
    assert controller.current_identity is None
    assert controller.frame_owner is None
    assert controller.is_current(identity) is False
    with pytest.raises(RuntimeError, match="closed"):
        controller.begin("000001")


@pytest.mark.parametrize("stage", ["", "bad stage", "../history"])
def test_kline_load_controller_rejects_invalid_task_stages(stage):
    controller = KlineLoadController(window_id="window-a")
    controller.begin("000001")

    with pytest.raises(ValueError, match="stage"):
        controller.task_id(stage)


def test_kline_load_controller_requires_identity_before_task_id():
    controller = KlineLoadController(window_id="window-a")

    with pytest.raises(RuntimeError, match="generation"):
        controller.task_id("history")


def test_kline_load_controller_rejects_invalid_identity_inputs():
    with pytest.raises(ValueError, match="window_id"):
        KlineLoadController(window_id="window:bad")

    controller = KlineLoadController(window_id="window-a")
    with pytest.raises(ValueError, match="code"):
        controller.begin(" ")

    foreign_identity = KlineLoadController(window_id="window-b").begin("000001")
    with pytest.raises(ValueError, match="another"):
        controller.task_id("history", identity=foreign_identity)
