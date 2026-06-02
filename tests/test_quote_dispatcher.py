# -*- coding: utf-8 -*-
from types import SimpleNamespace

from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication

import domains.quotes.dispatcher as dispatcher_module
from core.event_bus import event_bus
from core.global_store import global_store
from core.quote_dispatcher import publish_rt_quotes


def test_publish_rt_quotes_emits_normalized_payload():
    app = QApplication.instance() or QApplication([])
    spy = QSignalSpy(event_bus.sig_rt_quotes)

    publish_rt_quotes({"000001": {"close": 12.3}, "": {"close": 0}})
    app.processEvents()

    assert len(spy) == 1
    assert spy[0][0] == {"000001": {"close": 12.3}}
    assert global_store.get_latest_quotes()["000001"]["close"] == 12.3


def test_publish_rt_quotes_skips_invalid_payload_when_required():
    app = QApplication.instance() or QApplication([])
    spy = QSignalSpy(event_bus.sig_rt_quotes)

    publish_rt_quotes({"000001": {"close": 0, "market_cap": 1}}, require_valid=True)
    app.processEvents()

    assert len(spy) == 0


def test_publish_rt_quotes_returns_empty_and_handles_emit_failure(monkeypatch):
    assert publish_rt_quotes({}) == {}

    merged = []
    monkeypatch.setattr(dispatcher_module, "global_store", SimpleNamespace(merge_quotes=lambda payload: merged.append(payload)))
    monkeypatch.setattr(
        dispatcher_module,
        "event_bus",
        SimpleNamespace(
            sig_rt_quotes=SimpleNamespace(
                emit=lambda payload: (_ for _ in ()).throw(RuntimeError("signal deleted")),
            )
        ),
    )

    result = dispatcher_module.publish_rt_quotes({"000001": {"close": 1.0}}, source="test")

    assert result == {"000001": {"close": 1.0}}
    assert merged == [{"000001": {"close": 1.0}}]
