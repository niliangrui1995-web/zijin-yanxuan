# -*- coding: utf-8 -*-
from __future__ import annotations

import pandas as pd
import pytest

import infra.market_data.lhb_provider as provider
from infra.tasks.lifecycle import TaskCancelledError, TaskDeadlineExceeded


def _candidate(
    *,
    reason: str,
    close: float,
    pct: float,
    net: float,
    buyers: int = 1,
    sellers: int = 0,
) -> dict:
    return {
        "买方机构数": buyers,
        "卖方机构数": sellers,
        "机构买入总额": max(net, 0.0),
        "机构卖出总额": max(-net, 0.0),
        "机构买入净额": net,
        "_上榜原因_key": provider._normalize_reason_key(reason),
        "_上榜原因_tokens": provider._reason_tokens(reason),
        "_收盘价": close,
        "_涨跌幅": pct,
    }


def _detail_row(**overrides) -> dict:
    row = {
        "代码": "1",
        "名称": "测试股份",
        "龙虎榜净买额": 8000000.0,
        "收盘价": 12.3,
        "涨跌幅": 5.6,
        "换手率": 7.8,
        "流通市值": 0.0,
        "上榜原因": "原因A",
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (12000, "1.2亿"),
        (100, "100万"),
        (10, "10.0万"),
        (9.876, "9.88万"),
        (-123, "123万"),
    ],
)
def test_amount_formatter_covers_all_display_units(amount: float, expected: str) -> None:
    assert provider._format_wan_amount(amount) == expected


def test_foreign_amount_helpers_cover_buy_sell_and_balance() -> None:
    assert provider._foreign_amount_summary(10) == "净买10.0万"
    assert provider._foreign_amount_summary(-10) == "净卖10.0万"
    assert provider._foreign_amount_summary(0) == "平衡"
    assert provider._foreign_short_part("高盛", 2) == "高盛+2.00万"
    assert provider._foreign_short_part("高盛", -2) == "高盛-2.00万"
    assert provider._foreign_short_part("高盛", 0) == "高盛±0"
    assert provider._foreign_tooltip_line("高盛", 0) == "高盛：平衡0.00万"


def test_reason_normalization_handles_empty_separator_only_and_deduplication() -> None:
    assert provider._normalize_reason_key("") == ""
    assert provider._normalize_reason_key(" | ｜ ") == ""
    assert provider._normalize_reason_key(" 原因B｜原因A|原因B ") == "原因A | 原因B"
    assert provider._reason_tokens("") == set()
    assert provider._reason_tokens("原因B|原因A") == {"原因A", "原因B"}


def test_institution_candidate_merge_handles_empty_duplicates_and_ranking() -> None:
    low = _candidate(reason="A", close=1, pct=1, net=100, buyers=1)
    high = _candidate(reason="B", close=2, pct=2, net=-500, buyers=2, sellers=1)

    assert provider._merge_jg_candidates([]) == provider.DEFAULT_JG_INFO
    assert provider._merge_jg_candidates([low, dict(low)]) == low
    assert provider._merge_jg_candidates([low, high]) == high


def test_institution_resolution_uses_exact_single_price_token_then_ranked_fallback() -> None:
    exact = _candidate(reason="A", close=10, pct=1, net=10)
    price = _candidate(reason="B", close=20, pct=2, net=20)
    token = _candidate(reason="C|D", close=30, pct=3, net=30)
    fallback = _candidate(reason="E", close=40, pct=4, net=40)

    assert (
        provider._resolve_jg_info("000001", "A", 99, 99, {("000001", "A"): [exact]}, {"000001": [price]})
        == exact
    )
    assert provider._resolve_jg_info("000001", "", 99, 99, {}, {"000001": [exact]}) == exact
    assert provider._resolve_jg_info("000001", "A", 99, 99, {}, {}) == provider.DEFAULT_JG_INFO
    assert provider._resolve_jg_info(
        "000001", "unknown", 20, 2, {}, {"000001": [exact, price]}
    ) == price
    assert provider._resolve_jg_info(
        "000001", "C", 99, 99, {}, {"000001": [exact, token]}
    ) == token
    assert provider._resolve_jg_info(
        "000001", "unknown", 99, 99, {}, {"000001": [exact, fallback]}
    ) == fallback
    assert provider._resolve_jg_info(
        "000001", "", 99, 99, {}, {"000001": [exact, fallback]}
    ) == fallback


