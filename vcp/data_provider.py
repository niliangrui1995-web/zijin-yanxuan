# data_provider.py - 数据中台门面
from __future__ import annotations

import os
import threading

from core.logger import get_logger
from core.market_calendar import MarketCalendar
from vcp.adjustment_service import AdjustmentService
from vcp.constants import CACHE_DIR, MAX_HISTORY_BARS
from vcp.data_provider_cache import compact_runtime_caches, downcast_memory, prune_rt_quote_cache
from vcp.data_provider_history_mixin import TdxDataProviderHistoryMixin
from vcp.data_provider_local import fetch_from_local_tdx
from vcp.data_provider_realtime_mixin import TdxDataProviderRealtimeMixin
from vcp.local_history_provider import LocalHistoryProvider
from vcp.realtime_quote_provider import RealtimeQuoteProvider
from vcp.utils import _load_tdx_local_config

_log = get_logger(__name__)

RT_QUOTE_CACHE_TTL_SEC = 180.0
RT_QUOTE_CACHE_MAX_ENTRIES = 4096
RT_QUOTE_DEDUP_WINDOW_SEC = 8.5
RT_QUOTE_BATCH_SIZE = 20
RT_QUOTE_MIN_BATCH_SIZE = 5
RT_QUOTE_BATCH_PAUSE_SEC = 0.12


class TdxDataProvider(TdxDataProviderHistoryMixin, TdxDataProviderRealtimeMixin):
    def __init__(self, is_trading_day=None, offline=False, offline_mode=None):
        from pytdx.hq import TdxHq_API

        if offline_mode is not None:
            offline = bool(offline_mode)

        self.TdxHq_API = TdxHq_API
        self.legacy_cache_file = os.path.join(CACHE_DIR, "vcp_tdx_cache_adj.pkl")
        self.legacy_fallback_cache_file = os.path.join(CACHE_DIR, "cache_data_fallback.pkl")
        self.gbbq_cache_file = os.path.join(CACHE_DIR, "gbbq_parsed.json")
        self.legacy_gbbq_cache_file = os.path.join(CACHE_DIR, "gbbq_parsed.pkl")

        self.cache_data = {}
        self.cache_lock = threading.Lock()
        self.thread_local = threading.local()

        self._rt_quote_cache = {}
        self._rt_quote_time = {}
        self._rt_quote_lock = threading.Lock()
        self._rt_quote_cache_ttl_sec = RT_QUOTE_CACHE_TTL_SEC
        self._rt_quote_cache_max_entries = RT_QUOTE_CACHE_MAX_ENTRIES

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
        self._local_gbbq = {}
        self.server_pool = []

        self._adjustment_service = AdjustmentService(self)
        self._local_history_provider = LocalHistoryProvider(self, logger=_log)
        self._realtime_quote_provider = RealtimeQuoteProvider(self, logger=_log)

        if self.tdx_vipdoc:
            _log.info(f"[启动] 已启用通达信本地K线数据: {self.tdx_vipdoc}")
            self._load_local_gbbq()

        if offline:
            _log.warning("[启动] 离线模式启动：跳过联网检测，使用本地数据")
        else:
            _log.info("[启动] A股盘中实时行情改为东方财富接口，跳过旧通达信节点测速")

    def _get_adjustment_service(self) -> AdjustmentService:
        service = getattr(self, "_adjustment_service", None)
        if service is None:
            service = AdjustmentService(self)
            self._adjustment_service = service
        return service

    def _get_local_history_provider(self) -> LocalHistoryProvider:
        service = getattr(self, "_local_history_provider", None)
        if service is None:
            service = LocalHistoryProvider(self, logger=_log)
            self._local_history_provider = service
        return service

    def _get_realtime_quote_provider(self) -> RealtimeQuoteProvider:
        service = getattr(self, "_realtime_quote_provider", None)
        if service is None:
            service = RealtimeQuoteProvider(self, logger=_log)
            self._realtime_quote_provider = service
        return service

    def _prune_rt_quote_cache(self, now: float | None = None) -> int:
        return prune_rt_quote_cache(self, now=now)

    def compact_runtime_caches(self, now: float | None = None) -> dict:
        return compact_runtime_caches(self, now=now)

    def _downcast_memory(self):
        downcast_memory(self, logger=_log)

    def _load_local_gbbq(self, force=False):
        self._local_gbbq = self._get_adjustment_service().load_local_gbbq(force=force)

    def _get_market_code(self, stock_code):
        return self._get_adjustment_service().get_market_code(stock_code)

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
            offline_warn_printed=getattr(self, "_offline_warn_printed", False),
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
        self._get_realtime_quote_provider().archive_runtime(runtime)

    def _ensure_realtime_runtime(self):
        return self._get_realtime_quote_provider().ensure_runtime()

    def _reset_realtime_runtime(
        self,
        reason: str = "",
        *,
        log_warning: bool = True,
        penalize_server: bool = True,
    ):
        self._get_realtime_quote_provider().reset_runtime(
            reason,
            log_warning=log_warning,
            penalize_server=penalize_server,
        )

    def _register_realtime_success(self):
        self._get_realtime_quote_provider().register_success()

    def _enter_realtime_cooldown(self, reason: str, cooldown_sec: float | None = None):
        self._get_realtime_quote_provider().enter_cooldown(reason, cooldown_sec=cooldown_sec)

    def _register_realtime_failure(self, reason: str):
        self._get_realtime_quote_provider().register_failure(reason)

    def _submit_realtime_quote_request(self, params_list, timeout_sec: float):
        return self._get_realtime_quote_provider().submit_request(params_list, timeout_sec)

    def get_realtime_runtime_stats(self) -> dict:
        return self._get_realtime_quote_provider().get_runtime_stats()

    def protect_against_thread_anomaly(self, pytdx_thread_count: int, threshold: int | None = None) -> bool:
        return self._get_realtime_quote_provider().protect_against_thread_anomaly(
            pytdx_thread_count,
            threshold=threshold,
        )

    def _get_thread_api(self):
        if not hasattr(self.thread_local, "api"):
            api = self._create_api_client()
            self._connect_api_to_best_server(api, time_out=5, require_security_count=True, allow_unconnected=True)
            self.thread_local.api = api
        return self.thread_local.api

    def _apply_forward_adjustment(self, api, market, code, df):
        return self._get_adjustment_service().apply_forward_adjustment(api, market, code, df)

    def _fetch_standard_data(self, api, code, count=MAX_HISTORY_BARS):
        return self._get_local_history_provider().fetch_standard_data(api, code, count=count)
