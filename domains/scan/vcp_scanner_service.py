from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import polars as pl

from core.logger import get_logger
from domains.scan.indicator_service import IndicatorService
from vcp.constants import (
    EXCLUDE_DAYS_FOR_PEAKS,
    FLEXIBLE_MAX_INTERVAL,
    FLEXIBLE_MIN_INTERVAL,
    GROUP_DAYS,
    LOOKBACK_DAYS,
    MAX_PEAKS_COUNT,
    MAX_R2_BELOW_R1_PCT,
    MERGE_WITHIN_DAYS,
    MIN_DAYS_AFTER_LAST_PEAK,
    MIN_DAYS_AFTER_LAST_PEAK_CONFIRM,
    MIN_FIRST_TO_THIRD_DAYS,
    MIN_PEAKS_COUNT,
    MIN_R1_R2_DAYS,
    MIN_SMA50_SLOPE,
    PCT_BASELINE,
    PEAKS_FROM_GROUPS,
)
from vcp.models import VCPParams

_log = get_logger(__name__)


class VcpScannerService:
    """VCP 规则评估服务。"""

    @staticmethod
    def calculate_flexible_peaks(
        pldf: pl.DataFrame,
        curr_idx: int,
        params: VCPParams,
    ) -> tuple[list | None, str]:
        search_start = max(0, curr_idx - (LOOKBACK_DAYS - 1))
        if curr_idx - EXCLUDE_DAYS_FOR_PEAKS <= search_start:
            return None, "最近3日不参与算峰，需更多历史数据"

        window_len = (curr_idx - EXCLUDE_DAYS_FOR_PEAKS) - search_start
        if window_len < 20:
            return None, "可用于计算峰的数据不足"

        window_peak = pldf.slice(search_start, window_len)
        n_groups = (window_len + GROUP_DAYS - 1) // GROUP_DAYS
        group_peaks = []
        closes = window_peak.get_column("close").to_list()

        for group_idx in range(n_groups):
            start = group_idx * GROUP_DAYS
            end = min(start + GROUP_DAYS, window_len)
            if start >= end:
                continue
            sub_closes = closes[start:end]
            pos_in_window = int(np.argmax(sub_closes))
            idx_df = search_start + start + pos_in_window
            close_value = float(sub_closes[pos_in_window])
            group_peaks.append((idx_df, close_value))

        if len(group_peaks) < MIN_PEAKS_COUNT:
            return None, f"分组后不足{MIN_PEAKS_COUNT}个峰"

        group_peaks.sort(key=lambda item: item[1], reverse=True)
        baseline = max(closes)
        if baseline <= 0:
            return None, "无有效收盘价"

        top_peaks = group_peaks[: min(PEAKS_FROM_GROUPS, len(group_peaks))]
        top_by_date = sorted(top_peaks, key=lambda item: item[0])
        passed_93 = [(idx, price) for idx, price in top_by_date if price >= PCT_BASELINE * baseline]

        if len(passed_93) < MIN_PEAKS_COUNT:
            return None, f"93%基准下不足{MIN_PEAKS_COUNT}个峰"

        passed_93.sort(key=lambda item: item[0])
        while True:
            merged = False
            for idx in range(len(passed_93) - 1):
                if passed_93[idx + 1][0] - passed_93[idx][0] < MERGE_WITHIN_DAYS:
                    if passed_93[idx][1] >= passed_93[idx + 1][1]:
                        passed_93.pop(idx + 1)
                    else:
                        passed_93.pop(idx)
                    merged = True
                    break
            if not merged:
                break

        final_peaks = passed_93

        if len(final_peaks) < MIN_PEAKS_COUNT or len(final_peaks) > MAX_PEAKS_COUNT:
            return None, f"峰数量{len(final_peaks)}不在允许范围[{MIN_PEAKS_COUNT}-{MAX_PEAKS_COUNT}]"

        peak_idx = final_peaks[0][0]
        interval_days = curr_idx - peak_idx + 1
        if interval_days < FLEXIBLE_MIN_INTERVAL or interval_days > FLEXIBLE_MAX_INTERVAL:
            return None, f"区间长度{interval_days}日不在允许范围[{FLEXIBLE_MIN_INTERVAL}-{FLEXIBLE_MAX_INTERVAL}]"

        last_peak_idx = final_peaks[-1][0]
        if last_peak_idx - peak_idx + 1 < 20:
            return None, "峰之间时间跨度不足"

        return final_peaks, "OK"

    @staticmethod
    def check_ma_slope(pldf: pl.DataFrame, curr_idx: int, params: VCPParams) -> tuple[bool, float]:
        if not params.enable_ma_slope:
            return True, 0
        if curr_idx < 5:
            return False, 0

        sma50_current = pldf.row(curr_idx, named=True)["SMA50"]
        sma50_prev = pldf.row(curr_idx - 5, named=True)["SMA50"]

        if sma50_current is None or sma50_prev is None or sma50_prev == 0:
            return False, 0

        slope = (sma50_current - sma50_prev) / sma50_prev / 5
        if slope < MIN_SMA50_SLOPE:
            return False, slope
        return True, slope

    @staticmethod
    def evaluate_conditions(
        df: pd.DataFrame | pl.DataFrame,
        current_day,
        rps120: float,
        rps250: float,
        _rps_history: dict | None = None,
        params: VCPParams | None = None,
        skip_red_check: bool = False,
    ) -> tuple[bool, str, dict]:
        if params is None:
            params = VCPParams()

        is_pandas = isinstance(df, pd.DataFrame)
        if is_pandas:
            if df.index.name == "datetime":
                pldf = pl.from_pandas(df.reset_index())
            else:
                pldf = pl.from_pandas(df)
        else:
            pldf = df

        cd_date = current_day.date() if hasattr(current_day, "date") else current_day

        try:
            idx_query = pldf.with_row_index().filter(pl.col("datetime").cast(pl.Date) == cd_date).select("index")
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            _log.debug(f"[策略中台] 日期类型转换异常，回退字符串匹配: {exc}")
            idx_query = (
                pldf.with_row_index()
                .filter(pl.col("datetime").cast(pl.Utf8).str.starts_with(str(cd_date)))
                .select("index")
            )

        if idx_query.height == 0:
            return False, "非交易日", {}
        curr_idx = idx_query.item()

        if pd.isna(rps120) or pd.isna(rps250):
            return False, "RPS数据不足", {}

        if "entangle" not in pldf.columns:
            pldf = IndicatorService.calculate_indicators(pldf, include_chart=False)

        row = pldf.row(curr_idx, named=True)

        sma200_ok = row["SMA200"] is not None and not pd.isna(row["SMA200"])
        if curr_idx < params.min_history_days - 1:
            return False, f"数据不足{params.min_history_days}天", {}
        if params.min_history_days >= 200 and not sma200_ok:
            return False, "SMA200数据不足", {}

        sma50_val = row.get("SMA50")
        sma150_val = row.get("SMA150")
        if sma50_val is None or pd.isna(sma50_val) or sma150_val is None or pd.isna(sma150_val):
            return False, "均线数据不足(缺失SMA50或SMA150)", {}

        sma_bull = bool(sma50_val > sma150_val)
        close_above_sma200 = bool(sma200_ok and row["close"] > row["SMA200"])
        if not sma_bull or (sma200_ok and not close_above_sma200):
            return False, "均线非多头", {}

        if not skip_red_check and row["close"] <= row["open"]:
            return False, "当天K线非红盘", {}

        amount_mean = pldf.slice(max(0, curr_idx - 19), curr_idx + 1 - max(0, curr_idx - 19)).get_column("amount").mean()
        if amount_mean is None or pd.isna(amount_mean) or amount_mean < params.min_amount_20d:
            return False, "日均流水不足", {}

        entangle_min = pldf.slice(max(0, curr_idx - 4), curr_idx + 1 - max(0, curr_idx - 4)).get_column("entangle").min()
        if entangle_min is None or pd.isna(entangle_min) or entangle_min > params.ma_bind_threshold:
            return False, "短期均线不粘合", {}

        if rps250 < params.rps_threshold:
            return False, f"长线动量未达标(RPS250:{rps250:.0f} < {params.rps_threshold})", {}
        if rps250 < 90 and rps250 < rps120:
            return False, f"短线背离长线(RPS250:{rps250:.0f} < RPS120:{rps120:.0f})", {}

        ma_slope_ok, ma_slope_val = VcpScannerService.check_ma_slope(pldf, curr_idx, params)
        if not ma_slope_ok:
            return False, f"50日均线斜率不足(当前{ma_slope_val:.4f} < {MIN_SMA50_SLOPE})", {}

        final_peaks, msg = VcpScannerService.calculate_flexible_peaks(pldf, curr_idx, params)
        if final_peaks is None:
            return False, msg, {}

        peak_idx = final_peaks[0][0]
        last_peak_idx = final_peaks[-1][0]

        if curr_idx < last_peak_idx + MIN_DAYS_AFTER_LAST_PEAK:
            return False, f"买入点须在最后一峰之后{MIN_DAYS_AFTER_LAST_PEAK}个交易日", {}

        left_zone = pldf.slice(peak_idx, last_peak_idx + 1 - peak_idx)
        buy_zone = pldf.slice(last_peak_idx + 1, curr_idx + 1 - (last_peak_idx + 1))
        box_low = left_zone.get_column("low").min()
        box_high = left_zone.get_column("high").max()
        left_amp = (box_high - box_low) / box_low if box_low > 0 else 0

        buy_low = buy_zone.get_column("low").min()
        if buy_low and buy_low > 0:
            buy_high = buy_zone.get_column("high").max()
            buy_amp = (buy_high - buy_low) / buy_low
            if buy_amp >= left_amp:
                return False, "买入区振幅未小于左侧区振幅", {}

        if left_amp > params.amp_threshold:
            return False, f"左侧区振幅超限({left_amp:.1%} > {params.amp_threshold:.0%})", {}

        high_250 = row.get("High_250")
        if high_250 is None or pd.isna(high_250) or high_250 <= 0:
            return False, "无有效一年高点", {}

        peak_high = pldf.row(peak_idx, named=True)["high"]
        if 1 - (peak_high / high_250) > params.high_250_threshold:
            return False, "偏离一年高点超限", {}

        if row["close"] <= box_low * 1.05:
            return False, "贴近箱底(<5%)", {}

        prior_250_start = max(0, peak_idx - 250)
        if prior_250_start < peak_idx:
            prior_250_max = pldf.slice(prior_250_start, peak_idx - prior_250_start).get_column("high").max()
            if prior_250_max and prior_250_max > 0 and peak_high < prior_250_max * 0.92:
                deviation = (1 - peak_high / prior_250_max) * 100
                return False, f"第一高点非前250日相对高点(偏离{deviation:.1f}%)", {}

        if len(final_peaks) >= 3:
            first_to_third_days = final_peaks[2][0] - final_peaks[0][0] + 1
            if first_to_third_days < MIN_FIRST_TO_THIRD_DAYS:
                return False, f"第一峰到第三峰不足{MIN_FIRST_TO_THIRD_DAYS}日(当前{first_to_third_days}日)", {}

        h2_idx = final_peaks[1][0] if len(final_peaks) >= 2 else peak_idx
        h3_idx = final_peaks[2][0] if len(final_peaks) >= 3 else h2_idx

        s1 = pldf.slice(peak_idx, h2_idx + 1 - peak_idx)
        s2 = pldf.slice(h2_idx, h3_idx + 1 - h2_idx)
        r1_len = h2_idx - peak_idx + 1
        r2_len = h3_idx - h2_idx + 1
        if len(final_peaks) >= 3 and (r1_len + r2_len) <= MIN_R1_R2_DAYS:
            return False, f"R1+R2须大于{MIN_R1_R2_DAYS}个交易日(当前{r1_len + r2_len}日)", {}

        if len(final_peaks) >= 3:
            r1_low = float(s1.get_column("low").min() or 0)
            r2_low = float(s2.get_column("low").min() or 0)
            if r1_low > 0 and r2_low < r1_low:
                drop_pct = (r1_low - r2_low) / r1_low
                if drop_pct > MAX_R2_BELOW_R1_PCT:
                    return False, f"R2最低点相对R1最低点跌幅超15%(当前{drop_pct:.1%})", {}
            if r1_low > r2_low and box_low > 0:
                mid_left = (box_high + box_low) / 2
                if r1_low > mid_left:
                    return False, f"R1最低价高于R2时须在左侧区间下50%内(当前R1低{r1_low:.2f} > 左区中点{mid_left:.2f})", {}

        score = rps250 * 0.5 + 15
        if row.get("ATR10") is not None and row.get("ATR20") is not None and row.get("ATR60") is not None:
            if not pd.isna(row["ATR10"]) and not pd.isna(row["ATR20"]) and not pd.isna(row["ATR60"]):
                if row["ATR10"] < row["ATR20"] < row["ATR60"]:
                    score += 10

        vol_baseline = pldf.slice(max(0, curr_idx - 40), (curr_idx - 10) - max(0, curr_idx - 40)).get_column("volume").mean() or 1
        vol_recent = buy_zone.get_column("volume").mean() or 0
        vol_ratio = vol_recent / max(1, vol_baseline)
        if vol_ratio < 0.6:
            score += 10

        dist = (box_high - row["close"]) / row["close"] if row["close"] > 0 else 0
        if dist < 0:
            vol_ma25_val = row.get("vol_ma25")
            if vol_ma25_val is not None and not pd.isna(vol_ma25_val) and row["volume"] > vol_ma25_val * 1.5:
                score += 25
                breakout_status = "放量突破"
            else:
                score -= 10
                breakout_status = "缩量假突破"
        elif dist <= 0.05:
            score += 10
            breakout_status = "临近突破"
        else:
            breakout_status = "未突破"

        if rps250 >= 90:
            score += 10

        peak_dates = []
        pldf_dates = pldf.get_column("datetime")
        for idx, _ in final_peaks:
            raw_date = pldf_dates[idx]
            dt_obj = raw_date if isinstance(raw_date, datetime) else pd.to_datetime(str(raw_date))
            peak_dates.append(dt_obj.strftime("%Y-%m-%d"))

        r1_amp = (s1.get_column("high").max() / s1.get_column("low").min() - 1) if s1.get_column("low").min() else 0
        r2_amp = (s2.get_column("high").max() / s2.get_column("low").min() - 1) if s2.get_column("low").min() else 0

        metrics = {
            "评分": round(score, 1),
            "收盘": row["close"],
            "距突破": f"{dist:.1%}",
            "RPS强度": f"{rps120:.0f}/{rps250:.0f}",
            "突破状态": breakout_status,
            "区间最低点": box_low,
            "命中区间": "150日三高点区间",
            "区间最高价": box_high,
            "区间振幅": f"{left_amp:.1%}",
            "VCP收缩详情": f"R1:{r1_amp:.1%} R2:{r2_amp:.1%} R1+R2:{left_amp:.1%}",
            "VCP达标": "✓",
            "_box_amp": left_amp,
            "_hit_base": curr_idx - peak_idx,
            "_hit_E": curr_idx - last_peak_idx + 1,
            "_model_name": "150日三高",
            "_peak_dates": peak_dates,
            "_high1_idx": peak_idx,
            "_high2_idx": h2_idx,
            "_high3_idx": h3_idx,
            "_high1_date": peak_dates[0],
            "_high2_date": peak_dates[1],
            "_high3_date": peak_dates[2] if len(peak_dates) > 2 else peak_dates[-1],
        }

        days_after_last = curr_idx - last_peak_idx
        if days_after_last < MIN_DAYS_AFTER_LAST_PEAK:
            obs_msg = "pre_observation"
        elif days_after_last < MIN_DAYS_AFTER_LAST_PEAK_CONFIRM:
            obs_msg = "observation"
        else:
            obs_msg = "buy_confirmed"

        metrics["买入阶段"] = obs_msg
        return True, "OK", metrics
