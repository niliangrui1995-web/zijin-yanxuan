from __future__ import annotations

import time as _time

import pandas as pd

from core.logger import get_logger
from domains.market_calendar import MarketCalendar
from domains.scan.indicator_service import IndicatorService
from domains.scan.vcp_scanner_service import VcpScannerService
from vcp.constants import MIN_MARKET_CAP
from vcp.engine_external import batch_check_institution, batch_check_market_cap

_log = get_logger(__name__)


class BreakoutMonitorService:
    """待突破池与盘中轻量判断服务。"""

    @staticmethod
    def precompute_ready_pool(
        all_data,
        rps120_series,
        rps250_series,
        params,
        sector_manager=None,
        sector_rps_dict=None,
        sector_threshold=70,
        server_pool=None,
        code2name=None,
        progress_callback=None,
        cancelled_checker=None,
    ):
        del server_pool
        try:
            import polars as _pl
        except ImportError:
            _pl = None

        converted = 0
        for code in list(all_data.keys()):
            df = all_data[code]
            if _pl is not None and isinstance(df, _pl.DataFrame):
                pdf = df.to_pandas()
                if "datetime" in pdf.columns:
                    pdf["datetime"] = pd.to_datetime(pdf["datetime"])
                    pdf.set_index("datetime", inplace=True)
                all_data[code] = pdf
                converted += 1
        if converted:
            _log.info(f"[ready_pool] Polars->Pandas converted {converted} stocks")

        ready_pool = {}
        st_filtered = 0
        total_count = len(all_data)
        diag_short = 0
        diag_rps_nan = 0
        diag_ind_fail = 0
        diag_eval_fail = 0

        for idx_code, (code, df) in enumerate(all_data.items()):
            if cancelled_checker and cancelled_checker():
                raise InterruptedError("盘中监控已停止")

            if idx_code % 20 == 0:
                _time.sleep(0.001)

            if progress_callback and idx_code % 200 == 0:
                progress_callback(f"构建待突破池: {idx_code}/{total_count}...")

            if df is None or len(df) < 250:
                diag_short += 1
                continue

            if code2name:
                stock_name = code2name.get(code, "")
                if "ST" in stock_name.upper():
                    st_filtered += 1
                    continue

            r120 = rps120_series.get(code, float("nan"))
            r250 = rps250_series.get(code, float("nan"))
            if pd.isna(r120) or pd.isna(r250):
                diag_rps_nan += 1
                continue

            if "entangle" not in df.columns:
                try:
                    all_data[code] = IndicatorService.calculate_indicators(df.copy())
                    df = all_data[code]
                except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    _log.debug(f"[待突破池] {code} 指标计算异常: {exc}")
                    diag_ind_fail += 1
                    continue

            eval_day = df.index[-1]
            try:
                ok, reason, metrics = VcpScannerService.evaluate_conditions(
                    df,
                    eval_day,
                    float(r120),
                    float(r250),
                    None,
                    params,
                    skip_red_check=True,
                )
            except (AttributeError, IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                _log.debug(f"[待突破池] {code} 条件评估异常: {exc}")
                diag_eval_fail += 1
                continue

            if not ok:
                continue

            sector_info = ""
            if sector_manager and sector_rps_dict:
                sector_ok, sector_info, _ = sector_manager.check_sector_rps(code, sector_rps_dict, sector_threshold)
                if not sector_ok:
                    continue

            curr_idx = len(df) - 1
            vol_slice = df.iloc[max(0, curr_idx - 24) : curr_idx + 1]["volume"]
            vol_ma25 = float(vol_slice.mean()) if len(vol_slice) > 0 else 0

            ready_pool[code] = {
                "box_high": metrics.get("区间最高价", 0),
                "box_low": metrics.get("区间最低点", 0),
                "score": metrics.get("评分", 0),
                "rps_str": metrics.get("RPS强度", ""),
                "vol_ma25": vol_ma25,
                "sector_info": sector_info,
                "institution_tag": "",
                "meta": metrics,
            }

        _log.info(
            f"[待突破池] 预计算完成 | 全量 {len(all_data)} 只 → 候选 {len(ready_pool)} 只（ST 剔除 {st_filtered} 只）"
        )
        _log.debug(
            "[待突破池] 诊断 short=%s rps_nan=%s ind_fail=%s eval_fail=%s",
            diag_short,
            diag_rps_nan,
            diag_ind_fail,
            diag_eval_fail,
        )

        if ready_pool:
            if cancelled_checker and cancelled_checker():
                raise InterruptedError("盘中监控已停止")
            try:
                inst_results = batch_check_institution(list(ready_pool.keys()))
                inst_count = 0
                no_inst_count = 0
                for code, entry in ready_pool.items():
                    inst_info = inst_results.get(code, {})
                    has_inst = inst_info.get("has_institution", False)
                    detail = inst_info.get("detail", "")
                    if has_inst:
                        entry["institution_tag"] = f"✓机构:{detail}"
                        inst_count += 1
                    else:
                        entry["institution_tag"] = "无机构"
                        no_inst_count += 1
                _log.info(f"[机构股东] 标记完成 | 有机构 {inst_count} 只，无机构 {no_inst_count} 只（均保留在池中）")
            except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                _log.error(f"[机构股东] 查询异常，跳过筛选: {exc}")

        if ready_pool:
            if cancelled_checker and cancelled_checker():
                raise InterruptedError("盘中监控已停止")
            try:
                close_prices = {}
                for code in ready_pool:
                    df = all_data.get(code)
                    if df is not None and len(df) > 0:
                        close_prices[code] = float(df.iloc[-1]["close"])
                cap_results = batch_check_market_cap(list(ready_pool.keys()), close_prices=close_prices)
                small_cap_count = 0
                for code, entry in ready_pool.items():
                    cap = cap_results.get(code)
                    if cap and cap > 0:
                        cap_yi = cap / 1e8
                        entry["market_cap"] = f"{cap_yi:.0f}亿"
                        if cap < MIN_MARKET_CAP:
                            entry["small_cap"] = True
                            small_cap_count += 1
                    else:
                        entry["market_cap"] = "未知"
                _log.info(f"[市值标记] 完成 | 共 {len(ready_pool)} 只，其中小市值(<40亿) {small_cap_count} 只（均保留在池中）")
            except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                _log.error(f"[市值筛选] 查询异常，跳过: {exc}")

        return ready_pool

    @staticmethod
    def rt_quick_check(quote, pool_entry):
        rt_close = float(quote.get("close", 0) or 0)
        rt_open = float(quote.get("open", 0) or 0)
        rt_high = float(quote.get("high", 0) or 0)
        rt_low = float(quote.get("low", 0) or 0)
        rt_volume = float(quote.get("volume", 0) or 0)

        box_high = pool_entry["box_high"]
        base_score = pool_entry["score"]
        vol_ma25 = pool_entry["vol_ma25"]

        if rt_close <= 0 or rt_open <= 0 or box_high <= 0:
            return False, "数据异常", 0

        if rt_high > 0 and rt_low > 0 and rt_high == rt_low:
            return False, "一字板(不可交易)", 0

        if rt_close <= rt_open:
            return False, "非红盘", 0

        dist = (box_high - rt_close) / rt_close if rt_close > 0 else 0
        score = base_score

        if dist < 0:
            est_full_vol = BreakoutMonitorService.estimate_full_day_volume(rt_volume)
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
            score += 10
            breakout_status = f"临近突破({dist:.1%})"
        else:
            breakout_status = f"VCP蓄力({dist:.1%})"

        return True, breakout_status, round(score, 1)

    @staticmethod
    def estimate_full_day_volume(current_volume):
        now = MarketCalendar.now("CN")
        hour, minute = now.hour, now.minute

        if hour < 9 or (hour == 9 and minute < 30):
            return 0
        if hour < 11 or (hour == 11 and minute <= 30):
            elapsed = (hour - 9) * 60 + minute - 30
        elif hour < 13:
            elapsed = 120
        elif hour < 15:
            elapsed = 120 + (hour - 13) * 60 + minute
        else:
            elapsed = 240

        elapsed = max(elapsed, 1)
        time_ratio = elapsed / 240.0
        if elapsed < 30:
            return current_volume * 8
        return current_volume / time_ratio
