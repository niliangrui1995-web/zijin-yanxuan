# -*- coding: utf-8 -*-

from __future__ import annotations

import datetime
import socket
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from infra.market_data import asian_kline_provider as kline_provider
from infra.market_data import asian_market_http as market_http
from infra.market_data import asian_quote_provider as provider

ASIAN_MARKET_PROVIDER_URLS = (
    ("finance.naver.com", "https://finance.naver.com/item/main.naver?code=005930"),
    ("finance.yahoo.co.jp", "https://finance.yahoo.co.jp/quote/7203.T"),
    ("kabutan.jp", "https://kabutan.jp/stock/?code=7203"),
    ("mis.twse.com.tw", "https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_2330.tw&json=1"),
    ("polling.finance.naver.com", "https://polling.finance.naver.com/api/realtime/domestic/stock/005930"),
    ("qt.gtimg.cn", "https://qt.gtimg.cn/q=hk00700"),
    ("www.tpex.org.tw", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"),
    ("www.twse.com.tw", "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d?response=json"),
)


@pytest.fixture
def public_dns_resolution(monkeypatch):
    """Keep FakeSession transport tests independent of live provider DNS."""
    import infra.http_safety as http_safety

    def resolve_public_ip(_host, port, family=0, type=0, proto=0, flags=0):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", int(port)),
            )
        ]

    monkeypatch.setattr(http_safety.socket, "getaddrinfo", resolve_public_ip)


class _FakeResponse:
    def __init__(
        self,
        *,
        text: str = "",
        data: object = None,
        status_code: object = 200,
        content: object = None,
        json_error: Exception | None = None,
    ) -> None:
        self.text = text
        self._data = data
        self.status_code = status_code
        self.content = content
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._data


class _FakeSession:
    def __init__(self, *results: object) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        result = self._results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _history(rows: list[dict[str, float]], dates: list[str] | None = None) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if dates is not None:
        frame.index = pd.to_datetime(dates)
    return frame


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        (float("nan"), None),
        ("--", None),
        ("not numeric", None),
        ("￥1,234.50%", 1234.5),
        ("-7.25原", -7.25),
    ],
)
def test_numeric_normalization_handles_markers_noise_and_nan(raw, expected):
    assert provider.to_float(raw) == expected


def test_small_normalizers_cover_empty_and_fallback_paths():
    assert provider._text(None) == ""
    assert provider._ticker_base(" 2330.TW ") == "2330"
    assert provider._ticker_suffix("2330.tw") == "TW"
    assert provider._first_present(None, "", 0) == 0
    assert provider._first_present(None, "") is None
    assert provider._first_float("bad", "2.5") == 2.5
    assert provider._first_float("bad", None) is None
    assert provider._positive_float(0) is None
    assert provider._first_positive(-1, "3.5") == 3.5
    assert provider._first_positive(-1, None) is None
    assert provider._first_mapping_item([]) == {}
    assert provider._first_mapping_item(["bad"]) == {}
    assert provider._first_mapping_item([{"ok": True}]) == {"ok": True}
    assert provider._mapping([]) == {}
    assert provider.normalize_pe_value(-5) is None
    assert provider.pe_result("15.2", "source") == (15.2, "source")
    assert provider.pe_result("--", "source") == (None, "")
    assert provider.round_pct(None) == 0.0
    assert provider.strip_html_text("<b>A&amp;B</b>\n C") == "A&B C"
    assert provider.first_book_price("bad_0_12.5_13") == 12.5
    assert provider.first_book_price("bad_0") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("2026-07-12", "2026-07-12"),
        ("2026/07/12", "2026-07-12"),
        ("2026-07-12 09:30:00", "2026-07-12"),
        ("2026/07/12 09:30:00", "2026-07-12"),
        ("20260712", "2026-07-12"),
        ("2026-07-12T09:30:00+0900", "2026-07-12"),
        ("2026-07-12T09:30:00.123456+0900", "2026-07-12"),
        ("2026-07-12T09:30:00", "2026-07-12"),
        ("2026-07-12T09:30:00.123456", "2026-07-12"),
        ("not-a-date", None),
    ],
)
def test_trade_date_normalization_supports_all_declared_formats(raw, expected):
    assert provider.normalize_trade_date(raw) == expected


def test_history_previous_close_handles_empty_single_timezone_and_quote_gap():
    assert provider.history_previous_close(None) is None
    one = _history([{"Close": 11.0}], ["2026-07-10"])
    assert provider.history_previous_close(one) == 11.0

    two = _history([{"Close": 10.0}, {"Close": 12.0}], ["2026-07-10", "2026-07-11"])
    two.index = two.index.tz_localize("Asia/Tokyo")
    assert provider.history_previous_close(two, "2026-07-12") == 12.0
    assert provider.history_previous_close(two, "2026-07-11") == 10.0


def test_previous_close_resolution_exercises_direct_candidates_and_history_fallback():
    frame = _history([{"Close": 8.0}, {"Close": 9.0}], ["2026-07-10", "2026-07-11"])
    assert (
        provider.resolve_previous_close(
            realtime_quote={"source": "twse_mis", "previous_close": 7.5},
            fast_info={},
            frame=None,
        )
        == 7.5
    )
    assert (
        provider.resolve_previous_close(
            realtime_quote={"source": "yfinance", "previous_close": 7.5},
            fast_info={"regularMarketPreviousClose": 8.5},
            frame=frame,
        )
        == 8.5
    )
    assert provider.resolve_previous_close(realtime_quote={}, fast_info={}, frame=None) is None
    one = _history([{"Close": 9.0}], ["2026-07-11"])
    assert provider.resolve_previous_close(realtime_quote={}, fast_info={}, frame=one) == 9.0


