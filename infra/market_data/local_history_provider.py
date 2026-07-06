from __future__ import annotations

import datetime

import pandas as pd

from core.logger import get_logger
from core.runtime_paths import INCREMENTAL_BARS, MAX_HISTORY_BARS
from domains.market_calendar import MarketCalendar
from domains.scan.indicator_service import IndicatorService

_log = get_logger(__name__)


class LocalHistoryProvider:
    """本地历史数据与图表补全服务。"""

    def __init__(self, provider, *, logger=None) -> None:
        self.provider = provider
        self._log = logger or _log

    def fetch_standard_data(self, api, code, count=MAX_HISTORY_BARS):
        try:
            import polars as pl
        except ImportError:
            pl = None

        provider = self.provider
        market = provider._get_market_code(code)
        if provider.tdx_vipdoc:
            local_df = provider._fetch_from_local_tdx(code)
            if local_df is not None and len(local_df) >= 250:
                try:
                    local_df = provider._apply_forward_adjustment(api, market, code, local_df)
                    if "vol" in local_df.columns:
                        if isinstance(local_df, pd.DataFrame):
                            local_df = local_df.rename(columns={"vol": "volume"})
                        else:
                            local_df = local_df.rename({"vol": "volume"})
                    return local_df
                except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
                    self._log.error(f"[数据中台] 本地 {code} 复权失败，改用网络: {exc}")
            elif local_df is not None and len(local_df) < 250:
                self._log.info(f"[缓存] 本地日线 {code}: 共 {len(local_df)} 条")
            elif provider.tdx_vipdoc and (local_df is None or len(local_df) == 0):
                self._log.error(f"[数据中台] 本地日线 {code} 异常，改用网络")

        for _ in range(2):
            try:
                data = api.get_security_bars(9, market, code, 0, count)
                if data and len(data) > 0:
                    df = pl.DataFrame(data) if pl is not None else pd.DataFrame(data)
                    if pl is not None and "datetime" in df.columns and df.schema["datetime"] != pl.Date:
                        df = df.with_columns(
                            pl.col("datetime").str.strptime(pl.Datetime, "%Y-%m-%d %H:%M", strict=False).cast(pl.Date)
                        ).sort("datetime", descending=False)
                    if pl is not None and isinstance(df, pl.DataFrame):
                        pdf = df.to_pandas()
                    elif isinstance(df, pd.DataFrame):
                        pdf = df
                    else:
                        pdf = pd.DataFrame(df)
                    if "datetime" in pdf.columns:
                        pdf = pdf.set_index("datetime")
                    df = provider._apply_forward_adjustment(api, market, code, pdf)
                    if "vol" in df.columns:
                        df = df.rename(columns={"vol": "volume"})
                    return df
            except ValueError:
                raise
            except (ConnectionError, KeyError, OSError, RuntimeError, TimeoutError, TypeError) as exc:
                self._log.error(f"[数据中台] 拉取 {code} 历史数据失败: {exc}")
        return None

    def get_data_fresh_for_chart(self, code, force_sync: bool = False):
        provider = self.provider
        existing_df = provider.get_data(code)
        if not force_sync and provider._is_before_930_today():
            return existing_df
        if not force_sync and provider._is_after_1500_today() and existing_df is not None and len(existing_df) > 0:
            try:
                latest_ts = pd.Timestamp(existing_df.index.max())
                latest_date = latest_ts.date() if not pd.isna(latest_ts) else None
                if isinstance(latest_date, datetime.date) and latest_date >= MarketCalendar.today("CN"):
                    return existing_df
            except (TypeError, ValueError) as exc:
                self._log.debug(f"[K线 {code}] 缓存日期检查异常: {exc}")
        if not provider.server_pool:
            return existing_df

        api = provider._get_thread_api()
        try:
            if existing_df is not None and len(existing_df) >= 250:
                new = self.fetch_standard_data(api, code, count=INCREMENTAL_BARS)
                if new is not None and len(new) > 0:
                    try:
                        import polars as pl
                    except ImportError:
                        pl = None
                    if pl is not None and isinstance(new, pl.DataFrame):
                        new = new.to_pandas()
                        if "datetime" in new.columns:
                            new = new.set_index("datetime")

                    last_existing = existing_df.index.max()
                    first_new = new.index.min()
                    gap_days = (first_new - last_existing).days
                    if gap_days > 10:
                        full_df = self.fetch_standard_data(api, code, count=MAX_HISTORY_BARS)
                        if full_df is not None and len(full_df) >= 250:
                            full_df = IndicatorService.calculate_indicators(full_df)
                            with provider.cache_lock:
                                provider.cache_data[code] = full_df
                            return full_df
                    combined = pd.concat([existing_df, new])
                    merged = combined[~combined.index.duplicated(keep="last")].iloc[-MAX_HISTORY_BARS:]
                    merged = IndicatorService.calculate_indicators(merged)
                    with provider.cache_lock:
                        provider.cache_data[code] = merged
                    return merged
            else:
                full_df = self.fetch_standard_data(api, code, count=MAX_HISTORY_BARS)
                if full_df is not None and len(full_df) >= 250:
                    full_df = IndicatorService.calculate_indicators(full_df)
                    with provider.cache_lock:
                        provider.cache_data[code] = full_df
                    return full_df
        except (TimeoutError, OSError, ConnectionError) as exc:
            self._log.error(f"[K线 {code}] 联网补全失败(网络层)，继续使用缓存: {exc}")
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            self._log.error(f"[K线 {code}] 联网补全失败(数据层)，继续使用缓存: {exc}")
        return existing_df