def test_foreign_row_key_tolerates_nan_invalid_numbers_and_optional_reason() -> None:
    row = pd.Series(
        {
            "交易营业部名称": " 深股通专用 ",
            "买入金额": float("nan"),
            "卖出金额": "invalid",
            "净额": "3.14159",
            "类型": "B｜A",
        }
    )

    assert provider._build_foreign_row_key(row) == ("深股通专用", 0.0, 0.0, 3.14, "A | B")
    assert provider._build_foreign_row_key(row, include_reason=False) == ("深股通专用", 0.0, 0.0, 3.14)


@pytest.mark.parametrize(
    ("detail_reason", "target_reason", "expected"),
    [
        ("", "", True),
        ("", "A", False),
        ("A|B", "A|B", True),
        ("A", "A|B", True),
        ("A|B", "A", True),
        ("A|C", "A|B", True),
        ("C", "A|B", False),
    ],
)
def test_foreign_reason_matching_supports_empty_exact_subset_and_overlap(
    detail_reason: str,
    target_reason: str,
    expected: bool,
) -> None:
    assert provider._foreign_reason_matches(detail_reason, target_reason) is expected


def test_foreign_detail_collection_filters_deduplicates_and_tolerates_bad_net() -> None:
    assert provider._collect_foreign_branch_details(pd.DataFrame(), "A") == {}
    rows = pd.DataFrame(
        [
            {"交易营业部名称": "普通营业部", "净额": 99999, "类型": "A"},
            {"交易营业部名称": "高盛证券", "净额": 99999, "类型": "B"},
            {"交易营业部名称": "深股通专用", "买入金额": 1, "卖出金额": 2, "净额": "bad", "类型": "A"},
            {"交易营业部名称": "深股通专用", "买入金额": 1, "卖出金额": 2, "净额": "bad", "类型": "A"},
            {"交易营业部名称": "高盛证券", "买入金额": 3, "卖出金额": 1, "净额": 20000, "类型": "A"},
        ]
    )

    assert provider._collect_foreign_branch_details(rows, "A") == {"深股通": 0.0, "高盛": 2.0}


def test_detail_loader_merges_reasons_by_code_and_net_amount(monkeypatch) -> None:
    frame = pd.DataFrame(
        [
            _detail_row(**{"代码": "000001", "龙虎榜净买额": 100, "上榜原因": "原因A"}),
            _detail_row(**{"代码": "000001", "龙虎榜净买额": 100, "上榜原因": "原因B"}),
            _detail_row(**{"代码": "000001", "龙虎榜净买额": 200, "上榜原因": "原因C"}),
        ]
    )
    monkeypatch.setattr(provider.ak, "stock_lhb_detail_em", lambda **_kwargs: frame.copy())

    result, status, _message = provider._load_lhb_detail_frame("20260710")

    assert status == "ok"
    assert len(result) == 2
    assert result.iloc[0]["上榜原因"] == "原因A | 原因B"


def test_detail_loader_accepts_legacy_frame_without_dedupe_columns(monkeypatch) -> None:
    frame = pd.DataFrame([{"代码": "000001", "名称": "测试"}])
    monkeypatch.setattr(provider.ak, "stock_lhb_detail_em", lambda **_kwargs: frame.copy())

    result, status, _message = provider._load_lhb_detail_frame("20260710")

    assert status == "ok"
    assert result.to_dict("records") == frame.to_dict("records")


