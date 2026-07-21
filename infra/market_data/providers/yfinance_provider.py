"""Optional yfinance-backed Asian quote provider."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from infra.market_data.normalize.quote_normalizer import (
    currency_for_ticker,
    first_float,
    first_positive,
    normalize_pe_value,
    resolve_previous_close,
    resolve_quote_date,
)
from infra.tasks.lifecycle import bounded_io_timeout, raise_if_cancelled

_T = TypeVar("_T")
YFinanceCaller = Callable[[Callable[[], _T]], _T]
YahooErrorHandler = Callable[[str, Exception, str], bool]


class LazyYFinanceModule:
    """Keep yfinance outside module import and service construction."""

    def __init__(self) -> None:
        self._module: Any = None

    def __getattr__(self, name: str) -> Any:
        module = self._module
        if module is None:
            module = importlib.import_module("yfinance")
            self._module = module
        return getattr(module, name)


class YFinanceOperationError(RuntimeError):
    """Wrap an arbitrary failure raised by the optional yfinance client."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        self.cause = cause


def fetch_yfinance_realtime_quote(
    code: str,
    yf_session: Any,
    *,
    yf_module: Any,
    cancellation_token: Any = None,
    previous_close_resolver: Callable[..., float | None] = resolve_previous_close,
    quote_date_resolver: Callable[[dict[str, Any] | None, Any], str | None] = resolve_quote_date,
) -> dict[str, Any] | None:
    raise_if_cancelled(cancellation_token)
    ticker = yf_module.Ticker(code, session=yf_session)
    # ``fast_info`` issues its own unbounded history calls in current yfinance.
    # K-line owner tasks therefore use the explicitly bounded history response;
    # the legacy non-cancellable Asian-tab path keeps its richer fast-info fields.
    fast_info: Mapping[str, Any] = ticker.fast_info if cancellation_token is None else {}
    frame = ticker.history(
        period="5d",
        interval="1d",
        timeout=bounded_io_timeout(15, cancellation_token),
    )
    raise_if_cancelled(cancellation_token)
    if frame.empty:
        return None
    close_price = first_positive(fast_info.get("lastPrice"), frame.iloc[-1]["Close"])
    if close_price is None:
        return None
    previous_close = previous_close_resolver(
        realtime_quote={"source": "yfinance"},
        fast_info=fast_info,
        frame=frame,
    )
    if previous_close is None:
        history_index = -2 if len(frame) >= 2 else -1
        previous_close = float(frame.iloc[history_index]["Close"])
    result = {
        "date": quote_date_resolver(None, frame),
        "close": close_price,
        "open": first_positive(fast_info.get("open"), frame.iloc[-1]["Open"]),
        "high": first_positive(fast_info.get("dayHigh"), frame.iloc[-1]["High"]),
        "low": first_positive(fast_info.get("dayLow"), frame.iloc[-1]["Low"]),
        "volume": first_float(fast_info.get("lastVolume"), frame.iloc[-1].get("Volume", 0), 0.0),
        "previous_close": previous_close,
        "currency": fast_info.get("currency", currency_for_ticker(code)),
        "source": "yfinance",
        "quote_quality": "last",
        "df_today": frame,
    }
    raise_if_cancelled(cancellation_token)
    return result


def fetch_yahoo_enrichment(
    code: str,
    yf_session: Any,
    *,
    allow_network: bool,
    yf_module: Any,
    rate_limit_status: Callable[[], Mapping[str, Any]],
    error_handler: YahooErrorHandler,
    call_yfinance: YFinanceCaller[Any],
) -> tuple[dict[str, Any], Any, Any]:
    if not allow_network or rate_limit_status()["active"]:
        return {}, None, None
    ticker = yf_module.Ticker(code, session=yf_session)
    fast_info: dict[str, Any] = {}
    frame = None
    try:
        fast_info = call_yfinance(lambda: dict(ticker.fast_info or {}))
    except YFinanceOperationError as wrapped:
        if error_handler(code, wrapped.cause, "Yahoo fast_info"):
            return fast_info, frame, ticker
    if rate_limit_status()["active"]:
        return fast_info, frame, ticker
    try:
        history_frame = call_yfinance(lambda: ticker.history(period="2mo", interval="1d", timeout=15))
        if not getattr(history_frame, "empty", True):
            frame = history_frame
    except YFinanceOperationError as wrapped:
        error_handler(code, wrapped.cause, "Yahoo history enrichment")
    return fast_info, frame, ticker


def yahoo_pe(
    code: str,
    ticker: Any,
    info_session: Any,
    *,
    yf_module: Any,
    error_handler: YahooErrorHandler,
    call_yfinance: YFinanceCaller[Any],
) -> tuple[float | None, str, bool]:
    try:
        info_ticker = ticker if ticker is not None else yf_module.Ticker(code, session=info_session)
        info = call_yfinance(lambda: info_ticker.info)
        trailing_pe = normalize_pe_value(info.get("trailingPE"))
        if trailing_pe is not None:
            return trailing_pe, "trailing", True
        forward_pe = normalize_pe_value(info.get("forwardPE"))
        if forward_pe is not None:
            return forward_pe, "forward", True
        return None, "", True
    except YFinanceOperationError as wrapped:
        error_handler(code, wrapped.cause, "PE fetch")
        return None, "", False


__all__ = [
    "LazyYFinanceModule",
    "YFinanceOperationError",
    "fetch_yahoo_enrichment",
    "fetch_yfinance_realtime_quote",
    "yahoo_pe",
]