def test_daily_and_historical_fields_use_frame_cache_and_defaults():
    frame = _history(
        [
            {"Close": 8.0, "Open": 7.5},
            {"Close": 10.0, "Open": 9.5},
            {"Close": 12.0, "Open": 11.5},
        ]
    )
    assert (
        provider.resolve_daily_field(
            realtime_quote={"close": 0},
            fast_info={"last": None},
            frame=frame,
            quote_key="close",
            fast_info_key="last",
            history_column="Close",
        )
        == 12.0
    )
    assert (
        provider.resolve_daily_field(
            realtime_quote={},
            fast_info={},
            frame=None,
            quote_key="close",
            fast_info_key="last",
            history_column="Close",
            default=3.0,
        )
        == 3.0
    )
    assert (
        provider.resolve_previous_close_value(
            close_price=12.0,
            realtime_quote={},
            fast_info={},
            frame=frame,
            cached_payload={},
        )
        == 10.0
    )
    assert (
        provider.resolve_previous_close_value(
            close_price=12.0,
            realtime_quote={},
            fast_info={},
            frame=None,
            cached_payload={"previous_close": 11.0},
        )
        == 11.0
    )
    assert provider.past_pct_from_history(12.0, None, {"pct_5": 4.0}, 5) == 4.0
    nonpositive = _history([{"Close": 0.0}, {"Close": 12.0}])
    assert provider.past_pct_from_history(12.0, nonpositive, {"pct_1": 5.0}, 1) == 5.0
    assert provider.past_pct_from_history(12.0, frame, {}, 1) == pytest.approx(20.0)


def test_quote_date_and_cooldown_formatting_cover_frame_fallbacks():
    frame = _history([{"Close": 1.0}], ["2026-07-11"])
    frame.index = frame.index.tz_localize("Asia/Tokyo")
    assert provider.resolve_quote_date({"date": "2026-07-12"}, frame) == "2026-07-12"
    assert provider.resolve_quote_date(None, None) is None
    assert provider.resolve_quote_date(None, frame) == "2026-07-11"
    assert provider.format_cooldown_eta(1.2) == "1 秒"
    assert provider.format_cooldown_eta(61) == "2 分钟"


class _NoLength:
    def __len__(self):
        raise TypeError("no length")


def test_response_body_and_json_validation_distinguish_empty_bad_and_wrong_shape():
    assert provider.response_body_is_blank(_FakeResponse(content=b""))
    assert not provider.response_body_is_blank(_FakeResponse(content=b"x"))
    assert provider.response_body_is_blank(_FakeResponse(text=" ", content=_NoLength()))
    assert not provider.response_body_is_blank(SimpleNamespace())

    with pytest.raises(provider.AsianRealtimePayloadError, match="empty body"):
        provider.load_realtime_json(
            _FakeResponse(text="", content=b"", json_error=ValueError("bad")),
            source="fake",
        )
    with pytest.raises(provider.AsianRealtimePayloadError, match="bad JSON"):
        provider.load_realtime_json(
            _FakeResponse(text="not-json", content=b"not-json", json_error=ValueError("bad")),
            source="fake",
        )
    with pytest.raises(provider.AsianRealtimePayloadError, match="unexpected JSON payload: empty"):
        provider.load_realtime_json(_FakeResponse(data=None), source="fake")
    with pytest.raises(provider.AsianRealtimePayloadError, match="unexpected JSON payload: list"):
        provider.load_realtime_json(_FakeResponse(data=[]), source="fake")
    assert provider.load_realtime_json(_FakeResponse(data={"ok": True}), source="fake") == {"ok": True}


@pytest.mark.parametrize(
    ("info", "previous_close", "expected"),
    [
        ({"z": "10"}, None, (10.0, "last")),
        ({"pz": "11"}, None, (11.0, "match")),
        ({"a": "12_13"}, None, (12.0, "ask_only")),
        ({"b": "9_8"}, None, (9.0, "bid_only")),
        ({"b": "9", "a": "11"}, None, (10.0, "indicative_mid")),
        ({"o": "8"}, None, (8.0, "open_fallback")),
        ({}, 7.0, (7.0, "prev_close_fallback")),
        ({}, None, (None, "missing")),
    ],
)
def test_twse_price_selection_covers_order_book_and_fallback_quality(info, previous_close, expected):
    assert provider.pick_twse_price(info, previous_close) == expected


def test_tw_realtime_provider_handles_empty_and_valid_payloads(public_dns_resolution):
    assert provider.fetch_tw_realtime_quote("", object()) is None
    empty = _FakeSession(_FakeResponse(data={"msgArray": []}))
    assert provider.fetch_tw_realtime_quote("2330.TW", empty) is None
    missing_price = _FakeSession(_FakeResponse(data={"msgArray": [{"z": "--", "y": "--"}]}))
    assert provider.fetch_tw_realtime_quote("2330.TW", missing_price) is None

    valid = _FakeSession(
        _FakeResponse(
            data={
                "msgArray": [
                    {
                        "d": "20260712",
                        "z": "102.5",
                        "o": "100",
                        "h": "103",
                        "l": "99",
                        "v": "1,234",
                        "y": "98",
                    }
                ]
            }
        )
    )
    quote = provider.fetch_tw_realtime_quote("6488.TWO", valid)
    assert quote == {
        "date": "2026-07-12",
        "close": 102.5,
        "open": 100.0,
        "high": 103.0,
        "low": 99.0,
        "volume": 1234.0,
        "previous_close": 98.0,
        "currency": "TWD",
        "source": "twse_mis",
        "quote_quality": "last",
    }
    assert "ex_ch=otc_6488.tw" in valid.calls[0][0]


