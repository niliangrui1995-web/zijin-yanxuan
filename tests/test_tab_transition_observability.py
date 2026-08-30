# -*- coding: utf-8 -*-
from types import SimpleNamespace

from ui.workspaces import tab_transition_observability as transition_observability


def test_transition_stage_keeps_id_in_structured_log_and_uses_bounded_metric_tags(monkeypatch):
    metrics = []
    logs = []
    owner = SimpleNamespace(_tab_transition_sequence=0)
    widget = SimpleNamespace()

    monkeypatch.setattr(
        transition_observability,
        "record_metric",
        lambda metric, value, **kwargs: metrics.append((metric, value, kwargs)),
    )
    monkeypatch.setattr(
        transition_observability,
        "emit_structured_log",
        lambda event, **kwargs: logs.append((event, kwargs)),
    )

    context = transition_observability.begin_tab_transition(
        owner,
        source_tab="watchlist",
        target_tab="asian_market",
        reason="shell_nav",
        mounted_before=True,
        preload_active_key="",
    )
    transition_observability.attach_tab_transition_context(widget, context)

    with transition_observability.tab_transition_stage(
        widget,
        tab="asian_market",
        method="AsianMarketTab.showEvent",
        stage="reveal_or_mount",
        layout_signal="showEvent",
    ):
        pass

    assert context["transition_id"] == "1"
    assert widget._workspace_tab_transition_context["source_tab"] == "watchlist"
    metric, _value, kwargs = metrics[-1]
    assert metric == "tab_transition_stage_ms"
    assert kwargs["tags"] == {
        "source_tab": "watchlist",
        "target_tab": "asian_market",
        "reason": "shell_nav",
        "stage": "reveal_or_mount",
        "preload_state": "ready",
        "mounted_before": "true",
        "callback_kind": "",
        "layout_signal": "showEvent",
    }
    assert "transition_id" not in kwargs["tags"]
    assert logs[-1][0] == "workspace.tab_transition_stage"
    assert logs[-1][1]["transition_id"] == "1"
    assert logs[-1][1]["preload_active_key"] == ""


def test_transition_context_expires_before_it_can_misattribute_later_native_paints(monkeypatch):
    owner = SimpleNamespace(_tab_transition_sequence=0)
    widget = SimpleNamespace()
    clock = [100.0]
    monkeypatch.setattr(transition_observability.time, "monotonic", lambda: clock[0])

    context = transition_observability.begin_tab_transition(
        owner,
        source_tab="stock_candidates",
        target_tab="watchlist",
        reason="shell_nav",
        mounted_before=True,
        preload_active_key="",
        target_preload_state="interactive_warm",
    )
    transition_observability.attach_tab_transition_context(widget, context)
    clock[0] += (transition_observability.TAB_TRANSITION_CONTEXT_MAX_AGE_MS / 1000.0) + 0.001

    assert transition_observability.tab_transition_context(widget, tab="watchlist") == {}
