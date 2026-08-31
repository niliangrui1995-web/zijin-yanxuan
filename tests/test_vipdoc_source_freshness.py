# -*- coding: utf-8 -*-
from __future__ import annotations

import struct
from pathlib import Path

from infra.market_data import vipdoc_source_freshness as source_module
from infra.market_data.vipdoc_source_freshness import inspect_vipdoc_daily_source


def _write_day_tail(path: Path, trade_date: int, *, close: int = 1000, records: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = struct.pack("<IIIII fII", trade_date, 1000, 1100, 900, close, 1.0, 1, 0)
    path.write_bytes(record * records)


def test_vipdoc_freshness_uses_tail_date_mode_and_changes_signature_on_latest_bar_change(tmp_path):
    vipdoc = tmp_path / "vipdoc"
    _write_day_tail(vipdoc / "sh" / "lday" / "sh600000.day", 20260831, records=250)
    _write_day_tail(vipdoc / "sh" / "lday" / "sh688001.day", 20260831, records=250)
    _write_day_tail(vipdoc / "sz" / "lday" / "sz000001.day", 20260831, records=250)
    _write_day_tail(vipdoc / "bj" / "lday" / "bj920001.day", 20260828, records=250)
    (vipdoc / "sz" / "lday" / "sz200001.day").write_bytes(b"not-an-a-share-source")

    first = inspect_vipdoc_daily_source(vipdoc)

    assert first.effective_trade_date == "20260831"
    assert first.symbol_count == 4
    assert first.dated_symbol_count == 4
    assert first.unstable is False
    assert len(first.signature) == 64

    _write_day_tail(vipdoc / "sz" / "lday" / "sz000001.day", 20260831, close=1234, records=250)
    second = inspect_vipdoc_daily_source(vipdoc)

    assert second.effective_trade_date == first.effective_trade_date
    assert second.signature != first.signature


def test_vipdoc_freshness_excludes_short_history_from_effective_date_and_signature(tmp_path):
    vipdoc = tmp_path / "vipdoc"
    _write_day_tail(vipdoc / "sh" / "lday" / "sh600000.day", 20260830, records=250)
    _write_day_tail(vipdoc / "sh" / "lday" / "sh688001.day", 20260830, records=250)
    _write_day_tail(vipdoc / "sz" / "lday" / "sz000001.day", 20260829, records=250)
    short_path = vipdoc / "sz" / "lday" / "sz300001.day"
    _write_day_tail(short_path, 20260831)

    first = inspect_vipdoc_daily_source(vipdoc)

    assert first.source_file_count == 4
    assert first.symbol_count == 3
    assert first.dated_symbol_count == 3
    assert first.effective_trade_date == "20260830"

    _write_day_tail(short_path, 20260831, close=1234)
    second = inspect_vipdoc_daily_source(vipdoc)

    assert second.signature == first.signature
    assert second.effective_trade_date == first.effective_trade_date


def test_vipdoc_freshness_detects_eligible_file_rewritten_during_tail_read(tmp_path, monkeypatch):
    vipdoc = tmp_path / "vipdoc"
    day_path = vipdoc / "sh" / "lday" / "sh600000.day"
    _write_day_tail(day_path, 20260831, records=250)
    original_read_tail = source_module._read_day_tail
    rewritten = False

    def _rewrite_after_read(path, size_bytes):
        nonlocal rewritten
        payload = original_read_tail(path, size_bytes)
        if not rewritten:
            rewritten = True
            with Path(path).open("ab") as handle:
                handle.write(payload)
        return payload

    monkeypatch.setattr(source_module, "_read_day_tail", _rewrite_after_read)

    report = inspect_vipdoc_daily_source(vipdoc)

    assert report.unstable is True


def test_vipdoc_freshness_marks_present_but_unreadable_source_directory_unstable(tmp_path, monkeypatch):
    vipdoc = tmp_path / "vipdoc"
    _write_day_tail(vipdoc / "sh" / "lday" / "sh600000.day", 20260831, records=250)
    blocked_directory = vipdoc / "sz" / "lday"
    blocked_directory.mkdir(parents=True)
    real_scandir = source_module.os.scandir

    def _scandir(path):
        if Path(path).resolve() == blocked_directory.resolve():
            raise PermissionError("access denied")
        return real_scandir(path)

    monkeypatch.setattr(source_module.os, "scandir", _scandir)

    report = inspect_vipdoc_daily_source(vipdoc)

    assert report.unstable is True


def test_vipdoc_freshness_returns_empty_report_for_missing_or_invalid_source(tmp_path):
    missing = inspect_vipdoc_daily_source(tmp_path / "missing")
    assert missing.effective_trade_date == ""
    assert missing.symbol_count == 0
    assert missing.dated_symbol_count == 0