@pytest.mark.parametrize(
    ("info", "close_price", "expected"),
    [
        ({"compareToPreviousClosePriceRaw": "5", "fluctuationsRatioRaw": "2"}, 105.0, 100.0),
        ({"compareToPreviousClosePriceRaw": "5", "fluctuationsRatioRaw": "-2"}, 95.0, 100.0),
        ({"compareToPreviousClosePriceRaw": "5", "compareToPreviousPrice": {"code": "4"}}, 95.0, 100.0),
        ({"compareToPreviousClosePriceRaw": "5", "compareToPreviousPrice": {"code": "2"}}, 105.0, 100.0),
        ({"compareToPreviousClosePriceRaw": "-5"}, 95.0, 100.0),
        ({}, 100.0, None),
        ({"fluctuationsRatioRaw": "-100"}, 100.0, None),
        ({"fluctuationsRatioRaw": "25"}, 100.0, 80.0),
    ],
)
def test_kr_previous_close_resolves_difference_direction_and_ratio(info, close_price, expected):
    assert provider._kr_previous_close(info, close_price) == expected


def test_kr_realtime_provider_handles_empty_missing_and_valid_payloads(public_dns_resolution):
    assert provider.fetch_kr_realtime_quote("", object()) is None
    assert provider.fetch_kr_realtime_quote("005930.KS", _FakeSession(_FakeResponse(data={}))) is None
    missing = _FakeSession(_FakeResponse(data={"datas": [{"closePrice": "--"}]}))
    assert provider.fetch_kr_realtime_quote("005930.KS", missing) is None
    valid = _FakeSession(
        _FakeResponse(
            data={
                "datas": [
                    {
                        "localTradedAt": "2026-07-12T15:30:00+0900",
                        "closePrice": "80,000",
                        "openPrice": "79,000",
                        "highPrice": "81,000",
                        "lowPrice": "78,000",
                        "accumulatedTradingVolume": "12,345",
                        "compareToPreviousClosePrice": "1,000",
                        "fluctuationsRatio": "1.27",
                        "currencyType": {"code": "KRW"},
                    }
                ]
            }
        )
    )
    quote = provider.fetch_kr_realtime_quote("005930.KS", valid)
    assert quote is not None
    assert quote["close"] == 80000.0
    assert quote["previous_close"] == 79000.0
    assert quote["currency"] == "KRW"


def test_hk_realtime_provider_covers_content_decode_and_invalid_shapes(public_dns_resolution):
    assert provider.fetch_hk_realtime_quote("", object()) is None
    assert provider.fetch_hk_realtime_quote("0522.HK", _FakeSession(_FakeResponse(text="bad"))) is None

    missing_fields = [""] * 10
    missing = _FakeSession(_FakeResponse(text=f'v_hk00522="{"~".join(missing_fields)}";'))
    assert provider.fetch_hk_realtime_quote("0522.HK", missing) is None

    fields = [""] * 40
    fields[3] = "20.5"
    fields[4] = "20"
    fields[5] = "19.5"
    fields[6] = "1,500"
    fields[30] = "2026/07/12 16:00:00"
    fields[33] = "21"
    fields[34] = "19"
    content = f'v_hk00522="{"~".join(fields)}";'.encode("gb18030")
    valid = _FakeSession(_FakeResponse(content=content))
    quote = provider.fetch_hk_realtime_quote("522.HK", valid)
    assert quote is not None
    assert quote["date"] == "2026-07-12"
    assert quote["close"] == 20.5
    assert valid.calls[0][0].endswith("hk00522")


def _jp_indicator_page() -> str:
    return r'''
    <span class="CommonPriceBoard__price_x"><span class="StyledNumber__value_x">5,731</span></span>
    "previousPrice":{"value":"5,689","updateDateMeta":"2026-07-11"}
    "openPrice":{"value":"5,700","updateDateMeta":"2026-07-12T09:00:00+0900"}
    "highPrice":{"value":"5,800","updateDateMeta":"2026-07-12T10:00:00+0900"}
    "lowPrice":{"value":"5,600","updateDateMeta":"2026-07-12T11:00:00+0900"}
    "volume":{"value":"123,456","updateDateMeta":"2026-07-12T12:00:00+0900"}
    '''


def test_jp_page_parsers_cover_indicator_and_preloaded_shapes(monkeypatch):
    assert provider.extract_jp_page_price("missing") is None
    assert provider.extract_jp_indicator_value("missing", "openPrice") == (None, None)
    assert provider.latest_normalized_date(None, "bad") is None
    assert provider.latest_normalized_date("2026-07-10", "2026-07-12") == "2026-07-12"
    assert provider._jp_preloaded_quote({}) is None
    assert provider._parse_jp_preloaded_page("missing") is None
    assert provider._parse_jp_preloaded_page("__PRELOADED_STATE__ = {}") is None

    monkeypatch.setattr(
        provider.MarketCalendar,
        "now",
        classmethod(lambda cls, market="CN": datetime.datetime(2026, 7, 12, tzinfo=datetime.UTC)),
    )
    preloaded = {
        "mainStocksPriceBoard": {"priceBoard": {"price": "5731"}},
        "mainStocksDetail": {
            "detail": {
                "openPrice": "5700",
                "highPrice": "5800",
                "lowPrice": "5600",
                "volume": "123456",
                "previousPrice": "5689",
            }
        },
        "pageInfo": {"currentDateTime": 1783785600000},
    }
    page = f'<script>__PRELOADED_STATE__ = {provider.json.dumps(preloaded)}</script>'
    quote = provider.parse_jp_realtime_page(page)
    assert quote is not None
    assert quote["close"] == 5731.0
    assert quote["date"]

    indicator_quote = provider.parse_jp_realtime_page(_jp_indicator_page())
    assert indicator_quote is not None
    assert indicator_quote["date"] == "2026-07-12"
    assert indicator_quote["volume"] == 123456.0
    assert provider._parse_jp_indicator_page("missing") is None


