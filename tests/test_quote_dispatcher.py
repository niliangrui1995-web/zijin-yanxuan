# -*- coding: utf-8 -*-
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication

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
