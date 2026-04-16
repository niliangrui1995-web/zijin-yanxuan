# data_provider.py - 数据中台 # 从 vcp_hunter.pyw 提取 TdxDataProvider 类，逐步清理旧 pytdx 实时链路
import os
import threading
import time

from core.logger import get_logger
from core.market_calendar import MarketCalendar
from vcp.constants import CACHE_DIR, MAX_HISTORY_BARS
from vcp.data_provider_cache import compact_runtime_caches, downcast_memory, prune_rt_quote_cache
from vcp.data_provider_history_mixin import TdxDataProviderHistoryMixin
from vcp.data_provider_local import (
    apply_forward_adjustment,
    fetch_from_local_tdx,
    get_market_code,
    load_local_gbbq,
)
from vcp.data_provider_realtime_mixin import TdxDataProviderRealtimeMixin
from vcp.realtime_quote_runtime import RealtimeQuoteRuntime
from vcp.utils import _load_tdx_local_config

_log = get_logger(__name__)

RT_QUOTE_CACHE_TTL_SEC = 180.0
RT_QUOTE_CACHE_MAX_ENTRIES = 4096
RT_QUOTE_DEDUP_WINDOW_SEC = 8.5
RT_QUOTE_BATCH_SIZE = 20
RT_QUOTE_MIN_BATCH_SIZE = 5
RT_QUOTE_BATCH_PAUSE_SEC = 0.12
RT_EASTMONEY_COOLDOWN_SEC = 120.0