def test_fetch_jp_realtime_quote_covers_no_code_no_quote_and_retry(monkeypatch, public_dns_resolution):
    assert provider.fetch_jp_realtime_quote("", object()) is None
    no_quote = _FakeSession(_FakeResponse(text="normal page", status_code=200))
    assert provider.fetch_jp_realtime_quote("5201.T", no_quote) is None

    responses = iter(
        [
            _FakeResponse(text="現在表示できません", status_code=503),
            _FakeResponse(text=_jp_indicator_page(), status_code=200),
        ]
    )
    monkeypatch.setattr(provider, "asian_market_get", lambda *args, **kwargs: next(responses))
    quote = provider.fetch_jp_realtime_quote("5201.T", object())
    assert quote is not None
    assert quote["source"] == "yj_finance_page"

    responses = iter(
        [
            _FakeResponse(text="現在表示できません", status_code=503),
            _FakeResponse(text="still bad", status_code=500),
        ]
    )
    monkeypatch.setattr(provider, "asian_market_get", lambda *args, **kwargs: next(responses))
    assert provider.fetch_jp_realtime_quote("5201.T", object()) is None


def test_yfinance_realtime_quote_handles_empty_missing_and_history_fallback(monkeypatch):
    class _Ticker:
        def __init__(self, frame, fast_info):
            self._frame = frame
            self.fast_info = fast_info

        def history(self, **kwargs):
            return self._frame

    class _Yf:
        def __init__(self, ticker):
            self._ticker = ticker

        def Ticker(self, code, session=None):
            return self._ticker

    empty = _Ticker(pd.DataFrame(), {})
    assert provider.fetch_yfinance_realtime_quote("5201.T", object(), yf_module=_Yf(empty)) is None
    zero = _history([{"Open": 0.0, "High": 0.0, "Low": 0.0, "Close": 0.0, "Volume": 0.0}])
    assert provider.fetch_yfinance_realtime_quote("5201.T", object(), yf_module=_Yf(_Ticker(zero, {}))) is None

    frame = _history(
        [
            {"Open": 9.0, "High": 11.0, "Low": 8.0, "Close": 10.0, "Volume": 100.0},
            {"Open": 10.0, "High": 13.0, "Low": 9.0, "Close": 12.0, "Volume": 200.0},
        ],
        ["2026-07-10", "2026-07-11"],
    )
    monkeypatch.setattr(provider, "resolve_previous_close", lambda **kwargs: None)
    quote = provider.fetch_yfinance_realtime_quote(
        "5201.T",
        object(),
        yf_module=_Yf(_Ticker(frame, {"lastPrice": 12.0, "currency": "JPY"})),
    )
    assert quote is not None
    assert quote["previous_close"] == 10.0
    assert quote["open"] == 10.0


def test_market_pe_parsers_cover_invalid_shapes_and_matches(monkeypatch):
    assert provider._find_twse_pe([["short"]], code_index=0, pe_index=2, base_code="2330") == (None, "")
    assert provider._find_twse_pe([["9999", "x", "5"]], code_index=0, pe_index=2, base_code="2330") == (
        None,
        "",
    )
    assert provider._find_twse_pe([["2330", "x", "28.5"]], code_index=0, pe_index=2, base_code="2330") == (
        28.5,
        "twse_per",
    )
    assert provider.fetch_twse_pe("", object()) == (None, "")

    monkeypatch.setattr(provider, "asian_market_get", lambda *args, **kwargs: _FakeResponse(data={}))
    assert provider.fetch_twse_pe("2330.TW", object()) == (None, "")
    monkeypatch.setattr(
        provider,
        "asian_market_get",
        lambda *args, **kwargs: _FakeResponse(data={"fields": ["x"], "data": [["x"]]}),
    )
    assert provider.fetch_twse_pe("2330.TW", object()) == (None, "")

    assert provider.fetch_tpex_pe("", object()) == (None, "")
    monkeypatch.setattr(provider, "asian_market_get", lambda *args, **kwargs: _FakeResponse(data={}))
    assert provider.fetch_tpex_pe("6488.TWO", object()) == (None, "")
    monkeypatch.setattr(
        provider,
        "asian_market_get",
        lambda *args, **kwargs: _FakeResponse(
            data=["bad", {"SecuritiesCompanyCode": "9999"}, {"SecuritiesCompanyCode": "6488", "PriceEarningRatio": "21.5"}]
        ),
    )
    assert provider.fetch_tpex_pe("6488.TWO", object()) == (21.5, "tpex_per")

    assert provider.fetch_kr_naver_pe("", object()) == (None, "")
    monkeypatch.setattr(provider, "asian_market_get", lambda *args, **kwargs: _FakeResponse(text="missing"))
    assert provider.fetch_kr_naver_pe("005930.KS", object()) == (None, "")
    monkeypatch.setattr(
        provider,
        "asian_market_get",
        lambda *args, **kwargs: _FakeResponse(text='<em id="_per"><span>18.4</span></em>'),
    )
    assert provider.fetch_kr_naver_pe("005930.KS", object()) == (18.4, "naver_per")


