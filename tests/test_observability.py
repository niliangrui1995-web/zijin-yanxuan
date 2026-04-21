# -*- coding: utf-8 -*-
from core.observability import clear_metric_history, emit_structured_log, metric_history, record_metric


class _DummyLogger:
    def __init__(self):
        self.messages = []

    def info(self, message, *args):
        self.messages.append(("info", message % args if args else message))

    def debug(self, message, *args):
        self.messages.append(("debug", message % args if args else message))


def test_record_metric_keeps_history_and_tags():
    clear_metric_history()

    sample = record_metric(
        "quote_refresh_ms",
        123.4,
        unit="ms",
        tags={"market": "CN", "source": "central_quotes"},
        logger=_DummyLogger(),
    )

    assert sample.name == "quote_refresh_ms"
    assert sample.unit == "ms"
    assert sample.tags == {"market": "CN", "source": "central_quotes"}
    history = metric_history("quote_refresh_ms")
    assert history
    assert history[-1].value == 123.4


def test_emit_structured_log_serializes_payload():
    logger = _DummyLogger()

    payload = emit_structured_log(
        "startup.deferred_load.completed",
        logger=logger,
        elapsed_ms=42.5,
        cache_loaded=True,
    )

    assert payload["event"] == "startup.deferred_load.completed"
    assert payload["fields"]["elapsed_ms"] == 42.5
    assert payload["fields"]["cache_loaded"] is True
    assert logger.messages
    assert logger.messages[-1] == ("info", "[启动] 缓存加载完成 | 已载入缓存 | 42ms")


def test_record_metric_defaults_to_debug_structured_output():
    logger = _DummyLogger()

    record_metric(
        "quote_refresh_ms",
        123.4,
        unit="ms",
        logger=logger,
    )

    assert logger.messages
    assert logger.messages[-1][0] == "debug"
    assert '"event": "metric.recorded"' in logger.messages[-1][1]