class _CancelAfterProviderCall:
    def __init__(self, error_type=TaskDeadlineExceeded) -> None:
        self.calls = 0
        self.error_type = error_type

    def raise_if_cancelled(self) -> None:
        self.calls += 1
        if self.calls == 2:
            raise self.error_type("stop")


def test_detail_loader_propagates_deadline_after_provider_returns(monkeypatch) -> None:
    monkeypatch.setattr(provider.ak, "stock_lhb_detail_em", lambda **_kwargs: pd.DataFrame([{"x": 1}]))

    with pytest.raises(TaskDeadlineExceeded, match="stop"):
        provider._load_lhb_detail_frame("20260710", cancellation_token=_CancelAfterProviderCall())


def test_institution_loader_propagates_cancellation_and_recovers_provider_errors(monkeypatch) -> None:
    monkeypatch.setattr(provider.ak, "stock_lhb_jgmmtj_em", lambda **_kwargs: pd.DataFrame([{"x": 1}]))
    with pytest.raises(TaskDeadlineExceeded, match="stop"):
        provider._load_jg_lookups("20260710", cancellation_token=_CancelAfterProviderCall())

    monkeypatch.setattr(
        provider.ak,
        "stock_lhb_jgmmtj_em",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    assert provider._load_jg_lookups("20260710") == ({}, {})


def test_institution_loader_builds_reason_and_compatibility_lookups(monkeypatch) -> None:
    frame = pd.DataFrame(
        [
            {
                "代码": 1,
                "上榜原因": "B｜A",
                "收盘价": 10,
                "涨跌幅": 2,
                "买方机构数": 2,
                "卖方机构数": 1,
                "机构买入总额": 300,
                "机构卖出总额": 100,
                "机构买入净额": 200,
            },
            {
                "代码": "2",
                "上榜原因": "",
                "收盘价": float("nan"),
                "涨跌幅": float("nan"),
                "买方机构数": float("nan"),
                "卖方机构数": float("nan"),
                "机构买入总额": float("nan"),
                "机构卖出总额": float("nan"),
                "机构买入净额": float("nan"),
            },
        ]
    )
    monkeypatch.setattr(provider.ak, "stock_lhb_jgmmtj_em", lambda **_kwargs: frame.copy())

    by_reason, by_code = provider._load_jg_lookups("20260710")

    assert set(by_code) == {"000001", "000002"}
    assert ("000001", "A | B") in by_reason
    assert all(key[0] != "000002" for key in by_reason)
    assert by_code["000002"][0]["机构买入净额"] == 0.0


def test_foreign_presence_maps_support_multiple_names_sides_and_unknown_branches() -> None:
    frame = pd.DataFrame(
        [
            {"营业部名称": "深股通专用", "买入股票": "甲 乙", "卖出股票": "丙"},
            {"营业部名称": "普通营业部", "买入股票": "忽略", "卖出股票": "忽略"},
        ]
    )

    buys, sells = provider._build_foreign_presence_maps(frame)

    assert buys == {"甲": {"深股通"}, "乙": {"深股通"}}
    assert sells == {"丙": {"深股通"}}


def test_foreign_presence_loader_propagates_cancellation_and_handles_none(monkeypatch) -> None:
    monkeypatch.setattr(provider.ak, "stock_lhb_hyyyb_em", lambda **_kwargs: pd.DataFrame([{"x": 1}]))
    with pytest.raises(TaskDeadlineExceeded, match="stop"):
        provider._load_foreign_presence("20260710", cancellation_token=_CancelAfterProviderCall())

    monkeypatch.setattr(provider.ak, "stock_lhb_hyyyb_em", lambda **_kwargs: None)
    assert provider._load_foreign_presence("20260710") == ({}, {})


def test_stock_foreign_detail_loader_combines_surviving_sides(monkeypatch) -> None:
    calls = []

    def detail_api(*, flag, **_kwargs):
        calls.append(flag)
        if flag == "买入":
            return None
        return pd.DataFrame([{"净额": 10000}])

    monkeypatch.setattr(provider.ak, "stock_lhb_stock_detail_em", detail_api)

    assert provider._load_stock_foreign_details("000001", "20260710").to_dict("records") == [{"净额": 10000}]
    assert calls == ["买入", "卖出"]


def test_stock_foreign_detail_loader_propagates_owner_cancellation(monkeypatch) -> None:
    monkeypatch.setattr(provider.ak, "stock_lhb_stock_detail_em", lambda **_kwargs: pd.DataFrame([{"x": 1}]))

    with pytest.raises(TaskDeadlineExceeded, match="stop"):
        provider._load_stock_foreign_details(
            "000001",
            "20260710",
            cancellation_token=_CancelAfterProviderCall(),
        )


def test_foreign_aggregate_cache_avoids_reloading_stock_details(monkeypatch) -> None:
    aggregate_cache = {("000001", "A"): {"深股通": 1.0}}
    monkeypatch.setattr(
        provider,
        "_load_stock_foreign_details",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache must win")),
    )

    assert provider._foreign_details_for_reason(
        "000001",
        "20260710",
        "A",
        {},
        aggregate_cache,
    ) == {"深股通": 1.0}


def test_public_entrypoints_cover_plain_error_success_log_probe_and_pool_delegate(monkeypatch) -> None:
    monkeypatch.setattr(
        provider,
        "_load_lhb_detail_frame",
        lambda *_args, **_kwargs: (pd.DataFrame(), "error", "provider failed"),
    )
    assert provider.fetch_lhb_data_for_date("20260710", return_meta=False) == []

    frame = pd.DataFrame([_detail_row()])
    empty_context = {
        "jg_reason_dict": {},
        "jg_candidates": {},
        "foreign_buys": {},
        "foreign_sells": {},
        "foreign_detail_cache": {},
        "foreign_aggregate_cache": {},
    }
    monkeypatch.setattr(provider, "_load_lhb_detail_frame", lambda *_args, **_kwargs: (frame, "ok", "ok"))
    monkeypatch.setattr(provider, "_load_lhb_enrichment_context", lambda *_args, **_kwargs: empty_context)

    info_messages = []
    monkeypatch.setattr(provider.log, "info", info_messages.append)
    records = provider.fetch_lhb_data_for_date("20260710", strict_filter=False, emit_success_log=True)
    assert len(records) == 1
    assert records[0]["市值"] == "--"
    assert info_messages == ["[龙虎榜抓取] 20260710 成功拉取 1 条数据"]

    assert provider.probe_lhb_detail_count_for_date("20260710") == 1
    assert provider.probe_lhb_detail_count_for_date("20260710", return_meta=True) == {
        "count": 1,
        "status": "ok",
        "message": "ok",
    }

    observed = {}

    def fake_fetch(date_str, **kwargs):
        observed["date_str"] = date_str
        observed.update(kwargs)
        return [{"代码": "000001"}]

    monkeypatch.setattr(provider, "fetch_lhb_data_for_date", fake_fetch)
    assert provider.fetch_lhb_pool_for_date(
        "20260710",
        emit_success_log=False,
        return_meta=True,
        cancellation_token="token",
    ) == [{"代码": "000001"}]
    assert observed == {
        "date_str": "20260710",
        "strict_filter": False,
        "emit_success_log": False,
        "return_meta": True,
        "cancellation_token": "token",
    }


def test_build_records_stops_before_next_row_when_cancelled() -> None:
    class CancelOnSecondCheck:
        def __init__(self) -> None:
            self.calls = 0

        def raise_if_cancelled(self) -> None:
            self.calls += 1
            if self.calls >= 2:
                raise TaskCancelledError("owner shutdown")

    with pytest.raises(TaskCancelledError, match="owner shutdown"):
        provider._build_lhb_records(
            pd.DataFrame([_detail_row(), _detail_row(**{"代码": "2"})]),
            date_str="20260710",
            strict_filter=False,
            context={},
            cancellation_token=CancelOnSecondCheck(),
        )
