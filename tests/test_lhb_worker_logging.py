# -*- coding: utf-8 -*-
import logging

import pandas as pd

import infra.market_data.lhb_provider as lhb_worker


def test_fetch_lhb_data_for_date_returns_empty_meta(monkeypatch):
    monkeypatch.setattr(lhb_worker.ak, "stock_lhb_detail_em", lambda start_date, end_date: pd.DataFrame())

    payload = lhb_worker.fetch_lhb_data_for_date(
        "20260414",
        strict_filter=False,
        emit_success_log=False,
        return_meta=True,
    )

    assert payload["status"] == "empty"
    assert payload["count"] == 0
    assert payload["records"] == []


def test_fetch_lhb_data_for_date_returns_error_meta_without_error_log(monkeypatch):
    def _boom(start_date, end_date):
        raise TypeError("NoneType is not subscriptable")

    monkeypatch.setattr(lhb_worker.ak, "stock_lhb_detail_em", _boom)
    records = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = ListHandler()
    lhb_worker.log.addHandler(handler)
    try:
        payload = lhb_worker.fetch_lhb_data_for_date(
            "20260414",
            strict_filter=False,
            emit_success_log=False,
            return_meta=True,
        )
    finally:
        lhb_worker.log.removeHandler(handler)

    assert payload["status"] == "error"
    assert payload["count"] == 0
    assert payload["records"] == []
    assert "基础榜单异常" in payload["message"]
    assert any(record.levelno == logging.WARNING for record in records)
    assert not any(record.levelno >= logging.ERROR for record in records)