def test_japan_pe_parsers_and_fetchers_cover_primary_retry_and_kabutan(monkeypatch):
    assert provider.parse_jp_yahoo_pe_from_html('{"per":{"value":"17.5"}}') == (17.5, "yahoo_jp_per")
    item_html = "<dt><span>PER</span></dt><dd><b>22.1</b></dd>"
    assert provider.parse_jp_yahoo_pe_from_html(item_html) == (22.1, "yahoo_jp_per")
    assert provider.parse_jp_yahoo_pe_from_html("missing") == (None, "")

    monkeypatch.setattr(provider, "asian_market_get", lambda *args, **kwargs: _FakeResponse(status_code=500))
    assert provider.fetch_jp_kabutan_pe("5201") == (None, "")
    monkeypatch.setattr(
        provider,
        "asian_market_get",
        lambda *args, **kwargs: _FakeResponse(text="<table><tr><th>PER</th><td>24.3</td></tr></table>"),
    )
    assert provider.fetch_jp_kabutan_pe("5201") == (24.3, "kabutan_per")
    monkeypatch.setattr(provider, "asian_market_get", lambda *args, **kwargs: _FakeResponse(text="missing"))
    assert provider.fetch_jp_kabutan_pe("5201") == (None, "")

    assert provider.fetch_jp_yahoo_pe("", object()) == (None, "")
    monkeypatch.setattr(
        provider,
        "asian_market_get",
        lambda *args, **kwargs: _FakeResponse(text='{"per":{"value":"19.2"}}'),
    )
    assert provider.fetch_jp_yahoo_pe("5201.T", object()) == (19.2, "yahoo_jp_per")

    fallback_calls: list[str] = []

    def fallback(base):
        fallback_calls.append(base)
        return 25.0, "fallback"

    monkeypatch.setattr(provider, "asian_market_get", lambda *args, **kwargs: _FakeResponse(text="normal"))
    normal_no_pe = _FakeSession(_FakeResponse(text="normal", status_code=200))
    assert provider.fetch_jp_yahoo_pe("5201.T", normal_no_pe, kabutan_fetcher=fallback) == (25.0, "fallback")
    assert fallback_calls == ["5201"]

    responses = iter(
        [
            _FakeResponse(text="error", status_code=500),
            _FakeResponse(text='{"per":{"value":"20.5"}}', status_code=200),
        ]
    )
    monkeypatch.setattr(provider, "asian_market_get", lambda *args, **kwargs: next(responses))
    assert provider.fetch_jp_yahoo_pe("5201.T", object(), kabutan_fetcher=fallback) == (20.5, "yahoo_jp_per")


@pytest.mark.parametrize(
    ("code", "active", "expected_key"),
    [
        ("2330.TW", False, "TW"),
        ("6488.TWO", False, "TWO"),
        ("005930.KS", False, "KS"),
        ("5201.T", False, "T"),
        ("5201.T", True, "T_KABUTAN"),
        ("0522.HK", False, None),
    ],
)
def test_pe_fallback_dispatches_by_suffix_and_yahoo_cooldown(code, active, expected_key):
    calls: list[str] = []

    def _fetch(key):
        def _inner(*args):
            calls.append(key)
            return 1.0, key

        return _inner

    fetchers = {key: _fetch(key) for key in ("TW", "TWO", "KS", "T", "T_KABUTAN")}
    result = provider._dispatch_asian_pe_fallback(
        code,
        object(),
        fetchers=fetchers,
        rate_limit_status=lambda: {"active": active},
    )
    if expected_key is None:
        assert result == (None, "")
        assert calls == []
    else:
        assert result == (1.0, expected_key)
        assert calls == [expected_key]


def test_public_pe_fallback_rejects_invalid_input_and_contains_provider_errors():
    assert provider.fetch_asian_pe_fallback("", object()) == (None, "")
    assert provider.fetch_asian_pe_fallback("2330", object()) == (None, "")
    assert provider.fetch_asian_pe_fallback("2330.TW", None) == (None, "")

    def _broken(*args):
        raise ValueError("bad payload")

    assert provider.fetch_asian_pe_fallback("2330.TW", object(), twse_fetcher=_broken) == (None, "")


def test_direct_and_yfinance_safe_wrappers_cover_success_known_and_unknown_errors():
    marker = object()
    assert (
        provider._direct_quote(
            "0522.HK",
            marker,
            tw_fetcher=lambda *args: None,
            hk_fetcher=lambda code, session: {"code": code, "same": session is marker},
            kr_fetcher=lambda *args: None,
            jp_fetcher=lambda *args: None,
        )
        == {"code": "0522.HK", "same": True}
    )
    assert (
        provider._direct_quote(
            "BAD.US",
            marker,
            tw_fetcher=lambda *args: None,
            hk_fetcher=lambda *args: None,
            kr_fetcher=lambda *args: None,
            jp_fetcher=lambda *args: None,
        )
        is None
    )

    fetchers = {
        "tw_fetcher": lambda *args: (_ for _ in ()).throw(ValueError("known")),
        "hk_fetcher": lambda *args: None,
        "kr_fetcher": lambda *args: None,
        "jp_fetcher": lambda *args: None,
    }
    assert provider._safe_direct_quote("2330.TW", marker, **fetchers) is None
    fetchers["tw_fetcher"] = lambda *args: (_ for _ in ()).throw(provider.AsianRealtimePayloadError("payload"))
    with pytest.raises(provider.AsianRealtimePayloadError):
        provider._safe_direct_quote("2330.TW", marker, **fetchers)

    assert (
        provider._safe_yfinance_quote(
            "2330.TW",
            marker,
            yf_module=object(),
            yf_fetcher=lambda *args, **kwargs: {"close": 1},
            rate_limit_error=lambda exc: False,
            mark_rate_limited=lambda exc: 0,
        )
        == {"close": 1}
    )
    marked: list[Exception] = []
    assert (
        provider._safe_yfinance_quote(
            "2330.TW",
            marker,
            yf_module=object(),
            yf_fetcher=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("rate")),
            rate_limit_error=lambda exc: True,
            mark_rate_limited=lambda exc: marked.append(exc) or 61,
        )
        is None
    )
    assert len(marked) == 1
    assert (
        provider._safe_yfinance_quote(
            "2330.TW",
            marker,
            yf_module=object(),
            yf_fetcher=lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("known")),
            rate_limit_error=lambda exc: False,
            mark_rate_limited=lambda exc: 0,
        )
        is None
    )
    with pytest.raises(provider.YFinanceOperationError):
        provider._safe_yfinance_quote(
            "2330.TW",
            marker,
            yf_module=object(),
            yf_fetcher=lambda *args, **kwargs: (_ for _ in ()).throw(Exception("unknown")),
            rate_limit_error=lambda exc: False,
            mark_rate_limited=lambda exc: 0,
        )