class TdxDataProvider(TdxDataProviderHistoryMixin, TdxDataProviderRealtimeMixin):
    def __init__(self, is_trading_day=None, offline=False, offline_mode=None):
        from pytdx.hq import TdxHq_API
        # Backward compatibility: keep accepting legacy keyword `offline_mode`.
        if offline_mode is not None:
            offline = bool(offline_mode)
        self.TdxHq_API = TdxHq_API
        self.legacy_cache_file = os.path.join(CACHE_DIR, 'vcp_tdx_cache_adj.pkl')
        self.legacy_fallback_cache_file = os.path.join(CACHE_DIR, 'cache_data_fallback.pkl')
        self.gbbq_cache_file = os.path.join(CACHE_DIR, 'gbbq_parsed.json')
        self.legacy_gbbq_cache_file = os.path.join(CACHE_DIR, 'gbbq_parsed.pkl')
        self.cache_data = {}
        self.cache_lock = threading.Lock()
        self.thread_local = threading.local()
        # DataLoader 防抖微缓存 (仅对实时行情生效, 生命期 500ms)
        self._rt_quote_cache = {}
        self._rt_quote_time = {}
        self._rt_quote_lock = threading.Lock()
        self._rt_api_call_timeout_sec = 8.0
        self._rt_runtime_lock = threading.RLock()
        self._rt_runtime = None
        self._rt_runtime_failure_threshold = 3
        self._rt_runtime_cooldown_sec = 300.0
        self._rt_runtime_cooldown_until = 0.0
        self._rt_runtime_consecutive_failures = 0
        self._rt_runtime_last_success_at = 0.0
        self._rt_runtime_reconnect_archived = 0
        self._rt_runtime_last_error = ""
        self._rt_runtime_thread_threshold = 4
        self._rt_runtime_dedup_window_sec = RT_QUOTE_DEDUP_WINDOW_SEC
        self._rt_quote_batch_size = RT_QUOTE_BATCH_SIZE
        self._rt_quote_min_batch_size = RT_QUOTE_MIN_BATCH_SIZE
        self._rt_quote_batch_pause_sec = RT_QUOTE_BATCH_PAUSE_SEC
        self._rt_last_network_probe = {}
        self._rt_last_pressure_log_at = 0.0
        self._rt_eastmoney_cooldown_until = 0.0
        self._rt_eastmoney_last_error = ""
        self._rt_last_fallback_log_at = 0.0
        self.code2name = {}
        self._offline = offline
        self._is_trading_day = (
            is_trading_day
            if callable(is_trading_day)
            else (lambda d=None: MarketCalendar.is_trade_day(d, market="CN"))
        )
        self.tdx_vipdoc = _load_tdx_local_config()
        # 预加载本地 gbbq (股本变迁/除权除息) 数据
        self._local_gbbq = {}  # {code: DataFrame}
        if self.tdx_vipdoc:
            _log.info(f"[启动] 已启用通达信本地K线数据: {self.tdx_vipdoc}")
            self._load_local_gbbq()
        self.server_pool = []
        if offline:
            _log.warning("[启动] 离线模式启动：跳过联网检测，使用本地数据")
        else:
            _log.info("[启动] A股盘中实时行情改为东方财富接口，跳过旧通达信节点测速")

    def _prune_rt_quote_cache(self, now: float | None = None) -> int:
        return prune_rt_quote_cache(self, now=now)

    def compact_runtime_caches(self, now: float | None = None) -> dict:
        return compact_runtime_caches(self, now=now)

    def _downcast_memory(self):
        downcast_memory(self, logger=_log)

    def _load_local_gbbq(self, force=False):
        self._local_gbbq = load_local_gbbq(
            self.tdx_vipdoc,
            self.gbbq_cache_file,
            self.legacy_gbbq_cache_file,
            self._local_gbbq,
            force=force,
        )

    def _get_market_code(self, stock_code):
        return get_market_code(stock_code)

    def _is_before_930_today(self):
        now = MarketCalendar.now("CN")
        return now.hour < 9 or (now.hour == 9 and now.minute < 30)

    def _is_after_1500_today(self):
        return MarketCalendar.now("CN").hour >= 15

    def _fetch_from_local_tdx(self, code):
        df, self._offline_warn_printed = fetch_from_local_tdx(
            code,
            tdx_vipdoc=self.tdx_vipdoc,
            offline=self._offline,
            server_pool=self.server_pool,
            local_gbbq=self._local_gbbq,
            offline_warn_printed=getattr(self, '_offline_warn_printed', False),
        )
        return df

    def _create_api_client(self):
        return self.TdxHq_API(auto_retry=False, heartbeat=False)

    def _connect_api_to_best_server(
        self,
        api,
        *,
        time_out: float = 5,
        require_security_count: bool = True,
        allow_unconnected: bool = False,
    ):
        last_error = None
        for ip, port in self.server_pool:
            try:
                if not api.connect(ip, port, time_out=time_out):
                    last_error = ConnectionError(f"连接节点返回 False: {ip}:{port}")
                    continue
                if require_security_count and api.get_security_count(0) <= 0:
                    last_error = ConnectionError(f"节点返回的证券数量无效: {ip}:{port}")
                    try:
                        api.disconnect()
                    except (AttributeError, OSError, RuntimeError, TypeError):
                        pass
                    continue
                return (ip, port)
            except (TimeoutError, OSError, ConnectionError, ValueError) as exc:
                last_error = exc
                _log.debug(f"[网络] 连接行情节点失败 {ip}:{port} - {exc}")
            try:
                api.disconnect()
            except (AttributeError, OSError, RuntimeError, TypeError):
                pass

        if allow_unconnected:
            return None
        if last_error is not None:
            raise ConnectionError("无法连接任何 pytdx 行情节点") from last_error
        raise ConnectionError("pytdx 行情节点池为空")

    def _deprioritize_server(self, server, reason: str = ""):
        if not server or len(self.server_pool) <= 1:
            return
        server = tuple(server)
        if server not in self.server_pool:
            return

        new_pool = [item for item in self.server_pool if item != server]
        new_pool.append(server)
        if new_pool != self.server_pool:
            self.server_pool = new_pool
            if reason:
                _log.debug(f"[网络] 节点降权 {server[0]}:{server[1]} - {reason}")

    def _archive_realtime_runtime(self, runtime):
        if runtime is None:
            return
        try:
            stats = runtime.snapshot()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return
        self._rt_runtime_last_success_at = max(
            float(self._rt_runtime_last_success_at or 0),
            float(stats.get("last_success_at") or 0),
        )
        self._rt_runtime_reconnect_archived += int(stats.get("reconnect_count") or 0)

    def _ensure_realtime_runtime(self):
        now = time.time()
        if now < float(self._rt_runtime_cooldown_until or 0):
            remaining = max(1, int(self._rt_runtime_cooldown_until - now))
            raise TimeoutError(f"实时行情冷却中，剩余 {remaining}s")

        with self._rt_runtime_lock:
            runtime = self._rt_runtime
            if runtime is not None and runtime.is_alive():
                return runtime
            if runtime is not None:
                self._archive_realtime_runtime(runtime)
                self._rt_runtime = None
            runtime = RealtimeQuoteRuntime(self, _log)
            self._rt_runtime = runtime
            return runtime

    def _reset_realtime_runtime(
        self,
        reason: str = "",
        *,
        log_warning: bool = True,
        penalize_server: bool = True,
    ):
        runtime = None
        runtime_stats = {}
        with self._rt_runtime_lock:
            runtime = self._rt_runtime
            self._rt_runtime = None

        if runtime is not None:
            try:
                runtime_stats = runtime.snapshot()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                runtime_stats = {}
            self._archive_realtime_runtime(runtime)
            runtime.close()
        failed_server = runtime_stats.get("server")
        if penalize_server and failed_server:
            self._deprioritize_server(failed_server, reason)
        if reason:
            self._rt_runtime_last_error = reason
            if log_warning:
                _log.warning(f"[实时行情] {reason}")

    def _register_realtime_success(self):
        self._rt_runtime_consecutive_failures = 0
        self._rt_runtime_last_error = ""
        self._rt_runtime_cooldown_until = 0.0
        self._rt_runtime_last_success_at = max(
            float(self._rt_runtime_last_success_at or 0),
            time.time(),
        )

    def _enter_realtime_cooldown(self, reason: str, cooldown_sec: float | None = None):
        cooldown_sec = (
            float(cooldown_sec)
            if cooldown_sec is not None
            else float(self._rt_runtime_cooldown_sec)
        )
        self._rt_runtime_cooldown_until = time.time() + cooldown_sec
        self._rt_runtime_last_error = reason
        self._reset_realtime_runtime(reason, log_warning=False)
        _log.error(f"[实时行情] 进入冷却 {int(cooldown_sec)}s: {reason}")

    def _register_realtime_failure(self, reason: str):
        self._rt_runtime_consecutive_failures += 1
        self._rt_runtime_last_error = reason
        if self._rt_runtime_consecutive_failures >= self._rt_runtime_failure_threshold:
            self._enter_realtime_cooldown(reason)
            return
        self._reset_realtime_runtime(reason)

    def _submit_realtime_quote_request(self, params_list, timeout_sec: float):
        runtime = self._ensure_realtime_runtime()
        quotes = runtime.request(params_list, timeout_sec)
        runtime_stats = runtime.snapshot()
        self._rt_runtime_last_success_at = max(
            float(self._rt_runtime_last_success_at or 0),
            float(runtime_stats.get("last_success_at") or 0),
        )
        return quotes

    def get_realtime_runtime_stats(self) -> dict:
        with self._rt_runtime_lock:
            runtime = self._rt_runtime

        runtime_stats = runtime.snapshot() if runtime is not None else {}
        return {
            "inflight": int(runtime_stats.get("inflight") or 0),
            "last_success_at": max(
                float(self._rt_runtime_last_success_at or 0),
                float(runtime_stats.get("last_success_at") or 0),
            ),
            "consecutive_failures": int(self._rt_runtime_consecutive_failures or 0),
            "reconnect_count": int(self._rt_runtime_reconnect_archived or 0)
            + int(runtime_stats.get("reconnect_count") or 0),
            "cooldown_until": float(self._rt_runtime_cooldown_until or 0),
            "worker_alive": bool(runtime_stats.get("worker_alive")),
            "last_error": self._rt_runtime_last_error,
        }

    def protect_against_thread_anomaly(self, pytdx_thread_count: int, threshold: int | None = None) -> bool:
        threshold = int(threshold or self._rt_runtime_thread_threshold)
        if pytdx_thread_count <= threshold:
            return False
        reason = f"pytdx 线程异常: {pytdx_thread_count}>{threshold}"
        self._enter_realtime_cooldown(reason)
        return True

    def _get_thread_api(self):
        if not hasattr(self.thread_local, "api"):
            api = self._create_api_client()
            self._connect_api_to_best_server(api, time_out=5, require_security_count=True, allow_unconnected=True)
            self.thread_local.api = api
        return self.thread_local.api

    def _apply_forward_adjustment(self, api, market, code, df):
        return apply_forward_adjustment(api, market, code, df, self._local_gbbq)

    def _fetch_standard_data(self, api, code, count=MAX_HISTORY_BARS):
        import polars as pl
        market = self._get_market_code(code)
        if self.tdx_vipdoc:
            local_df = self._fetch_from_local_tdx(code)
            if local_df is not None and len(local_df) >= 250:
                try:
                    local_df = self._apply_forward_adjustment(api, market, code, local_df)
                    if 'vol' in local_df.columns:
                        local_df = local_df.rename({'vol': 'volume'})
                    return local_df
                except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as e:
                    _log.error(f"[数据中台] 本地 {code} 复权失败，改用网络: {e}")
            elif local_df is not None and len(local_df) < 250:
                _log.info(f"[缓存] 本地日线 {code}: 共 {len(local_df)} 条")
            # 修复: .height 是 Polars 属性，Pandas 无此属性，统一用 len()
            elif self.tdx_vipdoc and (local_df is None or len(local_df) == 0):
                _log.error(f"[数据中台] 本地日线 {code} 异常，改用网络")
        for _ in range(2):
            try:
                data = api.get_security_bars(9, market, code, 0, count)
                if data and len(data) > 0:
                    df = pl.DataFrame(data)
                    # Convert 'datetime' (e.g. "2024-05-12 15:00") to Date
                    if 'datetime' in df.columns:
                        if df.schema['datetime'] != pl.Date:
                            df = df.with_columns(
                                pl.col('datetime').str.strptime(pl.Datetime, "%Y-%m-%d %H:%M", strict=False)
                                .cast(pl.Date)
                            ).sort('datetime', descending=False)
                    # 联网路径也需要转成 Pandas 再传给 _apply_forward_adjustment
                    # 因为 _apply_forward_adjustment 已改为纯 Pandas 实现
                    if hasattr(df, 'to_pandas'):
                        df = df.to_pandas()
                        if 'datetime' in df.columns:
                            df = df.set_index('datetime')
                    df = self._apply_forward_adjustment(api, market, code, df)
                    if 'vol' in df.columns:
                        df = df.rename(columns={'vol': 'volume'})
                    return df
            except ValueError as ve:
                raise ve
            except (ConnectionError, KeyError, OSError, RuntimeError, TimeoutError, TypeError) as e:
                _log.error(f"[数据中台] 拉取 {code} 历史数据失败: {e}")
        return None
