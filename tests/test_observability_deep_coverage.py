from __future__ import annotations

import pytest

from core import observability as module


class _Logger:
    def __init__(self):
        self.messages = []

    def info(self, message, *args):
        self.messages.append(("info", message % args if args else message))

    def debug(self, message, *args):
        self.messages.append(("debug", message % args if args else message))


def test_observability_helper_edges():
    assert module._normalize_tags({"": 1, None: 2, " market ": " CN "}) == {"market": "CN"}
    assert module._normalize_tags(None) == {}
    assert module._coerce_float("1.5") == 1.5
    assert module._coerce_float(object()) is None
    assert module._format_elapsed_ms("bad") == ""
    assert "1.5" in module._format_elapsed_ms(1500)
    assert module._format_elapsed_ms(12.4).startswith("12")
    assert module._format_count("bad") == ""
    assert module._format_count(2.4, "x") == "2x"
    assert module._compact_join("a", "", "b") == "a | b"


@pytest.mark.parametrize(
    ("fields", "needles"),
    [
        ({"metric": "latency", "value": 1500, "unit": "ms"}, ("latency", "1.5")),
        ({"metric": "rows", "value": 2.2, "unit": "count"}, ("rows", "2")),
        ({"metric": "ratio", "value": 1.25, "unit": "%"}, ("ratio", "1.25%")),
        ({"metric": "label", "value": "n/a", "tags": {" market ": " CN ", "": "skip"}}, ("label", "n/a", "market=CN")),
        ({"metric": "", "value": 1}, ("unknown_metric", "1")),
    ],
)
def test_metric_event_formatting(fields, needles):
    result = module._format_metric_event(fields)
    assert all(needle in result for needle in needles)


@pytest.mark.parametrize(
    ("event", "fields", "needle"),
    [
        (
            "quotes.refresh.completed",
            {"provider_failed": True, "valid_quotes": False, "batch_size": 2, "elapsed_ms": 10},
            "2",
        ),
        ("quotes.refresh.completed", {"provider_failed": False, "valid_quotes": True}, "\u884c\u60c5"),
        ("kline.opened", {"code": "000001", "name": "Bank", "active_windows": 2, "elapsed_ms": 3}, "000001 Bank"),
        ("kline.opened", {}, "K"),
        ("workspace.mounted", {"tab_count": 3, "elapsed_ms": 4}, "3"),
        ("startup.deferred_load.completed", {"cache_loaded": False, "cache_date": "20260101"}, "20260101"),
        ("startup.asian_sync.completed", {"elapsed_ms": 5}, "5ms"),
        ("startup.network_probe.completed", {"online": True, "elapsed_ms": 6}, "6ms"),
        ("startup.network_probe.completed", {"online": False}, "\u542f\u52a8"),
        ("main_window.first_paint", {"elapsed_ms": 7}, "7ms"),
        ("metric.recorded", {"metric": "m", "value": 1}, "m"),
        ("custom", {"e": 5, "d": 4, "c": 3, "b": 2, "a": 1, "none": None, "empty": ""}, "a=1"),
        ("", {}, "unknown"),
    ],
)
def test_event_summary_variants(event, fields, needle):
    assert needle in module._format_event_summary(event, fields)


def test_event_summary_omits_empty_fallback_fields():
    assert module._format_event_summary("custom", {"empty": "", "none": None}) == "[事件] custom"


def test_emit_structured_log_filters_fields_and_falls_back_to_info():
    logger = _Logger()
    payload = module.emit_structured_log("", logger=logger, level="missing", **{"": 1, "ok": 2})
    assert payload == {"event": "unknown", "fields": {"ok": 2}}
    assert logger.messages[-1][0] == "info"

    payload = module.emit_structured_log("debug.event", logger=logger, level="debug", value=3)
    assert payload["event"] == "debug.event"
    assert logger.messages[-1][0] == "debug"


def test_metric_history_all_named_empty_and_clear():
    module.clear_metric_history()
    module.record_metric("one", 1, logger=_Logger(), tags={"x": None})
    module.record_metric("two", 2, logger=_Logger())
    assert len(module.metric_history()) == 2
    assert [sample.name for sample in module.metric_history(" one ")] == ["one"]
    assert module.metric_history("") == []
    module.clear_metric_history()
    assert module.metric_history() == []