def test_public_realtime_quote_controls_payload_errors_cooldown_and_yfinance_fallback():
    noops = {
        "tw_fetcher": lambda *args: None,
        "hk_fetcher": lambda *args: None,
        "kr_fetcher": lambda *args: None,
        "jp_fetcher": lambda *args: None,
    }
    assert provider.fetch_asian_realtime_quote("invalid", yf_session=object(), **noops) is None
    assert (
        provider.fetch_asian_realtime_quote(
            "2330.TW",
            yf_session=object(),
            allow_yfinance_fallback=False,
            tw_fetcher=lambda *args: {"close": 10},
            hk_fetcher=lambda *args: None,
            kr_fetcher=lambda *args: None,
            jp_fetcher=lambda *args: None,
        )
        == {"close": 10}
    )
    assert (
        provider.fetch_asian_realtime_quote(
            "2330.TW",
            yf_session=object(),
            rate_limit_status=lambda: {"active": True, "remaining_sec": 12},
            **noops,
        )
        is None
    )
    assert (
        provider.fetch_asian_realtime_quote(
            "2330.TW",
            yf_session=object(),
            rate_limit_status=lambda: {"active": False, "remaining_sec": 0},
            yf_fetcher=lambda *args, **kwargs: {"close": 11},
            **noops,
        )
        == {"close": 11}
    )

    payload_fetchers = {**noops, "tw_fetcher": lambda *args: (_ for _ in ()).throw(provider.AsianRealtimePayloadError("bad"))}
    assert provider.fetch_asian_realtime_quote("2330.TW", yf_session=object(), **payload_fetchers) is None
    with pytest.raises(provider.AsianRealtimePayloadError):
        provider.fetch_asian_realtime_quote(
            "2330.TW",
            yf_session=object(),
            raise_on_source_payload_error=True,
            **payload_fetchers,
        )


def test_optional_yahoo_error_handler_covers_rate_limit_known_and_unknown_errors():
    marked: list[Exception] = []
    assert provider.handle_optional_yahoo_error(
        "2330.TW",
        RuntimeError("rate"),
        "test",
        rate_limit_error=lambda exc: True,
        mark_rate_limited=lambda exc: marked.append(exc) or 120,
    )
    assert len(marked) == 1
    assert not provider.handle_optional_yahoo_error(
        "2330.TW",
        ValueError("known"),
        "test",
        rate_limit_error=lambda exc: False,
    )
    with pytest.raises(LookupError):
        provider.handle_optional_yahoo_error(
            "2330.TW",
            LookupError("unknown"),
            "test",
            rate_limit_error=lambda exc: False,
        )


class _YfTicker:
    def __init__(self, *, fast_info: object = None, frame: object = None, info: object = None) -> None:
        self._fast_info = fast_info
        self._frame = frame
        self._info = info

    @property
    def fast_info(self):
        if isinstance(self._fast_info, BaseException):
            raise self._fast_info
        return self._fast_info

    @property
    def info(self):
        if isinstance(self._info, BaseException):
            raise self._info
        return self._info

    def history(self, **kwargs):
        if isinstance(self._frame, BaseException):
            raise self._frame
        return self._frame


class _YfModule:
    def __init__(self, ticker: _YfTicker) -> None:
        self.ticker = ticker

    def Ticker(self, code, session=None):
        return self.ticker


def test_yahoo_enrichment_covers_disabled_success_fast_info_failure_and_history_failure():
    assert provider.fetch_yahoo_enrichment(
        "2330.TW",
        object(),
        allow_network=False,
        yf_module=_YfModule(_YfTicker()),
    ) == ({}, None, None)
    assert provider.fetch_yahoo_enrichment(
        "2330.TW",
        object(),
        rate_limit_status=lambda: {"active": True},
        yf_module=_YfModule(_YfTicker()),
    ) == ({}, None, None)

    frame = _history([{"Close": 10.0}])
    ticker = _YfTicker(fast_info={"currency": "TWD"}, frame=frame)
    fast_info, history, returned_ticker = provider.fetch_yahoo_enrichment(
        "2330.TW",
        object(),
        rate_limit_status=lambda: {"active": False},
        yf_module=_YfModule(ticker),
    )
    assert fast_info == {"currency": "TWD"}
    assert history is frame
    assert returned_ticker is ticker

    errors: list[str] = []
    ticker = _YfTicker(fast_info=ValueError("fast"), frame=ValueError("history"))
    result = provider.fetch_yahoo_enrichment(
        "2330.TW",
        object(),
        rate_limit_status=lambda: {"active": False},
        yf_module=_YfModule(ticker),
        error_handler=lambda code, exc, context: errors.append(context) or False,
    )
    assert result == ({}, None, ticker)
    assert errors == ["Yahoo fast_info", "Yahoo history enrichment"]

    ticker = _YfTicker(fast_info=ValueError("fast"), frame=frame)
    result = provider.fetch_yahoo_enrichment(
        "2330.TW",
        object(),
        rate_limit_status=lambda: {"active": False},
        yf_module=_YfModule(ticker),
        error_handler=lambda *args: True,
    )
    assert result == ({}, None, ticker)


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"trailingPE": "18.5"}, (18.5, "trailing", True)),
        ({"trailingPE": "--", "forwardPE": "20.5"}, (20.5, "forward", True)),
        ({}, (None, "", True)),
    ],
)
def test_yahoo_pe_selects_trailing_forward_or_empty(info, expected):
    ticker = _YfTicker(info=info)
    assert provider._yahoo_pe(
        "2330.TW",
        ticker,
        object(),
        yf_module=_YfModule(ticker),
        error_handler=lambda *args: False,
    ) == expected


