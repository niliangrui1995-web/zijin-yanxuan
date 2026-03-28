# engine.py - 策略中台（VCP 引擎）
# 从 vcp_hunter.pyw 提取 VCPEngine 类，零逻辑变更
import numpy as np
import pandas as pd
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
    MIN_SMA50_SLOPE, MIN_ATR10_THRESHOLD, MIN_ENTANGLE_PRE_SPREAD,
    INSTITUTION_KEYWORDS, INSTITUTION_NAME_KEYWORDS,
    SHAREHOLDER_CACHE_FILE, MIN_MARKET_CAP,
)
from vcp.models import VCPParams


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
    def calculate_indicators(df: pd.DataFrame, include_chart: bool = True) -> pd.DataFrame:
        """计算技术指标

        参数:
            df: K线 DataFrame
            include_chart: 是否包含图表指标(MACD/RSI/BB)。批量预算时传 False 节省内存
        """
        if df is None or len(df) < 10: return df

        # 检查缓存标记
        if hasattr(df, "attrs"):
            # 兼容旧标记：全量指标已就绪则直接返回
            if df.attrs.get("vcp_indicators_ready", False):
                return df
            core_done = df.attrs.get("vcp_core_ready", False)
            chart_done = df.attrs.get("vcp_chart_ready", False)
            if core_done and (not include_chart or chart_done):
                return df
        else:
            core_done = False
            chart_done = False

        # ═══ 核心指标（扫描必需，常驻内存）═══
        if not core_done:
            df['SMA50']  = df['close'].rolling(50).mean()
            df['SMA150'] = df['close'].rolling(150).mean()
            df['SMA200'] = df['close'].rolling(200).mean()
            df['High_250'] = df['high'].rolling(250).max()
            prev_close = df['close'].shift(1)

            tr = np.maximum(df['high'] - df['low'],
                            np.maximum((df['high'] - prev_close).abs(),
                                       (df['low'] - prev_close).abs()))

            df['ATR10'], df['ATR20'], df['ATR60'] = tr.rolling(10).mean(), tr.rolling(20).mean(), tr.rolling(60).mean()
            df['vol_ma25'] = df['volume'].rolling(25).mean()
            if 'amount' not in df.columns or df['amount'].isna().all() or (df['amount'] == 0).all():
                df['amount'] = df['volume'] * df['close'] * 100

            ma10 = df['close'].rolling(10).mean()
            ma20 = df['close'].rolling(20).mean()
            ma50 = df['SMA50']
            max_ma = np.maximum(np.maximum(ma10, ma20), ma50)
            min_ma = np.minimum(np.minimum(ma10, ma20), ma50)
            df['entangle'] = (max_ma / min_ma) - 1

            if hasattr(df, "attrs"):
                df.attrs["vcp_core_ready"] = True

        # ═══ 图表/诊断指标（按需计算，不常驻内存）═══
        if include_chart and not chart_done:
            try:
                exp1 = df['close'].ewm(span=12, adjust=False).mean()
                exp2 = df['close'].ewm(span=26, adjust=False).mean()
                df['MACD'] = exp1 - exp2
                df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
                df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']
            except Exception:
                pass

            # RSI（供本地诊断使用）
            try:
                delta = df['close'].diff()
                gain = delta.clip(lower=0).rolling(14).mean()
                loss = (-delta.clip(upper=0)).rolling(14).mean()
                rs = gain / loss
                df['RSI'] = 100 - (100 / (1 + rs))
            except Exception:
                pass

            # 布林带（供本地诊断使用）
            try:
                sma20 = df['close'].rolling(20).mean()
                std20 = df['close'].rolling(20).std()
                df['BB_up'] = sma20 + 2 * std20
                df['BB_low'] = sma20 - 2 * std20
            except Exception:
                pass

            if hasattr(df, "attrs"):
                df.attrs["vcp_chart_ready"] = True
                df.attrs["vcp_indicators_ready"] = True

        return df

    @staticmethod
    def _build_prices_matrix(data_dict: dict[str, pd.DataFrame], min_start: pd.Timestamp, end_ts: pd.Timestamp | None = None) -> pd.DataFrame:
        # ---- 加速路径：numpy 直接构建矩阵 ----
        try:
            from vcp.polars_engine import build_prices_matrix_fast
            fast_result = build_prices_matrix_fast(data_dict, min_start, end_ts)
            if not fast_result.empty:
                return fast_result
        except ImportError:
            pass  # polars_engine 不可用，使用 pandas
        except Exception as e:
            print(f"[策略中台] 加速矩阵构建失败，回退 pandas: {e}")

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
                print(f"[策略中台] 构建 RPS 时忽略 {c}，原因: {e}")
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
            print(f"\n[策略中台] RPS 矩阵命中缓存 (区间 {start_date} ~ {end_date})，跳过重算")
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
            print(f"[策略中台] Polars RPS 计算失败，回退 pandas: {e}")

        # ---- pandas 原始路径（fallback）----
        print(f"\n[策略中台] 正在计算全市场 RPS 强度矩阵... (标的数: {num_stocks})")
        start_ts = pd.to_datetime(start_date)
        end_ts = pd.to_datetime(end_date)
        min_start = start_ts - pd.Timedelta(days=RPS_BUFFER_DAYS)

        prices = self._build_prices_matrix(data_dict, min_start, end_ts)
        if prices.empty:
            print(f"[策略中台] ⚠ 区间 {start_date} ~ {end_date} 无可用价格数据，跳过该段 RPS 计算。")
            return {}

        rps50  = prices.pct_change(50).rank(axis=1, pct=True) * 100
        rps120 = prices.pct_change(120).rank(axis=1, pct=True) * 100
        rps250 = prices.pct_change(250).rank(axis=1, pct=True) * 100
        target_dates = prices.loc[start_date:end_date].index
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
        print(f"[策略中台] RPS 矩阵构建完成 — 参与标的 {prices.shape[1]} 只 | 扫描交易日 {len(target_dates)} 个")
        self._daily_rps_cache[cache_key] = result
        return result

    @staticmethod
    def _calculate_flexible_peaks(df: pd.DataFrame, curr_idx: int, params: VCPParams) -> tuple[list | None, str]:
        """弹性区间：计算3-4个峰"""
        search_start = max(0, curr_idx - (LOOKBACK_DAYS - 1))
        if curr_idx - EXCLUDE_DAYS_FOR_PEAKS <= search_start:
            return None, "最近3日不参与算峰，需更多历史数据"

        window_peak = df.iloc[search_start : curr_idx - EXCLUDE_DAYS_FOR_PEAKS]
        nw = len(window_peak)
        if nw < 20:
            return None, "可用于计算峰的数据不足"

        n_groups = (nw + GROUP_DAYS - 1) // GROUP_DAYS
        group_peaks = []
        for g in range(n_groups):
            start = g * GROUP_DAYS
            end = min(start + GROUP_DAYS, nw)
            if start >= end:
                continue
            sub = window_peak.iloc[start:end]
            pos_in_window = int(sub['close'].values.argmax())
            idx_df = search_start + start + pos_in_window
            c = float(sub['close'].iloc[pos_in_window])
            group_peaks.append((idx_df, c))

        if len(group_peaks) < MIN_PEAKS_COUNT:
            return None, f"分组后不足{MIN_PEAKS_COUNT}个峰"

        group_peaks.sort(key=lambda x: x[1], reverse=True)
        baseline = float(window_peak['close'].max())
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
    def _check_ma_slope(df: pd.DataFrame, curr_idx: int, params: VCPParams) -> tuple[bool, float]:
        if not params.enable_ma_slope:
            return True, 0
        if curr_idx < 5:
            return False, 0
        sma50_current = df.iloc[curr_idx]['SMA50']
        sma50_prev = df.iloc[curr_idx - 5]['SMA50']
        if pd.isna(sma50_current) or pd.isna(sma50_prev) or sma50_prev == 0:
            return False, 0
        slope = (sma50_current - sma50_prev) / sma50_prev / 5
        if slope < MIN_SMA50_SLOPE:
            return False, slope
        return True, slope

    @staticmethod
    def _check_volatility(df: pd.DataFrame, curr_idx: int, params: VCPParams) -> tuple[bool, float]:
        if not params.enable_volatility_filter:
            return True, 0
        row = df.iloc[curr_idx]
        if pd.isna(row['ATR10']) or row['close'] <= 0:
            return False, 0
        atr_ratio = row['ATR10'] / row['close']
        if atr_ratio < MIN_ATR10_THRESHOLD:
            return False, atr_ratio
        return True, atr_ratio

    @staticmethod
    def _check_entangle_pre_spread(df: pd.DataFrame, curr_idx: int, params: VCPParams) -> tuple[bool, float]:
        if not params.enable_pre_spread:
            return True, 0
        if curr_idx < 30:
            return False, 0
        entangle_max = df.iloc[max(0, curr_idx-29):curr_idx+1]['entangle'].max()
        if pd.isna(entangle_max):
            return False, 0
        if entangle_max < MIN_ENTANGLE_PRE_SPREAD:
            return False, entangle_max
        return True, entangle_max

    @staticmethod
    def _check_observation_vs_buy(df: pd.DataFrame, curr_idx: int, final_peaks: list, row: pd.Series, params: VCPParams) -> tuple[str, str]:
        last_peak_idx = final_peaks[-1][0]
        days_after_last = curr_idx - last_peak_idx
        if days_after_last < MIN_DAYS_AFTER_LAST_PEAK:
            return "pre_observation", f"距最后一峰仅{days_after_last}日，未进入观察期"
        if days_after_last < MIN_DAYS_AFTER_LAST_PEAK_CONFIRM:
            return "observation", f"观察期：距最后一峰{days_after_last}日"
        return "buy_confirmed", "建仓期确认"

    @staticmethod
    def evaluate_conditions(df: pd.DataFrame, current_day, rps120: float, rps250: float,
                            rps_history: dict | None = None, params: VCPParams | None = None,
                            skip_red_check: bool = False) -> tuple[bool, str, dict]:
        """增强版选股条件判断"""
        if params is None:
            params = VCPParams()

        try:
            loc = df.index.get_loc(current_day)
            if isinstance(loc, slice):
                curr_idx = loc.stop - 1 if loc.stop is not None else loc.start
            elif isinstance(loc, np.ndarray):
                curr_idx = int(loc[-1])
            else:
                curr_idx = int(loc)
        except Exception:
            return False, "非交易日", {}

        if pd.isna(rps120) or pd.isna(rps250):
            return False, "RPS数据不足", {}

        if 'entangle' not in df.columns:
            try: df = VCPEngine.calculate_indicators(df.copy())
            except Exception: return False, "指标计算失败", {}

        row = df.iloc[curr_idx]

        # 1. 基础防守
        sma200_ok = not pd.isna(row['SMA200'])
        if curr_idx < params.min_history_days - 1:
            return False, f"数据不足{params.min_history_days}天", {}
        if params.min_history_days >= 200 and not sma200_ok:
            return False, "SMA200数据不足", {}

        sma_bull = bool(row['SMA50'] > row['SMA150'])
        close_above_sma200 = bool(sma200_ok and row['close'] > row['SMA200'])
        if not sma_bull or (sma200_ok and not close_above_sma200):
            return False, "均线非多头", {}

        if not skip_red_check and row['close'] <= row['open']:
            return False, "当天K线非红盘", {}

        amount_mean = df.iloc[max(0, curr_idx-19):curr_idx+1]['amount'].mean()
        if amount_mean < params.min_amount_20d:
            return False, "日均流水不足", {}

        entangle_min = df.iloc[max(0, curr_idx-4):curr_idx+1]['entangle'].min()
        if entangle_min > params.ma_bind_threshold:
            return False, "短期均线不粘合", {}

        # RPS 过滤
        if rps250 < params.rps_threshold:
            return False, f"长线动量未达标(RPS250:{rps250:.0f} < {params.rps_threshold})", {}
        if rps250 < 90 and rps250 < rps120:
            return False, f"短线背离长线(RPS250:{rps250:.0f} < RPS120:{rps120:.0f})", {}

        # 二级筛选：均线斜率（50日均线须上升）
        ma_slope_ok, ma_slope_val = VCPEngine._check_ma_slope(df, curr_idx, params)
        if not ma_slope_ok:
            return False, f"50日均线斜率不足(当前{ma_slope_val:.4f} < {MIN_SMA50_SLOPE})", {}

        # 弹性区间峰计算
        final_peaks, msg = VCPEngine._calculate_flexible_peaks(df, curr_idx, params)
        if final_peaks is None:
            return False, msg, {}

        peak_idx = final_peaks[0][0]
        last_peak_idx = final_peaks[-1][0]

        if curr_idx < last_peak_idx + MIN_DAYS_AFTER_LAST_PEAK:
            return False, f"买入点须在最后一峰之后{MIN_DAYS_AFTER_LAST_PEAK}个交易日", {}

        # 振幅约束
        left_zone = df.iloc[peak_idx : last_peak_idx + 1]
        buy_zone = df.iloc[last_peak_idx + 1 : curr_idx + 1]
        box_low = left_zone['low'].min()
        box_high = left_zone['high'].max()
        left_amp = (box_high - box_low) / box_low if box_low > 0 else 0

        if buy_zone['low'].min() > 0:
            buy_amp = (buy_zone['high'].max() - buy_zone['low'].min()) / buy_zone['low'].min()
            if buy_amp >= left_amp:
                return False, "买入区振幅未小于左侧区振幅", {}

        if left_amp > params.amp_threshold:
            return False, f"左侧区振幅超限({left_amp:.1%} > {params.amp_threshold:.0%})", {}

        high_250 = row.get('High_250')
        if pd.isna(high_250) or high_250 <= 0:
            return False, "无有效一年高点", {}
        peak_high = df.iloc[peak_idx]['high']
        if 1 - (peak_high / high_250) > params.high_250_threshold:
            return False, "偏离一年高点超限", {}

        if row['close'] <= box_low * 1.05:
            return False, "贴近箱底(<5%)", {}

        prior_250_start = max(0, peak_idx - 250)
        if prior_250_start < peak_idx:
            prior_250_max = df.iloc[prior_250_start : peak_idx]['high'].max()
            if prior_250_max > 0 and peak_high < prior_250_max * 0.92:
                deviation = (1 - peak_high / prior_250_max) * 100
                return False, f"第一高点非前250日相对高点(偏离{deviation:.1f}%)", {}

        if len(final_peaks) >= 3:
            first_to_third_days = final_peaks[2][0] - final_peaks[0][0] + 1
            if first_to_third_days < MIN_FIRST_TO_THIRD_DAYS:
                return False, f"第一峰到第三峰不足{MIN_FIRST_TO_THIRD_DAYS}日(当前{first_to_third_days}日)", {}

        h2_idx = final_peaks[1][0] if len(final_peaks) >= 2 else peak_idx
        h3_idx = final_peaks[2][0] if len(final_peaks) >= 3 else h2_idx

        s1 = df.iloc[peak_idx : h2_idx + 1]
        s2 = df.iloc[h2_idx : h3_idx + 1]
        r1_len = h2_idx - peak_idx + 1
        r2_len = h3_idx - h2_idx + 1
        if len(final_peaks) >= 3 and (r1_len + r2_len) <= MIN_R1_R2_DAYS:
            return False, f"R1+R2须大于{MIN_R1_R2_DAYS}个交易日(当前{r1_len + r2_len}日)", {}

        if len(final_peaks) >= 3:
            r1_low = float(s1['low'].min())
            r2_low = float(s2['low'].min())
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
        if row['ATR10'] < row['ATR20'] < row['ATR60']:
            score += 10

        vol_baseline = df.iloc[max(0, curr_idx - 40) : curr_idx - 10]['volume'].mean()
        vol_recent = buy_zone['volume'].mean()
        vol_ratio = vol_recent / max(1, vol_baseline)
        if vol_ratio < 0.6:
            score += 10

        dist = (box_high - row['close']) / row['close'] if row['close'] > 0 else 0
        if dist < 0:
            if row['volume'] > row['vol_ma25'] * 1.5:
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
        for idx, _ in final_peaks:
            d = df.index[idx]
            peak_dates.append(d.strftime(DATE_FMT) if hasattr(d, 'strftime') else str(d))

        r1_len = h2_idx - peak_idx + 1
        r2_len = h3_idx - h2_idx + 1
        r3_len = curr_idx - last_peak_idx + 1

        r1_amp = s1['high'].max() / s1['low'].min() - 1 if s1['low'].min() > 0 else 0
        r2_amp = s2['high'].max() / s2['low'].min() - 1 if s2['low'].min() > 0 else 0

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
            "_hit_E": r3_len,
            "_model_name": "150日三高",
            "_peak_dates": peak_dates,
            "_high1_idx": peak_idx,
            "_high2_idx": h2_idx,
            "_high3_idx": h3_idx,
            "_high1_date": peak_dates[0],
            "_high2_date": peak_dates[1],
            "_high3_date": peak_dates[2] if len(peak_dates) > 2 else peak_dates[-1],
        }
        obs_status, obs_msg = VCPEngine._check_observation_vs_buy(df, curr_idx, final_peaks, row, params)
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
            except Exception:
                continue
        return None

    @staticmethod
    def batch_get_finance_info(codes):
        """通过通达信批量获取财务信息（总股本、法人股等）

        参数:
            codes: ['603659', '002463', ...] 股票代码列表

        返回:
            {code: {zongguben, farengu, hgu, guojiagu, ...}} 字典
        """
        import time as _time
        api = VCPEngine._tdx_connect()
        if api is None:
            print("[pytdx] 无法连接通达信服务器")
            return {}

        results = {}
        try:
            for i, code in enumerate(codes):
                # 判断市场：6/5开头=上海(market=1)，其余=深圳(market=0)
                market = 1 if code.startswith(('6', '5')) else 0
                try:
                    info = api.get_finance_info(market, code)
                    if info:
                        results[code] = info
                except Exception:
                    pass
                # 每50个暂停一下避免断连
                if (i + 1) % 50 == 0:
                    _time.sleep(0.3)
        finally:
            try:
                api.disconnect()
            except Exception:
                pass

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
                    # 总市值 = 总股本 × 收盘价
                    results[code] = zongguben * close_prices[code]
                else:
                    results[code] = zongguben  # 无收盘价时返回股本数
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
            except Exception:
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
                except Exception:
                    pass
            need_query.append(code)

        # ---- 联网查询未缓存的（东方财富 F10） ----
        if need_query:
            print(f"[机构股东] 东方财富查询 {len(need_query)} 只（缓存命中 {len(codes) - len(need_query)} 只）...")
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
            except Exception:
                pass

        return results


    # ================================================================
    # 盘中监控优化：预计算待突破池 + 轻量级实时判断
    # ================================================================

    @staticmethod
    def precompute_ready_pool(all_data, rps120_series, rps250_series, params,
                              sector_manager=None, sector_rps_dict=None, sector_threshold=70,
                              server_pool=None, code2name=None):
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
        ready_pool = {}
        processed = 0
        st_filtered = 0
        for code, df in all_data.items():
            if df is None or len(df) < 250:
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
                continue

            # 确保指标已计算（一次性）
            if 'entangle' not in df.columns:
                try:
                    all_data[code] = VCPEngine.calculate_indicators(df.copy())
                    df = all_data[code]
                except Exception:
                    continue

            eval_day = df.index[-1]
            try:
                ok, reason, m = VCPEngine.evaluate_conditions(
                    df, eval_day, float(r120), float(r250), None, params, skip_red_check=True)
            except Exception:
                continue

            if not ok:
                continue

            # 板块 RPS 检查
            sector_info = ""
            if sector_manager and sector_rps_dict:
                s_ok, s_info, s_max = sector_manager.check_sector_rps(
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
            processed += 1

        print(f"[待突破池] 预计算完成 | 全量 {len(all_data)} 只 → 候选 {len(ready_pool)} 只（ST 剔除 {st_filtered} 只）")

        # ---- 批量查询机构股东（东方财富 F10 API，90天缓存） ----
        if ready_pool:
            try:
                inst_results = VCPEngine.batch_check_institution(
                    list(ready_pool.keys()))
                no_inst_codes = []
                for code, entry in ready_pool.items():
                    inst_info = inst_results.get(code, {})
                    has_inst = inst_info.get('has_institution', False)
                    detail = inst_info.get('detail', '')
                    if has_inst:
                        entry['institution_tag'] = f"✓机构:{detail}"
                    else:
                        no_inst_codes.append(code)
                # 剔除无机构的股票
                for code in no_inst_codes:
                    del ready_pool[code]
                print(f"[机构股东] 筛选完成 | 有机构 {len(ready_pool)} 只，剔除无机构 {len(no_inst_codes)} 只")
            except Exception as e:
                print(f"[机构股东] 查询异常，跳过筛选: {e}")

        # ---- 总市值筛选：总股本×收盘价（通达信），剔除 < 40亿的股票 ----
        if ready_pool:
            try:
                # 从本地日线数据获取每只股票的最新收盘价
                close_prices = {}
                for code in ready_pool:
                    df = all_data.get(code)
                    if df is not None and len(df) > 0:
                        close_prices[code] = float(df.iloc[-1]['close'])
                cap_results = VCPEngine.batch_check_market_cap(
                    list(ready_pool.keys()), close_prices=close_prices)
                small_cap_codes = []
                for code, entry in ready_pool.items():
                    cap = cap_results.get(code)
                    if cap and cap > 0:
                        cap_yi = cap / 1e8  # 转为亿
                        entry['market_cap'] = f"{cap_yi:.0f}亿"
                        if cap < MIN_MARKET_CAP:
                            small_cap_codes.append(code)
                    else:
                        entry['market_cap'] = '未知'
                # 剔除小市值
                for code in small_cap_codes:
                    del ready_pool[code]
                print(f"[市值筛选] 完成 | 保留 {len(ready_pool)} 只，剔除小市值(<40亿) {len(small_cap_codes)} 只")
            except Exception as e:
                print(f"[市值筛选] 查询异常，跳过: {e}")

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
