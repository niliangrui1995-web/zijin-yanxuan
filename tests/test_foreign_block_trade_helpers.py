import pandas as pd

from ui.tabs.foreign_block_trade_tab import (
    _normalize_trade_date_series,
    _normalize_trade_date_value,
)


def test_normalize_trade_date_value_handles_epoch_ms():
    assert _normalize_trade_date_value("1775779200000") == "2026-04-10"


def test_normalize_trade_date_series_handles_iso_and_plain_text():
    series = pd.Series(["2026-04-10T00:00:00.000", "20260411", "2026-04-08"])
    result = _normalize_trade_date_series(series).tolist()
    assert result == ["2026-04-10", "2026-04-11", "2026-04-08"]
