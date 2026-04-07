# engine.py - 策略中台（VCP 引擎）
# 从 vcp_hunter.pyw 提取 VCPEngine 类，零逻辑变更
import numpy as np
import pandas as pd
import polars as pl
from datetime import datetime

from vcp.constants import (
    DATE_FMT, RPS_BUFFER_DAYS,
    LOOKBACK_DAYS, GROUP_DAYS, PEAKS_FROM_GROUPS, PCT_BASELINE,
    MERGE_WITHIN_DAYS,
    EXCLUDE_DAYS_FOR_PEAKS,
    MIN_PEAKS_COUNT, MAX_PEAKS_COUNT,
    FLEXIBLE_MIN_INTERVAL, FLEXIBLE_MAX_INTERVAL,
    MIN_DAYS_AFTER_LAST_PEAK, MIN_DAYS_AFTER_LAST_PEAK_CONFIRM,
    MAX_R2_BELOW_R1_PCT, MIN_FIRST_TO_THIRD_DAYS, MIN_R1_R2_DAYS,
    MIN_SMA50_SLOPE,
    INSTITUTION_KEYWORDS, INSTITUTION_NAME_KEYWORDS,
    SHAREHOLDER_CACHE_FILE, MIN_MARKET_CAP,
)
from vcp.models import VCPParams

from core.logger import get_logger
_log = get_logger(__name__)


