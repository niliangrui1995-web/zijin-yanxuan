from __future__ import annotations

from collections.abc import Callable

import pandas as pd

from core.f5_activation_gate import f5_snapshot_activation_boundary, f5_snapshot_read_boundary
from core.logger import get_logger
from core.runtime_paths import DATE_FMT, RPS_BUFFER_DAYS
from domains.market_calendar import MarketCalendar

_log = get_logger(__name__)


class RpsService:
    """RPS 预计算与矩阵构建服务。"""

    def __init__(
        self,
        *,
        prices_matrix_builder: Callable | None = None,
        rps_matrix_builder: Callable | None = None,
    ) -> None:
        self._daily_rps_cache: dict = {}
        self._rps_cache_date = None
        self._precomputed_rps_bundle: dict | None = None
        self._prices_matrix_builder = prices_matrix_builder
        self._rps_matrix_builder = rps_matrix_builder

    def set_precomputed_rps(self, cache_date: str, rps120, rps250) -> None:
        with f5_snapshot_activation_boundary():
            self._precomputed_rps_bundle = {
                "date": str(cache_date),
                "rps120": rps120,
                "rps250": rps250,
            }

    def get_precomputed_rps(self) -> dict | None:
        with f5_snapshot_read_boundary():
            return self._precomputed_rps_bundle

    @staticmethod
    def build_prices_matrix(
        data_dict: dict[str, pd.DataFrame],
        min_start: pd.Timestamp,
        end_ts: pd.Timestamp | None = None,
        *,
        prices_matrix_builder: Callable | None = None,
    ) -> pd.DataFrame:
        if prices_matrix_builder is not None:
            try:
                matrix, cols, dates = prices_matrix_builder(data_dict, min_start, end_ts)
                if len(cols) > 0:
                    return pd.DataFrame(matrix, index=dates, columns=cols)
            except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                _log.error(f"[策略中台] 加速矩阵构建失败，回退 pandas: {exc}")

        valid = [(code, df) for code, df in data_dict.items() if df is not None and not df.empty]
        if not valid:
            return pd.DataFrame()

        series_list = []
        for code, df in valid:
            try:
                mask = df.index >= min_start
                if end_ts is not None:
                    mask = mask & (df.index <= end_ts)
                sliced = df.loc[mask, "close"]
                if not sliced.empty:
                    series_list.append(sliced.rename(code))
            except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
                _log.info(f"[策略中台] 构建 RPS 时忽略 {code}，原因: {exc}")
        if not series_list:
            return pd.DataFrame()
        prices = pd.concat(series_list, axis=1).sort_index()
        return prices.ffill(limit=5)

    def _build_accelerated_rps(self, data_dict, start_date: str, end_date: str) -> dict | None:
        if self._rps_matrix_builder is None:
            return None
        try:
            return self._rps_matrix_builder(data_dict, start_date, end_date, self._daily_rps_cache) or None
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            _log.error(f"[策略中台] Polars RPS 计算失败，回退 pandas: {exc}")
            return None

    def build_rps_matrix(
        self,
        data_dict: dict[str, pd.DataFrame],
        start_date: str,
        end_date: str,
    ) -> dict:
        num_stocks = len(data_dict)
        today = MarketCalendar.today("CN")
        if self._rps_cache_date != today:
            self._daily_rps_cache = {}
            self._rps_cache_date = today

        cache_key = (str(start_date), str(end_date))
        if cache_key in self._daily_rps_cache:
            _log.warning(f"\n[策略中台] RPS 矩阵命中缓存 (区间 {start_date} ~ {end_date})，跳过重算")
            return self._daily_rps_cache[cache_key]

        accelerated = self._build_accelerated_rps(data_dict, start_date, end_date)
        if accelerated:
            return accelerated

        _log.info(f"\n[策略中台] 正在计算全市场 RPS 强度矩阵... (标的数: {num_stocks})")
        start_ts = pd.to_datetime(start_date)
        end_ts = pd.to_datetime(end_date)
        min_start = start_ts - pd.Timedelta(days=RPS_BUFFER_DAYS)

        prices = self.build_prices_matrix(
            data_dict,
            min_start,
            end_ts,
            prices_matrix_builder=self._prices_matrix_builder,
        )
        if prices.empty:
            _log.warning(f"[策略中台] ⚠ 区间 {start_date} ~ {end_date} 无可用价格数据，跳过该段 RPS 计算。")
            return {}

        rps50 = prices.pct_change(50).rank(axis=1, pct=True) * 100
        rps120 = prices.pct_change(120).rank(axis=1, pct=True) * 100
        rps250 = prices.pct_change(250).rank(axis=1, pct=True) * 100
        target_dates = prices.loc[start_date:end_date].index

        if len(target_dates) == 0 and not prices.empty:
            target_dates = prices.index[-1:]

        result = {}
        for trade_date in target_dates:
            r50_d = rps50.loc[trade_date]
            r120_d = rps120.loc[trade_date]
            r250_d = rps250.loc[trade_date]
            valid = r120_d.notna() & r250_d.notna()
            result[trade_date.strftime(DATE_FMT)] = {
                "rps50": r50_d[valid].to_dict(),
                "rps120": r120_d[valid].to_dict(),
                "rps250": r250_d[valid].to_dict(),
            }

        _log.info(f"[策略中台] RPS 矩阵构建完成 — 参与标的 {prices.shape[1]} 只 | 扫描交易日 {len(target_dates)} 个")
        self._daily_rps_cache[cache_key] = result
        return result
