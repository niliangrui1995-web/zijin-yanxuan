from __future__ import annotations

import datetime
from typing import cast

import pandas as pd

from core.logger import get_logger
from core.runtime_paths import INCREMENTAL_BARS, MAX_HISTORY_BARS, MIN_HISTORY_BARS
from domains.market_calendar import MarketCalendar
from infra.tasks.lifecycle import (
    TaskCancelledError,
    TaskDeadlineExceeded,
    bounded_io_timeout,
    raise_if_cancelled,
)
from infra.tasks.owner_lifecycle import invoke_with_cancellation

_log = get_logger(__name__)


class _LazyIndicatorService:
    @staticmethod
    def calculate_indicators(frame):
        from domains.scan.indicator_service import IndicatorService as Service

        return Service.calculate_indicators(frame)


IndicatorService = _LazyIndicatorService


def _calculate_indicators(frame):
    return IndicatorService.calculate_indicators(frame)


def _pandas_history_frame(frame) -> pd.DataFrame:
    if isinstance(frame, pd.DataFrame):
        return frame
    converter = getattr(frame, "to_pandas", None)
    converted = converter() if callable(converter) else frame
    if isinstance(converted, pd.DataFrame) and "datetime" in converted.columns:
        converted = converted.set_index("datetime")
    return cast(pd.DataFrame, converted)


def _cache_chart_frame(provider, code: str, frame, cancellation_token=None):
    raise_if_cancelled(cancellation_token)
    with provider.cache_lock:
        provider.cache_data[code] = frame
    raise_if_cancelled(cancellation_token)
    return frame


def _rename_volume(frame):
    if "vol" not in frame.columns:
        return frame
    if isinstance(frame, pd.DataFrame):
        return frame.rename(columns={"vol": "volume"})
    return frame.rename({"vol": "volume"})


def _network_history_frame(data, polars_module):
    if polars_module is None:
        frame = pd.DataFrame(data)
    else:
        frame = polars_module.DataFrame(data)
        if "datetime" in frame.columns and frame.schema["datetime"] != polars_module.Date:
            frame = frame.with_columns(
                polars_module.col("datetime")
                .str.strptime(polars_module.Datetime, "%Y-%m-%d %H:%M", strict=False)
                .cast(polars_module.Date)
            ).sort("datetime", descending=False)
        frame = frame.to_pandas()
    if "datetime" in frame.columns:
        frame = frame.set_index("datetime")
    return frame


def _get_security_bars_with_deadline(api, *args, cancellation_token=None):
    """Apply the owner deadline to the pytdx socket used by this blocking read."""
    raise_if_cancelled(cancellation_token)
    client = getattr(api, "client", None)
    set_timeout = getattr(client, "settimeout", None)
    get_timeout = getattr(client, "gettimeout", None)
    previous_timeout = get_timeout() if callable(get_timeout) else None
    timeout_changed = callable(set_timeout)
    if timeout_changed:
        set_timeout(bounded_io_timeout(5, cancellation_token))
    try:
        result = api.get_security_bars(*args)
    finally:
        if timeout_changed and previous_timeout is not None:
            set_timeout(previous_timeout)
    raise_if_cancelled(cancellation_token)
    return result


