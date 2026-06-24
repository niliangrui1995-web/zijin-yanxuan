# ui/workers.py - 后台工作线程
# 从 main_window_qt.py 拆分出来的 ScanWorker 和 RtScanWorker
import datetime
import math

from PyQt6.QtCore import QThread, pyqtSignal

from app.services.runtime_constants import RPS_CACHE_FILE
from app.services.scan_runtime_service import (
    VCPParams,
    batch_check_market_cap,
    batch_get_finance_info,
    build_rps_matrix,
    precompute_ready_pool,
    quick_check_breakout,
)
from app.services.ui_market_calendar_service import MarketCalendar
from core.exceptions import CacheIOError
from core.json_cache import remove_cache_file, save_json_file
from core.logger import get_logger
from core.sector_rps_helper import enrich_hot_sector_rows, load_sector_rps_snapshot

log = get_logger(__name__)

_QUOTE_PRESSURE_DEFER_SEC = 75.0
_QUOTE_PRESSURE_MIN_PENDING = 100
_QUOTE_PRESSURE_MIN_ELAPSED_MS = 20000.0
_QUOTE_FALLBACK_LAYER_TOKENS = ("sina", "tencent", "fallback", "offline", "stale")


class RtScanWorker(QThread):
    """盘中监控核心工作线程:
    首轮: 加载RPS -> 构建待突破池 -> 拉取实时报价 -> VCP突破检测 -> 发射信号
    后续轮: 复用待突破池 -> 拉取实时报价 -> rt_quick_check轻量检测 -> 发射信号
    """

    rt_result_ready = pyqtSignal(list)  # 每轮完整信号列表
    progress = pyqtSignal(str)  # 状态文字
    scan_count = pyqtSignal(int, int)  # (轮次, 待突破池大小)

    def __init__(self, data_provider, engine, interval=300, rps_threshold=80):
        super().__init__()
        self.data_provider = data_provider
        self.engine = engine
        self.interval = interval
        self.rps_threshold = rps_threshold
        self._is_running = True
        # 缓存(跨轮复用)
        self._ready_pool = None  # 待突破池 {code: entry}
        self._rps120 = None  # RPS120 Series
        self._rps250 = None  # RPS250 Series
        self._all_data = None  # 历史日线数据
        self._scan_count = 0
        self._pool_refresh_interval = 10  # 每 10 轮重建待突破池（动态剔除形态恶化标的）
        self._seen_signals = set()  # 信号去重 (code, status)
        self._signal_details = {}  # 盘中监控信号详情(仅待突破池红盘触发)
        self._special_details = {}  # 关注池实时数据(独立存储,不影响盘中监控)
        self._sector_manager = None  # 板块管理器(首轮创建后缓存)
        self._sector_rps = None  # 板块 RPS 字典(首轮计算后缓存)
        self._cap_cache = {}  # 市值缓存 {code: '71亿'} 跨轮复用
        self._zbg_cache = {}  # 总股本缓存 {code: zongguben}，用于按最新现价动态重算市值
        self._pool_rebuild_pending = False
        self._last_pool_rebuild_defer_log_at = 0.0

    def stop(self):
        self._is_running = False

    @staticmethod
    def _is_persistable_rps_snapshot(valid_count: int) -> bool:
        return int(valid_count or 0) >= 1000

    def _persist_rps_snapshot(self, resolved_date: str, valid_count: int) -> bool:
        if not self._is_persistable_rps_snapshot(valid_count):
            log.warning(f"[盘中] RPS样本过小({int(valid_count or 0)}只)，仅在线程内复用，不覆盖磁盘/全局缓存")
            return False

        try:
            rps_pkg = {"date": resolved_date, "rps120": self._rps120, "rps250": self._rps250}
            save_json_file(RPS_CACHE_FILE, rps_pkg)
            remove_cache_file(RPS_CACHE_FILE.replace(".json", ".pkl"))
            self.engine.set_precomputed_rps(resolved_date, self._rps120, self._rps250)
            log.debug(f"[盘中] RPS 已保存磁盘 ({resolved_date})")
            return True
        except CacheIOError as e:
            log.error(f"[盘中] RPS 磁盘保存失败: {e}")
            return False

    def _cleanup_caches(self):
        """线程退出时释放内存。
        为什么不在 stop() 里做？stop() 是 UI 线程调用的，
        而 _run_one_round() 可能正在 worker 线程中访问这些缓存。
        在同一线程的 run() 退出后调用，彻底避免竞态。
        """
        import gc as _gc

        self._ready_pool = None
        self._rps120 = None
        self._rps250 = None
        self._all_data = None
        self._signal_details = {}
        self._special_details = {}
        self._sector_rps = None
        self._sector_manager = None
        self._cap_cache = {}
        self._zbg_cache = {}
        self._seen_signals = set()
        self._pool_rebuild_pending = False
        _gc.collect()
        log.info("[监控] 线程结束，已释放全部缓存")

    def run(self):
        import time as _time

        import numpy as np

        while self._is_running:
            self._scan_count += 1
            t0 = _time.time()
            try:
                self._run_one_round(np)
            except InterruptedError:
                if not self._is_running:
                    self._cleanup_caches()
                    return
            except (AttributeError, IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
                self.progress.emit(f"盘中扫描异常: {e}")
                log.error(f"[盘中监控] 第{self._scan_count}轮扫描异常: {e}", exc_info=True)

            elapsed = _time.time() - t0
            # 每轮结束后主动回收 Polars 转换等产生的临时对象
            import gc as _gc

            _gc.collect()
            # stop() 可能在本轮执行中被调用：此时直接退出
            if not self._is_running:
                self._cleanup_caches()
                return
            self.progress.emit(f"第{self._scan_count}轮完成(耗时 {elapsed:.1f}s),等待下轮...")

            # 等待间隔(可被中断)
            for _ in range(int(self.interval * 10)):
                if not self._is_running:
                    self._cleanup_caches()
                    return
                _time.sleep(0.1)

    def _ensure_history_data(self) -> bool:
        if self._all_data is not None:
            return True
        self.progress.emit("加载历史日线数据...")
        cache_snapshot = self.data_provider.get_all_valid_data()
        self._all_data = {c: df for c, df in cache_snapshot.items() if df is not None and len(df) >= 60}
        if not self._all_data:
            self.progress.emit("❌ 无历史数据,请先执行 F5 或扫描")
            return False
        return True

    @staticmethod
    def _latest_trade_date_text() -> str:
        try:
            trade_dt = MarketCalendar.get_latest_trade_date()
            if trade_dt:
                return trade_dt.strftime("%Y%m%d")
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
            log.debug(f"[盘中] 获取最近交易日失败: {e}")
        return datetime.date.today().strftime("%Y%m%d")

    def _compute_rps_fallback(self) -> bool:
        try:
            import gc

            today_str = self._latest_trade_date_text()
            rps_matrix = build_rps_matrix(self._all_data, today_str, today_str)
            if not rps_matrix:
                self.progress.emit("❌ RPS 计算失败:无价格数据")
                return False

            d_str = list(rps_matrix.keys())[-1]
            d_rps = rps_matrix[d_str]
            self._rps120 = d_rps.get("rps120", {})
            self._rps250 = d_rps.get("rps250", {})
            valid_count = sum(1 for v in self._rps120.values() if v == v)
            del d_rps, rps_matrix
            gc.collect()

            log.info(f"[盘中] 现算 RPS 完成 ({valid_count} 只)，已释放中间矩阵")
            self._persist_rps_snapshot(d_str, valid_count)
            return True
        except (ImportError, AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
            log.error(f"[盘中] 兜底 RPS 计算异常: {e}")
            self.progress.emit("❌ RPS 计算失败")
            return False

    def _ensure_rps_ready(self) -> bool:
        if self._rps120 is not None and self._rps250 is not None:
            return True
        self.progress.emit("计算全市场 RPS 排名...")
        precomputed_bundle = self.engine.get_precomputed_rps()
        if precomputed_bundle:
            self._rps120 = precomputed_bundle.get("rps120")
            self._rps250 = precomputed_bundle.get("rps250")
            if self._rps120 is not None and self._rps250 is not None:
                log.info(f"[盘中] 加载预计算 RPS (基准日 {precomputed_bundle.get('date', '?')})")
        if self._rps120 is None or self._rps250 is None:
            return self._compute_rps_fallback()
        return True

    def _ready_pool_params(self) -> VCPParams:
        return VCPParams(
            rps_threshold=self.rps_threshold,
            amp_threshold=0.45,
            ma_bind_threshold=0.05,
            high_250_threshold=0.10,
            min_amount_20d=8e7,
            min_history_days=250,
        )

    def _sync_ready_pool_frames(self) -> None:
        for code, df in self._all_data.items():
            if "entangle" not in df.columns:
                continue
            orig = self.data_provider.cache_data.get(code)
            if orig is not None and "entangle" not in orig.columns:
                with self.data_provider.cache_lock:
                    self.data_provider.cache_data[code] = df

    def _log_ready_pool_delta(self, new_pool: dict) -> None:
        if self._ready_pool is None:
            return
        old_codes = set(self._ready_pool.keys())
        new_codes = set(new_pool.keys())
        added = new_codes - old_codes
        removed = old_codes - new_codes
        if added or removed:
            log.info(f"[待突破池] 刷新: +{len(added)} 新增 / -{len(removed)} 剔除 (形态恶化)")

    @staticmethod
    def _stats_int(stats: dict, key: str) -> int:
        try:
            return int(stats.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _stats_float(stats: dict, key: str) -> float:
        try:
            return float(stats.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _stats_time(value) -> float:
        if isinstance(value, (int, float)):
            return float(value or 0.0)
        text = str(value or "").strip()
        if not text:
            return 0.0
        try:
            return datetime.datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
        except (TypeError, ValueError):
            return 0.0

    def _eastmoney_quote_cooldown_left(self, now: float) -> int:
        try:
            cooldown_until = float(getattr(self.data_provider, "_rt_eastmoney_cooldown_until", 0.0) or 0.0)
        except (TypeError, ValueError):
            cooldown_until = 0.0
        return max(0, int(cooldown_until - now))

    def _recent_quote_fallback_pressure(self, now: float) -> tuple[bool, str]:
        stats_getter = getattr(self.data_provider, "get_quote_request_stats", None)
        if not callable(stats_getter):
            return False, ""
        try:
            stats = stats_getter() or {}
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False, ""
        if not isinstance(stats, dict):
            return False, ""

        requested = self._stats_int(stats, "recent_requested_count")
        pending = self._stats_int(stats, "recent_pending_count")
        cache_hits = self._stats_int(stats, "recent_cache_hit_count")
        elapsed_ms = self._stats_float(stats, "recent_elapsed_ms")
        status = str(stats.get("recent_status") or "").lower()
        source_layers = [
            str(layer or "").strip().lower()
            for layer in (stats.get("recent_source_layers") or [])
            if str(layer or "").strip()
        ]
        ended_at = self._stats_time(stats.get("recent_ended_at_ts") or stats.get("recent_ended_at"))
        cooldown_left = self._eastmoney_quote_cooldown_left(now)
        recent_enough = (ended_at > 0 and 0 <= now - ended_at <= _QUOTE_PRESSURE_DEFER_SEC) or cooldown_left > 0
        fallback_or_degraded = cooldown_left > 0 or "fallback" in status or "partial" in status or any(
            any(token in layer for token in _QUOTE_FALLBACK_LAYER_TOKENS) for layer in source_layers
        )
        heavy_network = (
            pending >= _QUOTE_PRESSURE_MIN_PENDING
            or (requested >= _QUOTE_PRESSURE_MIN_PENDING and cache_hits <= max(1, requested // 10))
            or (elapsed_ms >= _QUOTE_PRESSURE_MIN_ELAPSED_MS and pending >= 40)
        )
        if not (recent_enough and fallback_or_degraded and heavy_network):
            return False, ""

        layer_text = "/".join(source_layers) if source_layers else status or "fallback"
        return True, f"报价回退压力 pending={pending}/{requested} cache={cache_hits} elapsed={elapsed_ms:.0f}ms source={layer_text}"

    def _refresh_ready_pool_if_needed(self, time_module) -> None:
        need_rebuild = (
            self._ready_pool is None
            or self._pool_rebuild_pending
            or self._scan_count % self._pool_refresh_interval == 1
        )
        if not need_rebuild:
            return

        if self._ready_pool is not None:
            now = time_module.time()
            should_defer, reason = self._recent_quote_fallback_pressure(now)
            if should_defer:
                self._pool_rebuild_pending = True
                if (now - self._last_pool_rebuild_defer_log_at) >= _QUOTE_PRESSURE_DEFER_SEC:
                    self._last_pool_rebuild_defer_log_at = now
                    log.info(
                        f"[待突破池] 第{self._scan_count}轮重建延后: {reason}; "
                        f"继续使用现有池 {len(self._ready_pool)} 只，下轮再尝试"
                    )
                return

        label = "首轮构建" if self._ready_pool is None else f"第{self._scan_count}轮刷新"
        self.progress.emit(f"{label}待突破池...")
        t0 = time_module.time()
        new_pool = precompute_ready_pool(
            self._all_data,
            self._rps120,
            self._rps250,
            self._ready_pool_params(),
            code2name=self.data_provider.code2name,
            progress_callback=lambda msg: self.progress.emit(msg),
            cancelled_checker=lambda: not self._is_running,
        )
        self._sync_ready_pool_frames()
        self._log_ready_pool_delta(new_pool)
        self._ready_pool = new_pool
        self._pool_rebuild_pending = False
        log.info(f"[待突破池] {label}: {len(self._ready_pool)} 只 ({time_module.time() - t0:.1f}s)")

    @staticmethod
    def _watchlist_codes() -> set[str]:
        from app.services.ui_watchlist_service import watchlist_vm

        return set(watchlist_vm.get_all_codes())

    def _prune_runtime_caches(self, special_codes: set[str]) -> None:
        for code in [c for c in self._signal_details if c not in self._ready_pool]:
            del self._signal_details[code]
        self._seen_signals = {(code, status) for code, status in self._seen_signals if code in self._ready_pool}
        for code in [c for c in self._special_details if c not in special_codes]:
            del self._special_details[code]

        active_runtime_codes = set(self._ready_pool.keys()) | special_codes
        for code in [c for c in self._cap_cache if c not in active_runtime_codes]:
            del self._cap_cache[code]
        for code in [c for c in self._zbg_cache if c not in active_runtime_codes]:
            del self._zbg_cache[code]

    def _codes_to_fetch(self, special_codes: set[str]) -> list[str]:
        codes_to_fetch = list(self._ready_pool.keys())
        for code in special_codes:
            if code not in codes_to_fetch:
                codes_to_fetch.append(code)
        return codes_to_fetch

    def _fetch_realtime_quotes(self, special_codes: set[str]):
        codes_to_fetch = self._codes_to_fetch(special_codes)
        self.progress.emit(f"第{self._scan_count}轮:拉取 {len(codes_to_fetch)} 只报价...")
        quotes = self.data_provider.fetch_realtime_quotes_batch(codes_to_fetch)
        if not quotes:
            self.progress.emit("实时报价获取失败")
            return None
        return quotes

    def _refresh_zbg_cache(self, quotes: dict) -> None:
        codes_need_zbg = [code for code in quotes.keys() if code not in self._zbg_cache]
        if not codes_need_zbg:
            return
        try:
            finance_data = batch_get_finance_info(codes_need_zbg)
            for code in codes_need_zbg:
                info = finance_data.get(code, {}) if isinstance(finance_data, dict) else {}
                zbg = float(info.get("zongguben", 0) or 0)
                if zbg > 0:
                    self._zbg_cache[code] = zbg
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
            log.debug(f"[盘中] 总股本缓存刷新失败: {e}")

    def _prepare_realtime_quotes(self):
        special_codes = self._watchlist_codes()
        self._prune_runtime_caches(special_codes)
        quotes = self._fetch_realtime_quotes(special_codes)
        if not quotes:
            return None, special_codes
        self._refresh_zbg_cache(quotes)
        return quotes, special_codes

    def _format_dynamic_cap(self, code: str, rt_price: float, fallback: str = "") -> str:
        zbg = float(self._zbg_cache.get(code, 0) or 0)
        if zbg > 0 and rt_price > 0:
            return f"{(zbg * rt_price) / 1e8:.0f}亿"
        return fallback or ""

    def _quote_price_context(self, code: str, quote: dict) -> tuple[float, float]:
        last_close = float(quote.get("last_close", 0) or 0)
        rt_close = float(quote.get("close", 0) or 0)
        if last_close <= 0:
            hist_df = self.data_provider.get_data(code)
            if hist_df is not None and len(hist_df) > 0:
                last_close = float(hist_df.iloc[-2]["close"]) if len(hist_df) > 1 else float(hist_df.iloc[-1]["close"])
        if rt_close <= 0 and last_close > 0:
            rt_close = last_close
            quote["close"] = rt_close
        pct = ((rt_close / last_close) - 1) * 100 if last_close > 0 and rt_close > 0 else 0
        return rt_close, pct

    @staticmethod
    def _special_params() -> VCPParams:
        return VCPParams(
            rps_threshold=0,
            amp_threshold=2.0,
            ma_bind_threshold=0.30,
            high_250_threshold=0.50,
            min_amount_20d=0,
            min_history_days=60,
        )

    def _evaluate_special_status(self, code: str, quote: dict, r120: float, r250: float) -> tuple[str, dict, str]:
        has_rps = not (math.isnan(r120) or math.isnan(r250))
        rps_display = f"{r120:.0f}/{r250:.0f}" if has_rps else "--/--"
        rt_df = self.data_provider.build_realtime_df(code, quote)
        if not (has_rps and rt_df is not None and len(rt_df) >= 60):
            return "跟踪中", {"评分": "--", "RPS强度": rps_display}, rps_display

        eval_day = rt_df.index[-1]
        ok, reason, metrics = self.engine.evaluate_conditions(
            rt_df, eval_day, float(r120), float(r250), None, self._special_params()
        )
        if ok:
            return "触发买点 · " + metrics.get("突破状态", "突破"), metrics, rps_display
        return f"未触发 · 跟踪（{reason.split(' | ')[0]}）", {"评分": "--", "RPS强度": rps_display}, rps_display

    def _build_special_signal(self, code, quote, pool_entry, r120, r250, rt_close, pct) -> dict:
        status, metrics, rps_display = self._evaluate_special_status(code, quote, r120, r250)
        cap = self._format_dynamic_cap(code, rt_close, pool_entry.get("market_cap", "") if pool_entry else "")
        sector = pool_entry.get("sector_info", "--") if pool_entry else "--"
        sig = {
            "时间": datetime.datetime.now().strftime("%H:%M"),
            "代码": code,
            "名称": self.data_provider.code2name.get(code, code),
            "现价": f"{quote['close']:.2f}",
            "涨幅%": f"{pct:+.2f}%",
            "评分": metrics.get("评分", "--"),
            "RPS强度": rps_display,
            "市值": cap,
            "突破状态": status,
            "热点板块": sector,
            "区间振幅": metrics.get("区间振幅", ""),
            "_is_special": True,
        }
        if isinstance(metrics, dict):
            for key, value in metrics.items():
                if key not in sig and value is not None:
                    sig[key] = value
        return sig

    def _notify_new_breakout(self, code, quote, pct, breakout_status) -> None:
        sig_key = (code, breakout_status)
        if sig_key in self._seen_signals:
            return
        self._seen_signals.add(sig_key)
        stock_name = self.data_provider.code2name.get(code, "")
        log.info(f"[盘中] 🔥 {code} {stock_name} | {quote['close']:.2f} | {pct:+.2f}% | {breakout_status}")
        try:
            from ui.components.notification_service import notify_breakout

            notify_breakout(code, stock_name, f"{pct:+.2f}% {breakout_status}")
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
            log.debug(f"[盘中] 桌面通知发送失败: {e}")

    def _build_breakout_signal(self, code, quote, pool_entry, r120, r250, rt_close, pct, breakout_status, score) -> dict:
        meta = pool_entry.get("meta", {})
        prev_sig = self._signal_details.get(code, {})
        sector = pool_entry.get("sector_info", "") or prev_sig.get("热点板块", "")
        cap_fallback = pool_entry.get("market_cap", "") or prev_sig.get("市值", "")
        sig = {
            "时间": datetime.datetime.now().strftime("%H:%M"),
            "代码": code,
            "名称": self.data_provider.code2name.get(code, code),
            "现价": f"{quote['close']:.2f}",
            "涨幅%": f"{pct:+.2f}%",
            "评分": score,
            "RPS强度": pool_entry.get("rps_str", f"{r120:.0f}/{r250:.0f}"),
            "市值": self._format_dynamic_cap(code, rt_close, cap_fallback),
            "突破状态": breakout_status,
            "热点板块": sector,
            "区间振幅": meta.get("区间振幅", ""),
        }
        if isinstance(meta, dict):
            for key, value in meta.items():
                if key not in sig and value is not None:
                    sig[key] = value
        return sig

    def _collect_round_signals(self, quotes: dict, special_codes: set[str]) -> list[dict]:
        new_signals = []
        for code, quote in quotes.items():
            pool_entry = self._ready_pool.get(code)
            r120 = self._rps120.get(code, float("nan"))
            r250 = self._rps250.get(code, float("nan"))
            rt_close, pct = self._quote_price_context(code, quote)

            if code in special_codes:
                sig = self._build_special_signal(code, quote, pool_entry, r120, r250, rt_close, pct)
                new_signals.append(sig)
                self._special_details[code] = sig

            if math.isnan(r120) or math.isnan(r250) or pool_entry is None:
                continue
            ok, breakout_status, score = quick_check_breakout(quote, pool_entry)
            if not ok:
                continue

            self._notify_new_breakout(code, quote, pct, breakout_status)
            sig = self._build_breakout_signal(code, quote, pool_entry, r120, r250, rt_close, pct, breakout_status, score)
            new_signals.append(sig)
            self._signal_details[code] = sig
        return new_signals

    def _all_signal_details(self) -> dict:
        return {**self._signal_details, **self._special_details}

    def _refresh_dynamic_caps(self, all_sigs: dict, quotes: dict) -> None:
        for sig in all_sigs.values():
            code = sig.get("代码", "")
            quote = quotes.get(code, {}) if code else {}
            rt_price = float(quote.get("close", 0) or 0)
            if rt_price <= 0:
                try:
                    rt_price = float(str(sig.get("现价", "")).replace(",", ""))
                except (ValueError, TypeError):
                    rt_price = 0
            cap_val = self._format_dynamic_cap(code, rt_price, sig.get("市值", ""))
            if cap_val:
                sig["市值"] = cap_val

    def _fill_missing_caps(self, all_sigs: dict, quotes: dict) -> None:
        codes_need_cap = [
            sig["代码"] for sig in all_sigs.values() if not sig.get("市值") and sig["代码"] not in self._cap_cache
        ]
        if not codes_need_cap:
            return
        try:
            self.progress.emit(f"补全 {len(codes_need_cap)} 只市值...")
            close_prices = {}
            for code in codes_need_cap:
                quote = quotes.get(code)
                rt_price = float(quote.get("close", 0) or 0) if quote else 0
                if rt_price <= 0:
                    hist = self.data_provider.get_data(code)
                    if hist is not None and len(hist) > 0:
                        rt_price = float(hist.iloc[-1]["close"])
                close_prices[code] = rt_price
            cap_results = batch_check_market_cap(codes_need_cap, close_prices=close_prices)
            for code in codes_need_cap:
                cap = cap_results.get(code)
                self._cap_cache[code] = f"{cap / 1e8:.0f}亿" if cap and cap > 0 else ""
        except (KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
            log.error(f"[盘中] 市值补全异常: {e}")

    def _ensure_sector_snapshot(self) -> None:
        if self._sector_manager is not None:
            return
        latest_trade_date = MarketCalendar.get_latest_trade_date().strftime("%Y%m%d")
        try:
            self._sector_manager, self._sector_rps, _, source = load_sector_rps_snapshot(
                self.data_provider,
                self._all_data,
                target_date=latest_trade_date,
                logger=log,
            )
            if self._sector_manager and self._sector_rps:
                log.info(f"[盘中] 热点板块补全就绪 ({source})")
            else:
                self._sector_manager = False
                self._sector_rps = {}
        except (AttributeError, IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
            log.error(f"[盘中] 板块管理器创建异常: {e}")
            self._sector_manager = False

    def _enrich_sector_fields(self, all_sigs: dict) -> None:
        if self._sector_manager and self._sector_rps:
            for sig in all_sigs.values():
                code = sig["代码"]
                if not sig.get("市值") and code in self._cap_cache:
                    sig["市值"] = self._cap_cache[code]
            enrich_hot_sector_rows(
                all_sigs.values(),
                self._sector_manager,
                self._sector_rps,
                logger=log,
            )
            return

        for sig in all_sigs.values():
            if not sig.get("市值") and sig["代码"] in self._cap_cache:
                sig["市值"] = self._cap_cache[sig["代码"]]
            sig["热点板块"] = sig.get("热点板块") or "--"

    def _complete_signal_metadata(self, quotes: dict) -> None:
        all_sigs = self._all_signal_details()
        self._refresh_dynamic_caps(all_sigs, quotes)
        self._fill_missing_caps(all_sigs, quotes)
        self._ensure_sector_snapshot()
        self._enrich_sector_fields(all_sigs)

    def _emit_round_results(self, pool_size: int, quotes: dict, new_signals: list[dict]) -> None:
        all_signals = list(self._signal_details.values()) + list(self._special_details.values())
        log.info(
            f"[盘中] 第{self._scan_count}轮 | 池 {pool_size} | 报价 {len(quotes)} | 新信号 {len(new_signals)} | 累计 {len(all_signals)}"
        )
        if self._scan_count % 12 == 0:
            log.info(
                f"[盘中] heartbeat ready_pool={pool_size} "
                f"signal_cache={len(self._signal_details)} "
                f"watchlist_cache={len(self._special_details)} "
                f"cap_cache={len(self._cap_cache)} "
                f"zbg_cache={len(self._zbg_cache)}"
            )
        self.rt_result_ready.emit(all_signals)

    def _run_one_round(self, _np):
        import time as _time

        if not self._ensure_history_data():
            return
        if not self._ensure_rps_ready():
            return

        self._refresh_ready_pool_if_needed(_time)
        pool_size = len(self._ready_pool)
        self.scan_count.emit(self._scan_count, pool_size)

        quotes, special_codes = self._prepare_realtime_quotes()
        if not quotes:
            return

        new_signals = self._collect_round_signals(quotes, special_codes)
        self._complete_signal_metadata(quotes)
        self._emit_round_results(pool_size, quotes, new_signals)