def test_yahoo_pe_contains_client_failure():
    contexts: list[str] = []
    ticker = _YfTicker(info=ValueError("broken"))
    assert provider._yahoo_pe(
        "2330.TW",
        ticker,
        object(),
        yf_module=_YfModule(ticker),
        error_handler=lambda code, exc, context: contexts.append(context) or False,
    ) == (None, "", False)
    assert contexts == ["PE fetch"]


def test_pe_refresh_covers_fresh_quote_time_yahoo_and_fallback_paths(monkeypatch):
    monkeypatch.setattr(provider.MarketCalendar, "infer_market", classmethod(lambda cls, code: "TW"))
    monkeypatch.setattr(provider.MarketCalendar, "normalize_market", classmethod(lambda cls, market: market))
    current = 100_000.0
    assert provider.refresh_pe_if_needed(
        "2330.TW",
        ticker=None,
        info_session=object(),
        pe_value=10.0,
        pe_source="cache",
        pe_updated_at=current - 1,
        now=lambda: current,
    ) == (10.0, "cache", current - 1)
    assert provider.refresh_pe_if_needed(
        "2330.TW",
        ticker=None,
        info_session=object(),
        pe_value=10.0,
        pe_source="cache",
        pe_updated_at=0,
        quote_refresh_time=lambda market: True,
        now=lambda: current,
    ) == (10.0, "cache", 0)

    ticker = _YfTicker(info={"trailingPE": 18.0})
    assert provider.refresh_pe_if_needed(
        "2330.TW",
        ticker=ticker,
        info_session=object(),
        pe_value=None,
        pe_source="",
        pe_updated_at=0,
        yf_module=_YfModule(ticker),
        rate_limit_status=lambda: {"active": False},
        quote_refresh_time=lambda market: False,
        now=lambda: current,
    ) == (18.0, "trailing", current)

    empty_ticker = _YfTicker(info={})
    assert provider.refresh_pe_if_needed(
        "2330.TW",
        ticker=empty_ticker,
        info_session=object(),
        pe_value=10.0,
        pe_source="cache",
        pe_updated_at=0,
        yf_module=_YfModule(empty_ticker),
        rate_limit_status=lambda: {"active": False},
        quote_refresh_time=lambda market: False,
        fallback_fetcher=lambda *args: (22.0, "market"),
        now=lambda: current,
    ) == (22.0, "market", current)

    assert provider.refresh_pe_if_needed(
        "2330.TW",
        ticker=None,
        info_session=object(),
        pe_value=10.0,
        pe_source="cache",
        pe_updated_at=0,
        rate_limit_status=lambda: {"active": True},
        quote_refresh_time=lambda market: False,
        fallback_fetcher=lambda *args: (None, ""),
        now=lambda: current,
    ) == (10.0, "cache", current)


def test_quote_source_composition_handles_direct_error_and_enrichment_replacement():
    errors: list[str] = []
    result = provider._fetch_quote_sources(
        "2330.TW",
        object(),
        allow_optional_network=True,
        realtime_fetcher=lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("direct")),
        enrichment_fetcher=lambda *args, **kwargs: ({"currency": "TWD"}, "history", "ticker"),
        error_handler=lambda code, exc, context: errors.append(context) or False,
    )
    assert result == (None, {"currency": "TWD"}, "history", "ticker")
    assert errors == ["single quote fallback"]

    with pytest.raises(provider.AsianRealtimePayloadError):
        provider._fetch_quote_sources(
            "2330.TW",
            object(),
            allow_optional_network=True,
            realtime_fetcher=lambda *args, **kwargs: (_ for _ in ()).throw(provider.AsianRealtimePayloadError("bad")),
            enrichment_fetcher=lambda *args, **kwargs: ({}, None, None),
            error_handler=lambda *args: False,
        )

    direct = {"source": "twse_mis", "close": 10.0, "df_today": "direct-frame"}
    assert provider._fetch_quote_sources(
        "2330.TW",
        object(),
        allow_optional_network=True,
        realtime_fetcher=lambda *args, **kwargs: direct,
        enrichment_fetcher=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("not needed")),
        error_handler=lambda *args: False,
    ) == (direct, {}, "direct-frame", None)


def test_snapshot_and_complete_payload_cover_missing_and_cached_history_paths(monkeypatch):
    assert provider._daily_ohlc(None, {}, None) is None
    quote = {
        "date": "2026-07-12",
        "close": 12.0,
        "open": 11.0,
        "high": 13.0,
        "low": 10.0,
        "volume": 123.0,
        "previous_close": 10.0,
        "currency": "TWD",
        "source": "twse_mis",
        "quote_quality": "last",
    }
    snapshot = provider._price_snapshot(quote, {}, None, {})
    assert snapshot is not None
    assert snapshot["pct"] == pytest.approx(20.0)
    payload = provider._complete_payload(
        snapshot,
        realtime_quote=quote,
        fast_info={},
        frame=None,
        cached_payload={"pct_5": 5.0, "pct_10": 10.0, "pct_20": 20.0},
        pe_value=18.0,
        pe_source="market",
        pe_updated_at=123.0,
    )
    assert payload["pct_5"] == 5.0
    assert payload["pe"] == 18.0
    assert payload["source"] == "twse_mis"

    values = iter([12.0, None, None, None])
    monkeypatch.setattr(provider, "resolve_daily_field", lambda **kwargs: next(values))
    assert provider._daily_ohlc({}, {}, None) == (12.0, 12.0, 12.0, 12.0)