class LocalHistoryProvider:
    """本地历史数据与图表补全服务。"""

    def __init__(self, provider, *, logger=None) -> None:
        self.provider = provider
        self._log = logger or _log

    def _load_local_history(self, api, market, code, cancellation_token):
        provider = self.provider
        local_df = invoke_with_cancellation(provider._fetch_from_local_tdx, cancellation_token, code)
        if local_df is None or len(local_df) == 0:
            self._log.error(f"[数据中台] 本地日线 {code} 异常，改用网络")
            return None
        if len(local_df) < MIN_HISTORY_BARS:
            self._log.info(f"[缓存] 本地日线 {code}: 共 {len(local_df)} 条")
            return None
        try:
            adjusted = invoke_with_cancellation(
                provider._apply_forward_adjustment,
                cancellation_token,
                api,
                market,
                code,
                local_df,
            )
            return _rename_volume(adjusted)
        except (TaskCancelledError, TaskDeadlineExceeded):
            raise
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
            self._log.error(f"[数据中台] 本地 {code} 复权失败，改用网络: {exc}")
            return None

    def _fetch_network_history(self, api, market, code, count, polars_module, cancellation_token):
        data = _get_security_bars_with_deadline(
            api,
            9,
            market,
            code,
            0,
            count,
            cancellation_token=cancellation_token,
        )
        if not data:
            return None
        frame = _network_history_frame(data, polars_module)
        adjusted = invoke_with_cancellation(
            self.provider._apply_forward_adjustment,
            cancellation_token,
            api,
            market,
            code,
            frame,
        )
        return _rename_volume(adjusted)

    def fetch_standard_data(self, api, code, count=MAX_HISTORY_BARS, *, cancellation_token=None):
        raise_if_cancelled(cancellation_token)
        try:
            import polars as pl
        except ImportError:
            pl = None

        provider = self.provider
        market = provider._get_market_code(code)
        if provider.tdx_vipdoc:
            local_df = self._load_local_history(api, market, code, cancellation_token)
            if local_df is not None:
                return local_df

        for _ in range(2):
            raise_if_cancelled(cancellation_token)
            try:
                frame = self._fetch_network_history(api, market, code, count, pl, cancellation_token)
                if frame is not None:
                    return frame
            except ValueError:
                raise
            except (TaskCancelledError, TaskDeadlineExceeded):
                raise
            except (ConnectionError, KeyError, OSError, RuntimeError, TimeoutError, TypeError) as exc:
                self._log.error(f"[数据中台] 拉取 {code} 历史数据失败: {exc}")
        raise_if_cancelled(cancellation_token)
        return None

    def _refresh_existing_chart_data(self, api, code, existing_df, cancellation_token):
        new = invoke_with_cancellation(
            self.fetch_standard_data,
            cancellation_token,
            api,
            code,
            count=INCREMENTAL_BARS,
        )
        if new is None or len(new) == 0:
            return None
        new = _pandas_history_frame(new)
        existing_df = _pandas_history_frame(existing_df)
        first_new = cast(pd.Timestamp, new.index.min())
        last_existing = cast(pd.Timestamp, existing_df.index.max())
        gap_days = (first_new - last_existing).days
        if gap_days > 10:
            full_df = invoke_with_cancellation(
                self.fetch_standard_data,
                cancellation_token,
                api,
                code,
                count=MAX_HISTORY_BARS,
            )
            if full_df is not None and len(full_df) >= MIN_HISTORY_BARS:
                return _cache_chart_frame(self.provider, code, full_df, cancellation_token)
            return None
        combined = pd.concat([existing_df, new])
        merged = combined[~combined.index.duplicated(keep="last")].iloc[-MAX_HISTORY_BARS:]
        return _cache_chart_frame(self.provider, code, merged, cancellation_token)

    def _refresh_full_chart_data(self, api, code, cancellation_token):
        full_df = invoke_with_cancellation(
            self.fetch_standard_data,
            cancellation_token,
            api,
            code,
            count=MAX_HISTORY_BARS,
        )
        if full_df is None or len(full_df) < MIN_HISTORY_BARS:
            return None
        return _cache_chart_frame(self.provider, code, full_df, cancellation_token)

    def get_data_fresh_for_chart(self, code, force_sync: bool = False, *, cancellation_token=None):
        provider = self.provider
        existing_df = invoke_with_cancellation(provider.get_data, cancellation_token, code)
        if not force_sync and provider._is_before_930_today():
            raise_if_cancelled(cancellation_token)
            return existing_df
        if not force_sync and provider._is_after_1500_today() and existing_df is not None and len(existing_df) > 0:
            try:
                latest_ts = pd.Timestamp(existing_df.index.max())
                latest_date = latest_ts.date() if not pd.isna(latest_ts) else None
                if isinstance(latest_date, datetime.date) and latest_date >= MarketCalendar.today("CN"):
                    raise_if_cancelled(cancellation_token)
                    return existing_df
            except (TypeError, ValueError) as exc:
                self._log.debug(f"[K线 {code}] 缓存日期检查异常: {exc}")
        if not provider.server_pool:
            raise_if_cancelled(cancellation_token)
            return existing_df

        api = invoke_with_cancellation(provider._get_thread_api, cancellation_token)
        try:
            if existing_df is not None and len(existing_df) >= MIN_HISTORY_BARS:
                refreshed = self._refresh_existing_chart_data(api, code, existing_df, cancellation_token)
            else:
                refreshed = self._refresh_full_chart_data(api, code, cancellation_token)
            if refreshed is not None:
                return refreshed
        except (TaskCancelledError, TaskDeadlineExceeded):
            raise
        except (TimeoutError, OSError, ConnectionError) as exc:
            self._log.error(f"[K线 {code}] 联网补全失败(网络层)，继续使用缓存: {exc}")
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            self._log.error(f"[K线 {code}] 联网补全失败(数据层)，继续使用缓存: {exc}")
        raise_if_cancelled(cancellation_token)
        return existing_df
