from __future__ import annotations

import pandas as pd

from app.services.ai_industry_chain_period_return_service import build_period_return_rows


def test_period_return_rows_use_one_batch_history_read():
    calls = []

    class _Provider:
        def get_data_batch(self, codes):
            calls.append(tuple(codes))
            return {
                "000001": pd.DataFrame({"close": list(range(1, 22))}),
                "000002": pd.DataFrame({"close": [10, 11, 12, 13, 14, 15]}),
            }

        def get_data(self, _code):
            raise AssertionError("batch-capable providers must not scan one symbol at a time")

    rows = build_period_return_rows(
        [{"代码": "000001"}, {"代码": "000002"}, {"代码": "000001"}],
        data_provider=_Provider(),
        period_columns={5: "5日涨幅", 20: "20日涨幅"},
        placeholder="--",
    )

    assert calls == [("000001", "000002")]
    assert rows[0]["5日涨幅"] == (21 / 16 - 1) * 100
    assert rows[0]["20日涨幅"] == (21 / 1 - 1) * 100
    assert rows[1]["5日涨幅"] == (15 / 10 - 1) * 100
    assert rows[1]["20日涨幅"] == "--"
    assert rows[2] == rows[0]


def test_period_return_rows_prefer_close_tail_batch_without_frame_materialization():
    calls = []

    class _Provider:
        def get_close_tail_batch(self, codes, limit):
            calls.append((tuple(codes), limit))
            return {
                "000001": tuple(range(1, 22)),
                "000002": (10.0, None, 15.0),
                "000003": (10.0, "invalid", 15.0),
            }

        def get_data_batch(self, _codes):
            raise AssertionError("close-tail capable providers must not materialize full history frames")

    rows = build_period_return_rows(
        [{"代码": "000001"}, {"代码": "000002"}, {"代码": "000003"}],
        data_provider=_Provider(),
        period_columns={5: "5日涨幅", 20: "20日涨幅"},
        placeholder="--",
    )

    assert calls == [(('000001', '000002', '000003'), 21)]
    assert rows[0]["5日涨幅"] == (21 / 16 - 1) * 100
    assert rows[0]["20日涨幅"] == (21 / 1 - 1) * 100
    assert rows[1]["5日涨幅"] == "--"
    assert rows[1]["20日涨幅"] == "--"
    assert rows[2]["5日涨幅"] == "--"
    assert rows[2]["20日涨幅"] == "--"


def test_period_return_rows_fall_back_to_existing_batch_path_when_close_tail_fails():
    calls = []

    class _Provider:
        def get_close_tail_batch(self, codes, limit):
            calls.append(("tail", tuple(codes), limit))
            raise OSError("narrow reader unavailable")

        def get_data_batch(self, codes):
            calls.append(("frames", tuple(codes)))
            return {
                "000001": pd.DataFrame(
                    {"close": [15.0, 10.0, None, 12.0]},
                    index=pd.to_datetime(["2026-01-04", "2026-01-01", "2026-01-03", "2026-01-02"]),
                )
            }

    rows = build_period_return_rows(
        [{"代码": "000001"}],
        data_provider=_Provider(),
        period_columns={2: "2日涨幅"},
        placeholder="--",
    )

    assert calls == [("tail", ("000001",), 3), ("frames", ("000001",))]
    assert rows == [{"代码": "000001", "2日涨幅": 50.0}]


def test_period_return_rows_keep_single_symbol_provider_fallback():
    calls = []

    class _Provider:
        def get_data(self, code):
            calls.append(code)
            return pd.DataFrame({"收盘": [10, 12, 15]})

    rows = build_period_return_rows(
        [{"代码": "000001"}],
        data_provider=_Provider(),
        period_columns={2: "2日涨幅"},
        placeholder="--",
    )

    assert calls == ["000001"]
    assert rows == [{"代码": "000001", "2日涨幅": 50.0}]