def test_normalized_quote_returns_none_without_price_and_builds_complete_payload():
    assert provider.fetch_normalized_asian_quote(
        "2330.TW",
        yf_session=object(),
        info_session=object(),
        cached_payload={},
        allow_optional_network=False,
        realtime_fetcher=lambda *args, **kwargs: None,
        enrichment_fetcher=lambda *args, **kwargs: ({}, None, None),
        pe_refresher=lambda *args, **kwargs: (None, "", 0),
    ) is None

    quote = {
        "date": "2026-07-12",
        "close": 12.0,
        "open": 11.0,
        "high": 13.0,
        "low": 10.0,
        "volume": 123.0,
        "previous_close": 10.0,
        "currency": "TWD",
        "source": "twse_mis",
        "quote_quality": "last",
    }
    captured: dict[str, object] = {}

    def _refresh(code, **kwargs):
        captured.update(kwargs)
        return 19.0, "market", 456.0

    payload = provider.fetch_normalized_asian_quote(
        "2330.TW",
        yf_session=object(),
        info_session=object(),
        cached_payload={"pe": 18.0, "pe_source": "cache", "pe_updated_at": "123"},
        allow_optional_network=False,
        realtime_fetcher=lambda *args, **kwargs: quote,
        enrichment_fetcher=lambda *args, **kwargs: ({}, None, None),
        pe_refresher=_refresh,
    )
    assert payload is not None
    assert payload["pe"] == 19.0
    assert payload["pe_updated_at"] == 456.0
    assert captured["pe_value"] == 18.0


def test_asian_http_transport_merges_headers_parses_status_and_retries(monkeypatch):
    assert market_http.asian_market_headers()["User-Agent"]
    assert market_http.asian_market_headers({"Accept-Language": "ja"})["Accept-Language"] == "ja"
    assert market_http.response_status_code(SimpleNamespace(status_code="201")) == 201
    assert market_http.response_status_code(SimpleNamespace(status_code="bad"), default=204) == 204
    assert market_http.is_http_success(SimpleNamespace(status_code=399))
    assert not market_http.is_http_success(SimpleNamespace(status_code=400))

    response = object()
    calls: list[tuple[str, dict[str, object]]] = []

    def _flaky(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            raise requests.RequestException("temporary")
        return response

    monkeypatch.setattr(market_http, "requests_get_https", _flaky)
    assert market_http.asian_market_get("https://finance.yahoo.co.jp/quote/7203.T", retries=1) is response
    assert len(calls) == 2
    assert all(kwargs["allowed_hosts"] == market_http.ASIAN_MARKET_ALLOWED_HOSTS for _, kwargs in calls)

    monkeypatch.setattr(
        market_http,
        "requests_get_https",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.RequestException("down")),
    )
    with pytest.raises(requests.RequestException, match="down"):
        market_http.asian_market_get("https://finance.yahoo.co.jp/quote/7203.T", retries=2)


def test_asian_http_transport_allowlist_matches_provider_urls():
    assert market_http.ASIAN_MARKET_ALLOWED_HOSTS == frozenset(host for host, _ in ASIAN_MARKET_PROVIDER_URLS)


@pytest.mark.parametrize("_host, url", ASIAN_MARKET_PROVIDER_URLS)
def test_asian_http_transport_allows_known_provider_host(_host, url, public_dns_resolution):
    response = object()
    session = _FakeSession(response)

    assert market_http.asian_market_get(url, session=session) is response
    assert session.calls == [
        (
            url,
            {
                "headers": market_http.ASIAN_MARKET_HTTP_HEADERS,
                "timeout": market_http.ASIAN_MARKET_HTTP_TIMEOUT_SEC,
                "allow_redirects": False,
            },
        )
    ]


def test_asian_http_transport_rejects_unknown_host_before_request():
    session = _FakeSession(object())

    with pytest.raises(ValueError, match="HTTPS host is not allowed"):
        market_http.asian_market_get("https://example.com/quote", session=session)

    assert session.calls == []


def test_asian_http_transport_rejects_cross_host_redirect_to_unknown_host(public_dns_resolution):
    class _RedirectResponse:
        status_code = 302
        headers = {"Location": "https://evil.example/next"}

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    redirect = _RedirectResponse()
    session = _FakeSession(redirect)

    with pytest.raises(ValueError, match="HTTPS host is not allowed"):
        market_http.asian_market_get("https://finance.yahoo.co.jp/quote/7203.T", session=session)

    assert redirect.closed is True
    assert len(session.calls) == 1


def test_asian_kline_adapter_forwards_session_and_cancellation_budget(monkeypatch):
    single_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        kline_provider._legacy_fetcher,
        "fetch_single_kline",
        lambda *args, **kwargs: single_calls.append((args, kwargs)) or "rows",
    )
    session = object()
    assert kline_provider.fetch_single_kline("name", "2330.TW", period="6mo", session=session) == "rows"
    assert single_calls == [(('name', '2330.TW'), {"period": "6mo", "session": session})]

    class _Token:
        def raise_if_cancelled(self):
            return None

        def remaining_seconds(self):
            return 12.0

    sync_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        kline_provider._legacy_fetcher,
        "sync_asian_kline_cache",
        lambda **kwargs: sync_calls.append(kwargs) or {"ok": True},
    )
    assert kline_provider.sync_asian_kline_cache(time_budget_sec=20, cancellation_token=_Token()) == {"ok": True}
    assert sync_calls[0]["time_budget_sec"] == 12.0
    assert callable(sync_calls[0]["cancellation_checkpoint"])