class VCPEngine:
    _instance = None

    @classmethod
    def get_instance(cls) -> 'VCPEngine':
        """获取全局唯一引擎实例（单例）"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        # 防止多次初始化覆盖已有数据
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        self._daily_rps_cache: dict = {}
        self._rps_cache_date = None
        self._precomputed_rps_bundle: dict | None = None

    def set_precomputed_rps(self, cache_date: str, rps120, rps250) -> None:
        self._precomputed_rps_bundle = {
            'date': str(cache_date),
            'rps120': rps120,
            'rps250': rps250,
        }

    def get_precomputed_rps(self) -> dict | None:
        return self._precomputed_rps_bundle

    @staticmethod
    def calculate_indicators(df: pd.DataFrame | pl.DataFrame, include_chart: bool = True) -> pd.DataFrame | pl.DataFrame:
        """计算技术指标 - Polars加速版

        参数:
            df: K线 DataFrame (支持 Polars 也可以平滑兼容 Pandas)
            include_chart: 是否包含图表指标(MACD/RSI/BB)
        """
        import polars as pl
        if df is None or len(df) < 10: return df

        is_pandas = isinstance(df, pd.DataFrame)
        
        # 将 Pandas 转为 Polars DataFrame
        if is_pandas:
            if hasattr(df, "attrs") and df.attrs.get("vcp_indicators_ready", False):
                return df
                
            if df.index.name in ('datetime', 'date') or isinstance(df.index, pd.DatetimeIndex):
                if df.index.name is None:
                    df.index.name = 'datetime'
                pldf = pl.from_pandas(df.reset_index())
            else:
                pldf = pl.from_pandas(df)
        else:
            pldf = df

        # 若已经有 'entangle' 这列说明核心跑完了，则跳过
        core_done = "entangle" in pldf.columns
        chart_done = "MACD" in pldf.columns

        if core_done and (not include_chart or chart_done):
            return df if is_pandas else pldf

        # ═══ 核心指标（扫描必需，常驻内存）═══
        if not core_done:
            # 高性能 Polars 管道运算
            pldf = pldf.with_columns([
                pl.col("close").rolling_mean(50).alias("SMA50"),
                pl.col("close").rolling_mean(150).alias("SMA150"),
                pl.col("close").rolling_mean(200).alias("SMA200"),
                pl.col("high").rolling_max(250).alias("High_250"),
                pl.col("close").shift(1).alias("prev_close")
            ]).with_columns([
                pl.max_horizontal([
                    pl.col("high") - pl.col("low"),
                    (pl.col("high") - pl.col("prev_close")).abs(),
                    (pl.col("low") - pl.col("prev_close")).abs()
                ]).alias("TR")
            ]).with_columns([
                pl.col("TR").rolling_mean(10).alias("ATR10"),
                pl.col("TR").rolling_mean(20).alias("ATR20"),
                pl.col("TR").rolling_mean(60).alias("ATR60"),
                pl.col("volume").rolling_mean(25).alias("vol_ma25"),
                pl.col("close").rolling_mean(10).alias("ma10"),
                pl.col("close").rolling_mean(20).alias("ma20"),
            ])
            
            # Amount 填补与计算
            if "amount" not in pldf.columns:
                pldf = pldf.with_columns((pl.col("volume") * pl.col("close") * 100).alias("amount"))
            else:
                pldf = pldf.with_columns([
                    pl.when(pl.col("amount").is_null() | (pl.col("amount") == 0))
                    .then(pl.col("volume") * pl.col("close") * 100)
                    .otherwise(pl.col("amount")).alias("amount")
                ])

            pldf = pldf.with_columns([
                pl.max_horizontal(["ma10", "ma20", "SMA50"]).alias("max_ma"),
                pl.min_horizontal(["ma10", "ma20", "SMA50"]).alias("min_ma")
            ]).with_columns([
                ((pl.col("max_ma") / pl.col("min_ma")) - 1).alias("entangle")
            ])

        # ═══ 图表/诊断指标（按需计算，不常驻内存）═══
        if include_chart and not chart_done:
            # MACD
            pldf = pldf.with_columns([
                pl.col("close").ewm_mean(span=12, adjust=False).alias("exp1"),
                pl.col("close").ewm_mean(span=26, adjust=False).alias("exp2"),
            ]).with_columns([
                (pl.col("exp1") - pl.col("exp2")).alias("MACD")
            ]).with_columns([
                pl.col("MACD").ewm_mean(span=9, adjust=False).alias("MACD_Signal")
            ]).with_columns([
                (pl.col("MACD") - pl.col("MACD_Signal")).alias("MACD_Hist")
            ])

            # RSI
            pldf = pldf.with_columns([
                pl.col("close").diff().alias("delta")
            ]).with_columns([
                pl.when(pl.col("delta") > 0).then(pl.col("delta")).otherwise(0).rolling_mean(14).alias("gain"),
                pl.when(pl.col("delta") < 0).then(-pl.col("delta")).otherwise(0).rolling_mean(14).alias("loss")
            ]).with_columns([
                (pl.col("gain") / pl.col("loss")).alias("rs")
            ]).with_columns([
                (100 - (100 / (1 + pl.col("rs")))).alias("RSI")
            ])

            # 布林带
            pldf = pldf.with_columns([
                pl.col("close").rolling_std(20).alias("std20")
            ]).with_columns([
                (pl.col("ma20") + 2 * pl.col("std20")).alias("BB_up"),
                (pl.col("ma20") - 2 * pl.col("std20")).alias("BB_low")
            ])

        if is_pandas:
            new_df = pldf.to_pandas()
            if 'datetime' in new_df.columns:
                new_df.set_index('datetime', inplace=True)
            elif 'date' in new_df.columns:
                new_df.set_index('date', inplace=True)
            new_df.attrs["vcp_core_ready"] = True
            if include_chart:
                new_df.attrs["vcp_chart_ready"] = True
                new_df.attrs["vcp_indicators_ready"] = True
            return new_df
        return pldf

    @staticmethod
    def _build_prices_matrix(data_dict: dict[str, pd.DataFrame], min_start: pd.Timestamp, end_ts: pd.Timestamp | None = None) -> pd.DataFrame:
        # ---- 加速路径：numpy 直接构建矩阵 ----
        try:
            from vcp.polars_engine import build_prices_matrix_fast
            matrix, cols, dates = build_prices_matrix_fast(data_dict, min_start, end_ts)
            if len(cols) > 0:
                import pandas as pd
                return pd.DataFrame(matrix, index=dates, columns=cols)
        except ImportError:
            pass  # polars_engine 不可用，使用 pandas
        except Exception as e:
            _log.error(f"[策略中台] 加速矩阵构建失败，回退 pandas: {e}")
        # ---- pandas 原始路径（fallback）----
        valid = [(c, df) for c, df in data_dict.items() if df is not None and not df.empty]
        if not valid:
            return pd.DataFrame()
        series_list = []
        for c, df in valid:
            try:
                mask = df.index >= min_start
                if end_ts is not None:
                    mask = mask & (df.index <= end_ts)
                sliced = df.loc[mask, 'close']
                if not sliced.empty:
                    series_list.append(sliced.rename(c))
            except Exception as e:
                _log.info(f"[策略中台] 构建 RPS 时忽略 {c}，原因: {e}")
        if not series_list:
            return pd.DataFrame()
        prices = pd.concat(series_list, axis=1).sort_index()
        prices = prices.ffill(limit=5)
        return prices

    def build_rps_matrix(self, data_dict: dict[str, pd.DataFrame], start_date: str, end_date: str) -> dict:
        num_stocks = len(data_dict)
        today = datetime.now().date()
        if getattr(self, '_rps_cache_date', None) != today:
            self._daily_rps_cache = {}
            self._rps_cache_date = today
        cache_key = (str(start_date), str(end_date))
        if cache_key in self._daily_rps_cache:
            _log.warning(f"\n[策略中台] RPS 矩阵命中缓存 (区间 {start_date} ~ {end_date})，跳过重算")
            return self._daily_rps_cache[cache_key]

        # ---- Polars 快速路径 ----
        try:
            from vcp.polars_engine import build_rps_matrix_pl
            result = build_rps_matrix_pl(data_dict, start_date, end_date, self._daily_rps_cache)
            if result:
                return result
        except ImportError:
            pass  # polars 未安装
        except Exception as e:
            _log.error(f"[策略中台] Polars RPS 计算失败，回退 pandas: {e}")
        # ---- pandas 原始路径（fallback）----
        _log.info(f"\n[策略中台] 正在计算全市场 RPS 强度矩阵... (标的数: {num_stocks})")
        start_ts = pd.to_datetime(start_date)
        end_ts = pd.to_datetime(end_date)
        min_start = start_ts - pd.Timedelta(days=RPS_BUFFER_DAYS)

        prices = self._build_prices_matrix(data_dict, min_start, end_ts)
        if prices.empty:
            _log.warning(f"[策略中台] ⚠ 区间 {start_date} ~ {end_date} 无可用价格数据，跳过该段 RPS 计算。")
            return {}

        rps50  = prices.pct_change(50).rank(axis=1, pct=True) * 100
        rps120 = prices.pct_change(120).rank(axis=1, pct=True) * 100
        rps250 = prices.pct_change(250).rank(axis=1, pct=True) * 100
        target_dates = prices.loc[start_date:end_date].index
        
        # 【修复】周末节假日防空
        if len(target_dates) == 0 and not prices.empty:
            target_dates = prices.index[-1:]
            
        result = {}
        for d in target_dates:
            r50_d  = rps50.loc[d]
            r120_d = rps120.loc[d]
            r250_d = rps250.loc[d]
            valid = r120_d.notna() & r250_d.notna()
            result[d.strftime(DATE_FMT)] = {
                'rps50':  r50_d[valid].to_dict(),
                'rps120': r120_d[valid].to_dict(),
                'rps250': r250_d[valid].to_dict()
            }
        _log.info(f"[策略中台] RPS 矩阵构建完成 — 参与标的 {prices.shape[1]} 只 | 扫描交易日 {len(target_dates)} 个")
        self._daily_rps_cache[cache_key] = result
        return result

    @staticmethod
    def _calculate_flexible_peaks(pldf: pl.DataFrame, curr_idx: int, params: VCPParams) -> tuple[list | None, str]:
        """弹性区间：计算3-4个峰"""
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
        
        for g in range(n_groups):
            start = g * GROUP_DAYS
            end = min(start + GROUP_DAYS, window_len)
            if start >= end:
                continue
            sub_closes = closes[start:end]
            pos_in_window = int(np.argmax(sub_closes))
            idx_df = search_start + start + pos_in_window
            c = float(sub_closes[pos_in_window])
            group_peaks.append((idx_df, c))
            
        if len(group_peaks) < MIN_PEAKS_COUNT:
            return None, f"分组后不足{MIN_PEAKS_COUNT}个峰"

        group_peaks.sort(key=lambda x: x[1], reverse=True)
        baseline = max(closes)
        if baseline <= 0:
            return None, "无有效收盘价"

        top_peaks = group_peaks[: min(PEAKS_FROM_GROUPS, len(group_peaks))]
        top_by_date = sorted(top_peaks, key=lambda x: x[0])
        passed_93 = [(idx, c) for idx, c in top_by_date if c >= PCT_BASELINE * baseline]

        if len(passed_93) < MIN_PEAKS_COUNT:
            return None, f"93%基准下不足{MIN_PEAKS_COUNT}个峰"

        passed_93.sort(key=lambda x: x[0])
        while True:
            merged = False
            for i in range(len(passed_93) - 1):
                if passed_93[i + 1][0] - passed_93[i][0] < MERGE_WITHIN_DAYS:
                    if passed_93[i][1] >= passed_93[i + 1][1]:
                        passed_93.pop(i + 1)
                    else:
                        passed_93.pop(i)
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
    def _check_ma_slope(pldf: pl.DataFrame, curr_idx: int, params: VCPParams) -> tuple[bool, float]:
        if not params.enable_ma_slope:
            return True, 0
        if curr_idx < 5:
            return False, 0
            
        sma50_current = pldf.row(curr_idx, named=True)['SMA50']
        sma50_prev = pldf.row(curr_idx - 5, named=True)['SMA50']
        
        if sma50_current is None or sma50_prev is None or sma50_prev == 0:
            return False, 0
            
        slope = (sma50_current - sma50_prev) / sma50_prev / 5
        if slope < MIN_SMA50_SLOPE:
            return False, slope
        return True, slope

    @staticmethod
    def evaluate_conditions(df: pd.DataFrame | pl.DataFrame, current_day, rps120: float, rps250: float,
                            _rps_history: dict | None = None, params: VCPParams | None = None,
                            skip_red_check: bool = False) -> tuple[bool, str, dict]:
        """增强版选股条件判断 (Polars 高性能加速版)"""
        if params is None:
            params = VCPParams()
            
        is_pandas = isinstance(df, pd.DataFrame)
        if is_pandas:
            if df.index.name == 'datetime':
                pldf = pl.from_pandas(df.reset_index())
            else:
                pldf = pl.from_pandas(df)
        else:
            pldf = df
            
        # 安全转换当前日期，解决 Pandas Timestamp 与 Polars Datetime 精确匹配漏掉的问题
        cd_date = current_day.date() if hasattr(current_day, 'date') else current_day
        
        try:
            # 兼容原生 Datetime 类型的提取
            idx_query = pldf.with_row_index().filter(pl.col("datetime").cast(pl.Date) == cd_date).select("index")
        except Exception as _e:
            # 如果极端情况被解析成字符串等，回退到字符串匹配
            _log.debug(f"[策略中台] 日期类型转换异常，回退字符串匹配: {_e}")
            idx_query = pldf.with_row_index().filter(pl.col("datetime").cast(pl.Utf8).str.starts_with(str(cd_date))).select("index")
            
        if idx_query.height == 0:
            return False, "非交易日", {}
        curr_idx = idx_query.item()
        
        if pd.isna(rps120) or pd.isna(rps250):
            return False, "RPS数据不足", {}
            
        if 'entangle' not in pldf.columns:
            pldf = VCPEngine.calculate_indicators(pldf, include_chart=False)
            
        row = pldf.row(curr_idx, named=True)
        
        # 1. 基础防守
        sma200_ok = row['SMA200'] is not None and not pd.isna(row['SMA200'])
        if curr_idx < params.min_history_days - 1:
            return False, f"数据不足{params.min_history_days}天", {}
        if params.min_history_days >= 200 and not sma200_ok:
            return False, "SMA200数据不足", {}
            
        sma50_val = row.get('SMA50')
        sma150_val = row.get('SMA150')
        
        if sma50_val is None or pd.isna(sma50_val) or sma150_val is None or pd.isna(sma150_val):
            return False, "均线数据不足(缺失SMA50或SMA150)", {}
            
        sma_bull = bool(sma50_val > sma150_val)
        close_above_sma200 = bool(sma200_ok and row['close'] > row['SMA200'])
        if not sma_bull or (sma200_ok and not close_above_sma200):
            return False, "均线非多头", {}
            
        if not skip_red_check and row['close'] <= row['open']:
            return False, "当天K线非红盘", {}
            
        amount_mean = pldf.slice(max(0, curr_idx-19), curr_idx+1 - max(0, curr_idx-19)).get_column("amount").mean()
        if amount_mean is None or pd.isna(amount_mean) or amount_mean < params.min_amount_20d:
            return False, "日均流水不足", {}
            
        entangle_min = pldf.slice(max(0, curr_idx-4), curr_idx+1 - max(0, curr_idx-4)).get_column("entangle").min()
        if entangle_min is None or pd.isna(entangle_min) or entangle_min > params.ma_bind_threshold:
            return False, "短期均线不粘合", {}
            
        if rps250 < params.rps_threshold:
            return False, f"长线动量未达标(RPS250:{rps250:.0f} < {params.rps_threshold})", {}
        if rps250 < 90 and rps250 < rps120:
            return False, f"短线背离长线(RPS250:{rps250:.0f} < RPS120:{rps120:.0f})", {}
            
        ma_slope_ok, ma_slope_val = VCPEngine._check_ma_slope(pldf, curr_idx, params)
        if not ma_slope_ok:
            return False, f"50日均线斜率不足(当前{ma_slope_val:.4f} < {MIN_SMA50_SLOPE})", {}
            
        final_peaks, msg = VCPEngine._calculate_flexible_peaks(pldf, curr_idx, params)
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
            
        peak_high = pldf.row(peak_idx, named=True)['high']
        if 1 - (peak_high / high_250) > params.high_250_threshold:
            return False, "偏离一年高点超限", {}
            
        if row['close'] <= box_low * 1.05:
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
                    
        # 评分
        score = rps250 * 0.5 + 15
        if row.get('ATR10') is not None and row.get('ATR20') is not None and row.get('ATR60') is not None:
            if not pd.isna(row['ATR10']) and not pd.isna(row['ATR20']) and not pd.isna(row['ATR60']):
                if row['ATR10'] < row['ATR20'] < row['ATR60']:
                    score += 10

        vol_baseline = pldf.slice(max(0, curr_idx - 40), (curr_idx - 10) - max(0, curr_idx - 40)).get_column("volume").mean() or 1
        vol_recent = buy_zone.get_column("volume").mean() or 0
        vol_ratio = vol_recent / max(1, vol_baseline)
        if vol_ratio < 0.6:
            score += 10

        dist = (box_high - row['close']) / row['close'] if row['close'] > 0 else 0
        if dist < 0:
            vol_ma25_val = row.get('vol_ma25')
            if vol_ma25_val is not None and not pd.isna(vol_ma25_val) and row['volume'] > vol_ma25_val * 1.5:
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
            d = pldf_dates[idx]
            dt_obj = d if isinstance(d, datetime) else pd.to_datetime(str(d))
            peak_dates.append(dt_obj.strftime(DATE_FMT))

        r1_amp = (s1.get_column("high").max() / s1.get_column("low").min() - 1) if s1.get_column("low").min() else 0
        r2_amp = (s2.get_column("high").max() / s2.get_column("low").min() - 1) if s2.get_column("low").min() else 0

        m = {
            "评分": round(score, 1),
            "收盘": row['close'],
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
            
        m["买入阶段"] = obs_msg
        return True, "OK", m

    # ================================================================
    # 总市值计算（数据源：通达信 pytdx）
    # 公式：总市值 = 总股本(get_finance_info) × 收盘价(本地日线)
    # 优势：无需 HTTP 外部请求，批量高效，可算历史市值
    # ================================================================

    # 通达信服务器池（轮询备用）
    _TDX_SERVERS = [
        ('180.153.18.170', 7709),
        ('180.153.18.171', 7709),
        ('202.108.253.130', 7709),
        ('202.108.253.131', 7709),
        ('60.12.136.250', 7709),
        ('218.75.126.9', 7709),
    ]

    @staticmethod
    def _tdx_connect():
        """连接通达信服务器，返回 api 对象，失败返回 None"""
        from pytdx.hq import TdxHq_API
        api = TdxHq_API()
        for host, port in VCPEngine._TDX_SERVERS:
            try:
                if api.connect(host, port, time_out=5):
                    return api
            except Exception as _e:
                _log.debug(f"[pytdx] 连接服务器 {host}:{port} 失败: {_e}")
                continue
        return None

    @staticmethod
    def batch_get_finance_info(codes):
        """通过通达信批量获取财务信息（总股本、法人股等），带30天磁盘缓存"""
        import time as _time
        import os
        import pickle
        from datetime import datetime
        from vcp.constants import FINANCE_CACHE_FILE

        # 1. 加载本地缓存（有效期 30 天）
        cache = {}
        if os.path.exists(FINANCE_CACHE_FILE):
            try:
                with open(FINANCE_CACHE_FILE, 'rb') as f:
                    cache = pickle.load(f)
            except Exception as _e:
                _log.debug(f"[pytdx] 财务缓存文件读取异常，将重建: {_e}")
                cache = {}

        results = {}
        need_query = []
        now = datetime.now()

        for code in codes:
            if code in cache:
                cached = cache[code]
                try:
                    cache_date = datetime.strptime(cached.get('date', '2000-01-01'), '%Y-%m-%d')
                    if (now - cache_date).days < 30:
                        results[code] = cached['info']
                        continue
                except (ValueError, KeyError) as _e:
                    _log.debug(f"[pytdx] 缓存日期解析异常({code}): {_e}")
            need_query.append(code)

        if not need_query:
            return results

        api = VCPEngine._tdx_connect()
        if api is None:
            _log.warning("[pytdx] 无法连接通达信服务器，市值计算将使用本地旧缓存或暂无数据")
            # 离线降级：如果连不上服务器，强制使用历史缓存（即使已过期）
            for code in need_query:
                if code in cache:
                    results[code] = cache[code]['info']
            return results

        try:
            for i, raw_code in enumerate(need_query):
                # 清理 sh / sz 前缀
                code = raw_code.replace("sh", "").replace("sz", "")
                
                # 判断市场：6/5开头=上海(market=1)，其余=深圳(market=0)
                market = 1 if code.startswith(('6', '5')) else 0
                try:
                    info = api.get_finance_info(market, code)
                    if info:
                        results[raw_code] = info
                        cache[raw_code] = {'info': info, 'date': now.strftime('%Y-%m-%d')}
                except Exception as _e:
                    _log.debug(f"[pytdx] 获取 {raw_code} 财务信息失败: {_e}")
                
                # 每50个暂停一下避免断连
                if (i + 1) % 50 == 0:
                    _time.sleep(0.3)
            
            # 写入缓存
            try:
                with open(FINANCE_CACHE_FILE, 'wb') as f:
                    pickle.dump(cache, f)
            except Exception as e:
                _log.error(f"[pytdx] 财务缓存写入失败: {e}")
                
        finally:
            try:
                api.disconnect()
            except Exception as _e:
                _log.debug(f"[pytdx] 断开服务器连接时异常（可忽略）: {_e}")

        return results

    @staticmethod
    def batch_check_market_cap(codes: list[str], close_prices: dict[str, float] | None = None) -> dict[str, float]:
        """批量计算总市值 = 总股本 × 收盘价

        参数:
            codes: 股票代码列表
            close_prices: {code: close_price} 收盘价字典（可选）

        返回:
            {code: market_cap_in_yuan}
        """
        finance_data = VCPEngine.batch_get_finance_info(codes)
        results = {}
        for code in codes:
            info = finance_data.get(code)
            if not info:
                continue
            zongguben = info.get('zongguben', 0)
            if zongguben and zongguben > 0:
                if close_prices and code in close_prices:
                    # zongguben 如果已经是股数（或 UI 已做好 1e8 转换），这里就不乘 10000。
                    results[code] = zongguben * close_prices[code]
                else:
                    results[code] = zongguben
        return results

    # ================================================================
    # 十大流通股东机构检查（硬过滤）
    # 数据源：东方财富 F10 HTTP API（免费、无需登录）
    # 说明：通达信 pytdx 仅提供法人股/国家股等聚合字段，无法获取
    #       十大流通股东明细及机构类型（基金/保险/社保/QFII等），
    #       因此此功能使用东方财富接口。带 90 天磁盘缓存，日常几乎
    #       不产生联网请求。
    # ================================================================

    @staticmethod
    def _is_institution(name, holder_type):
        """判断单个股东是否为机构

        参数:
            name: 股东名称
            holder_type: 东方财富返回的 HOLDER_TYPE（个人/证券投资基金/私募基金/...）

        返回:
            True 如果是机构
        """
        # 1. 通过东方财富的 HOLDER_TYPE 直接判断
        #    用户认定的机构类型：基金/券商/保险/社保/信托/QFII/北向资金
        for kw in INSTITUTION_KEYWORDS:
            if kw in (holder_type or ''):
                return True

        # 2. 通过股东名称关键词判断（北向资金等标为"其它"类型）
        for kw in INSTITUTION_NAME_KEYWORDS:
            if kw in (name or ''):
                return True

        return False

    @staticmethod
    def check_institutional_shareholders(code):
        """通过东方财富API查询单只股票的十大流通股东，判断是否有机构持仓

        参数:
            code: 股票代码，如 '603659'

        返回:
            (has_institution, institution_names)
            has_institution: bool 是否有机构
            institution_names: str 机构名称摘要（最多显示3个）
        """
        import urllib.request
        import json

        # 确定市场前缀（SH=上海，SZ=深圳，BJ=北交所）
        if code.startswith(('6', '5')):
            prefix = 'SH'
        elif code.startswith(('0', '3')):
            prefix = 'SZ'
        elif code.startswith(('4', '8')):
            prefix = 'BJ'
        else:
            prefix = 'SZ'

        url = f'https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={prefix}{code}'

        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://emweb.securities.eastmoney.com/',
            })
            resp = urllib.request.urlopen(req, timeout=8)
            data = json.loads(resp.read().decode('utf-8'))

            # sdltgd = 十大流通股东（最新一期，最多10条）
            shareholders = data.get('sdltgd', [])
            if not shareholders:
                return False, "无股东数据"

            # 遍历十大流通股东，查找机构
            institutions = []
            for sh in shareholders:
                name = sh.get('HOLDER_NAME', '')
                holder_type = sh.get('HOLDER_TYPE', '')

                if VCPEngine._is_institution(name, holder_type):
                    # 精简名称用于显示
                    short = name[:12] + '..' if len(name) > 12 else name
                    institutions.append(short)

            if institutions:
                display = '/'.join(institutions[:3])
                if len(institutions) > 3:
                    display += f' 等{len(institutions)}家'
                return True, display
            else:
                return False, "无机构"

        except Exception as e:
            return False, f"查询失败:{str(e)[:20]}"

    @staticmethod
    def batch_check_institution(codes):
        """批量查询多只股票的机构股东情况（带 90 天磁盘缓存）

        数据源: 东方财富 F10 HTTP API
        缓存: SHAREHOLDER_CACHE_FILE，90天有效

        参数:
            codes: ['603659', '002463', ...] 股票代码列表

        返回:
            {code: {'has_institution': bool, 'detail': str, 'date': str}}
        """
        import os
        import pickle
        import time as _time

        # ---- 加载磁盘缓存 ----
        cache = {}
        if os.path.exists(SHAREHOLDER_CACHE_FILE):
            try:
                with open(SHAREHOLDER_CACHE_FILE, 'rb') as f:
                    cache = pickle.load(f)
            except Exception as _e:
                _log.debug(f"[机构股东] 缓存文件读取异常，将重建: {_e}")
                cache = {}

        results = {}
        need_query = []
        now = datetime.now()

        for code in codes:
            if code in cache:
                cached = cache[code]
                # 缓存有效期：90天
                try:
                    cache_date = datetime.strptime(cached['date'], '%Y-%m-%d')
                    if (now - cache_date).days < 90:
                        results[code] = cached
                        continue
                except (ValueError, KeyError) as _e:
                    _log.debug(f"[机构股东] 缓存日期解析异常({code}): {_e}")
            need_query.append(code)

        # ---- 联网查询未缓存的（东方财富 F10） ----
        if need_query:
            _log.info(f"[机构股东] 东方财富查询 {len(need_query)} 只（缓存命中 {len(codes) - len(need_query)} 只）...")
            for i, code in enumerate(need_query):
                has_inst, detail = VCPEngine.check_institutional_shareholders(code)
                entry = {
                    'has_institution': has_inst,
                    'detail': detail,
                    'date': now.strftime('%Y-%m-%d'),
                }
                results[code] = entry
                cache[code] = entry
                # 每次查询间隔 0.3 秒，避免被东方财富限流
                if i < len(need_query) - 1:
                    _time.sleep(0.3)

            # ---- 保存缓存到磁盘 ----
            try:
                with open(SHAREHOLDER_CACHE_FILE, 'wb') as f:
                    pickle.dump(cache, f)
            except Exception as _e:
                _log.debug(f"[机构股东] 缓存保存失败: {_e}")

        return results


    # ================================================================
    # 盘中监控优化：预计算待突破池 + 轻量级实时判断
    # ================================================================

    @staticmethod
    def precompute_ready_pool(all_data, rps120_series, rps250_series, params,
                              sector_manager=None, sector_rps_dict=None, sector_threshold=70,
                              server_pool=None, code2name=None, progress_callback=None):
        """收盘后一次性预计算"待突破池"

        用昨日 EOD 数据对全量股票执行完整 evaluate_conditions，
        将通过的股票及其箱体参数缓存下来，盘中不再重复计算。

        参数:
            all_data: {代码: DataFrame} 全部股票日线
            rps120_series: RPS120 Series
            rps250_series: RPS250 Series
            params: VCPParams 参数
            sector_manager: 板块管理器（可选）
            sector_rps_dict: 板块RPS字典（可选）
            sector_threshold: 板块RPS阈值
            server_pool: [(ip,port),...] 通达信服务器列表，用于查询机构股东（可选）

        返回:
            ready_pool: {代码: {
                'box_high': 箱顶价,
                'box_low': 箱底价,
                'score': 基础评分,
                'rps_str': RPS强度,
                'vol_ma25': 25日均量,
                'sector_info': 热点板块信息,
                'institution_tag': 机构标记（'有机构:XX基金/...' 或 '无机构'），
                'meta': 完整evaluate返回的m字典,
            }}
        """
        # Parquet cache returns Polars DataFrames, but downstream code needs Pandas
        import polars as _pl
        _converted = 0
        for _code in list(all_data.keys()):
            _df = all_data[_code]
            if isinstance(_df, _pl.DataFrame):
                _pdf = _df.to_pandas()
                if "datetime" in _pdf.columns:
                    _pdf["datetime"] = pd.to_datetime(_pdf["datetime"])
                    _pdf.set_index("datetime", inplace=True)
                all_data[_code] = _pdf
                _converted += 1
        if _converted:
            _log.info(f"[ready_pool] Polars->Pandas converted {_converted} stocks")

        import time as _time
        ready_pool = {}
        st_filtered = 0
        total_count = len(all_data)
        # === 诊断计数器 ===
        _diag_short = 0
        _diag_rps_nan = 0
        _diag_ind_fail = 0
        _diag_eval_fail = 0
        _diag_eval_err = 0
        for idx_code, (code, df) in enumerate(all_data.items()):
            # 【休眠释放 GIL】每完成几只后主动释放 CPU，防卡死
            if idx_code % 20 == 0:
                _time.sleep(0.001)
            # #10: 每处理 200 只回报进度，消除首轮启动的“黑屏焦虑”
            if progress_callback and idx_code % 200 == 0:
                progress_callback(f"构建待突破池: {idx_code}/{total_count}...")
                
            if df is None or len(df) < 250:
                _diag_short += 1
                continue

            # === ST 股过滤：ST/*ST 涨跌幅仅 5%，易伪装成 VCP 收缩形态 ===
            if code2name:
                stock_name = code2name.get(code, '')
                if 'ST' in stock_name.upper():
                    st_filtered += 1
                    continue

            r120 = rps120_series.get(code, np.nan)
            r250 = rps250_series.get(code, np.nan)
            if pd.isna(r120) or pd.isna(r250):
                _diag_rps_nan += 1
                continue

            # 确保指标已计算（一次性）
            if 'entangle' not in df.columns:
                try:
                    all_data[code] = VCPEngine.calculate_indicators(df.copy())
                    df = all_data[code]
                except Exception as _e:
                    _log.debug(f"[待突破池] {code} 指标计算异常: {_e}")
                    _diag_ind_fail += 1
                    continue

            eval_day = df.index[-1]
            try:
                ok, reason, m = VCPEngine.evaluate_conditions(
                    df, eval_day, float(r120), float(r250), None, params, skip_red_check=True)
            except Exception as _e:
                _log.debug(f"[待突破池] {code} 条件评估异常: {_e}")
                _diag_eval_fail += 1
                continue

            if not ok:
                continue

            # 板块 RPS 检查
            sector_info = ""
            if sector_manager and sector_rps_dict:
                s_ok, s_info, _ = sector_manager.check_sector_rps(
                    code, sector_rps_dict, sector_threshold)
                if not s_ok:
                    continue
                sector_info = s_info

            # 提取盘中需要的关键参数
            box_high = m.get('区间最高价', 0)
            box_low = m.get('区间最低点', 0)

            # 25日均量（用于放量推算）
            curr_idx = len(df) - 1
            vol_slice = df.iloc[max(0, curr_idx - 24):curr_idx + 1]['volume']
            vol_ma25 = float(vol_slice.mean()) if len(vol_slice) > 0 else 0

            ready_pool[code] = {
                'box_high': box_high,
                'box_low': box_low,
                'score': m.get('评分', 0),
                'rps_str': m.get('RPS强度', ''),
                'vol_ma25': vol_ma25,
                'sector_info': sector_info,
                'institution_tag': '',  # 稍后批量填充
                'meta': m,  # 保留完整结果用于绘图等
            }

        _log.info(f"[待突破池] 预计算完成 | 全量 {len(all_data)} 只 → 候选 {len(ready_pool)} 只（ST 剔除 {st_filtered} 只）")
        # ---- 批量查询机构股东（东方财富 F10 API，90天缓存） ----
        # 改为"软标记"而非硬删除，避免误杀扫描已确认的 VCP 形态股
        if ready_pool:
            try:
                inst_results = VCPEngine.batch_check_institution(
                    list(ready_pool.keys()))
                inst_count = 0
                no_inst_count = 0
                for code, entry in ready_pool.items():
                    inst_info = inst_results.get(code, {})
                    has_inst = inst_info.get('has_institution', False)
                    detail = inst_info.get('detail', '')
                    if has_inst:
                        entry['institution_tag'] = f"✓机构:{detail}"
                        inst_count += 1
                    else:
                        entry['institution_tag'] = "无机构"
                        no_inst_count += 1
                _log.info(f"[机构股东] 标记完成 | 有机构 {inst_count} 只，无机构 {no_inst_count} 只（均保留在池中）")
            except Exception as e:
                _log.error(f"[机构股东] 查询异常，跳过筛选: {e}")
        # ---- 总市值标记：总股本×收盘价（通达信） ----
        # 改为"软标记"而非硬删除，小市值股票保留在池中但打上标记
        if ready_pool:
            try:
                close_prices = {}
                for code in ready_pool:
                    df = all_data.get(code)
                    if df is not None and len(df) > 0:
                        close_prices[code] = float(df.iloc[-1]['close'])
                cap_results = VCPEngine.batch_check_market_cap(
                    list(ready_pool.keys()), close_prices=close_prices)
                small_cap_count = 0
                for code, entry in ready_pool.items():
                    cap = cap_results.get(code)
                    if cap and cap > 0:
                        cap_yi = cap / 1e8
                        entry['market_cap'] = f"{cap_yi:.0f}亿"
                        if cap < MIN_MARKET_CAP:
                            entry['small_cap'] = True
                            small_cap_count += 1
                    else:
                        entry['market_cap'] = '未知'
                _log.info(f"[市值标记] 完成 | 共 {len(ready_pool)} 只，其中小市值(<40亿) {small_cap_count} 只（均保留在池中）")
            except Exception as e:
                _log.error(f"[市值筛选] 查询异常，跳过: {e}")
        return ready_pool

    @staticmethod
    def rt_quick_check(quote, pool_entry):
        """盘中轻量级实时判断（~0.01ms/只）

        只检查 1 项硬性条件：红盘（现价 > 开盘价）。
        只要在待突破池中（VCP形态成立）且当天红盘，即触发信号。
        突破状态作为附加标签（放量突破/缩量突破/临近突破/未突破），不影响是否触发。

        参数:
            quote: 实时报价 dict，含 close/open/volume
            pool_entry: ready_pool 中的缓存数据

        返回:
            (触发: bool, 状态文字: str, 调整后评分: float)
        """
        rt_close = float(quote.get('close', 0) or 0)
        rt_open = float(quote.get('open', 0) or 0)
        rt_high = float(quote.get('high', 0) or 0)
        rt_low = float(quote.get('low', 0) or 0)
        rt_volume = float(quote.get('volume', 0) or 0)

        box_high = pool_entry['box_high']
        base_score = pool_entry['score']
        vol_ma25 = pool_entry['vol_ma25']

        if rt_close <= 0 or rt_open <= 0 or box_high <= 0:
            return False, "数据异常", 0

        # === 一字板过滤：涨/跌停封死 (high == low)，散户无法买入 ===
        if rt_high > 0 and rt_low > 0 and rt_high == rt_low:
            return False, "一字板(不可交易)", 0

        # ---- 唯一硬性条件：红盘 ----
        if rt_close <= rt_open:
            return False, "非红盘", 0

        # ---- 突破状态标签（不过滤，仅标注） ----
        dist = (box_high - rt_close) / rt_close if rt_close > 0 else 0
        score = base_score

        if dist < 0:
            # 已突破箱顶
            est_full_vol = VCPEngine._estimate_full_day_volume(rt_volume)
            if vol_ma25 > 0 and est_full_vol > 0:
                vol_ratio = est_full_vol / vol_ma25
                if vol_ratio >= 1.5:
                    score += 25
                    breakout_status = f"放量突破(量比{vol_ratio:.1f})"
                else:
                    score -= 10
                    breakout_status = f"缩量突破(量比{vol_ratio:.1f})"
            else:
                breakout_status = "突破"
        elif dist <= 0.05:
            # 临近箱顶（5%以内）
            score += 10
            breakout_status = f"临近突破({dist:.1%})"
        else:
            # VCP蓄力中（距箱顶较远）
            breakout_status = f"VCP蓄力({dist:.1%})"

        return True, breakout_status, round(score, 1)

    @staticmethod
    def _estimate_full_day_volume(current_volume):
        """根据当前时间推算全天成交量

        A股交易时间：9:30-11:30（120分钟）+ 13:00-15:00（120分钟）= 240分钟
        """
        from datetime import datetime
        now = datetime.now()
        hour, minute = now.hour, now.minute

        # 计算已过交易时间（分钟）
        if hour < 9 or (hour == 9 and minute < 30):
            return 0  # 盘前，无法推算
        elif hour < 11 or (hour == 11 and minute <= 30):
            # 上午盘：9:30 → 11:30
            elapsed = (hour - 9) * 60 + minute - 30
        elif hour < 13:
            # 午间休市：按上午120分钟算
            elapsed = 120
        elif hour < 15:
            # 下午盘：13:00 → 15:00
            elapsed = 120 + (hour - 13) * 60 + minute
        else:
            # 盘后：全天240分钟
            elapsed = 240

        elapsed = max(elapsed, 1)  # 避免除零
        time_ratio = elapsed / 240.0

        # 早盘前30分钟（10点前）推算偏差大，不推算
        if elapsed < 30:
            return current_volume * 8  # 粗估：前30分钟≈全天12.5%

        return current_volume / time_ratio
