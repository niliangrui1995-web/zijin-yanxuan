from PyQt6.QtCore import QObject, QTimer, pyqtSlot
from core.event_bus import event_bus
from core.task_manager import task_manager
from core.logger import get_logger
import re

log = get_logger(__name__)
_A_SHARE_CODE_RE = re.compile(r"^\d{6}$")

class CentralQuotesService(QObject):
    """
    统一的中央实时报价广播站
    解决痛点：原本各Tab独立维护Timer查报价，导致网络I/O重叠卡死。
    现在由本服务统一3秒一并查询全局所需的报价，提取所有Tab要看盘的代码合集，再向event_bus发车。
    """
    def __init__(self, main_window, data_provider):
        super().__init__(main_window)
        self.main_window = main_window
        self.data_provider = data_provider
        self._closed = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._trigger_fetch)
        # 盘中刷新频率：10秒（兼顾基本盯盘需求与极低系统消耗）
        self._timer.start(10000)
        self._is_fetching = False
        # 防止后台任务 hang 住导致 _is_fetching 永不释放，记录开始时间做超时保护
        self._fetch_start_time = 0.0

        # 熔断器：连续失败 N 次后暂停轮询，冷却一段时间再恢复
        # 防止网络断开时每 10 秒打一次必然失败的请求，白费 CPU 和日志
        self._consecutive_failures = 0
        self._circuit_breaker_cooldown = 0
        self._FAILURE_THRESHOLD = 3   # 连续失败 3 次后触发熔断
        self._COOLDOWN_TICKS = 3      # 熔断后跳过 3 个周期（约 30 秒）
        self._fetch_generation = 0
        self._tick_count = 0
        self._heartbeat_every_ticks = 6

    @property
    def _is_market_active(self):
        from core.market_calendar import MarketCalendar
        return MarketCalendar.is_market_active()

    def _get_all_active_codes(self) -> set:
        codes = set()
        mw = self.main_window

        def _normalize_a_code(code):
            code = str(code).strip()
            return code if _A_SHARE_CODE_RE.match(code) else None
        
        def _extract(model_data):
            for r in model_data:
                c = _normalize_a_code(r.get("代码"))
                if c:
                    codes.add(c)

        if hasattr(mw, 'tab_scan') and mw.tab_scan.source_model:
            _extract(mw.tab_scan.source_model.row_data)
            
        if hasattr(mw, 'tab_rt') and mw.tab_rt.source_model:
            _extract(mw.tab_rt.source_model.row_data)
            
        if hasattr(mw, 'tab_watchlist') and getattr(mw.tab_watchlist, 'model', None):
            _extract(mw.tab_watchlist.model.row_data)
            
        if hasattr(mw, 'tab_foreign_block') and hasattr(mw.tab_foreign_block, '_block_trade_codes'):
            for c in mw.tab_foreign_block._block_trade_codes:
                c_norm = _normalize_a_code(c)
                if c_norm:
                    codes.add(c_norm)

        if hasattr(mw, 'tab_na_daily') and getattr(mw.tab_na_daily, 'model', None):
            _extract(mw.tab_na_daily.model.row_data)

        if hasattr(mw, 'tab_earnings') and getattr(mw.tab_earnings, 'model', None):
            _extract(mw.tab_earnings.model.row_data)
            
        if hasattr(mw, 'tab_lhb') and getattr(mw.tab_lhb, 'model', None):
            _extract(mw.tab_lhb.model.row_data)
            
        return codes

    def _record_failure(self, reason: str):
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._FAILURE_THRESHOLD:
            self._circuit_breaker_cooldown = self._COOLDOWN_TICKS
            log.warning(
                f"[报价站] {reason}，连续失败 {self._consecutive_failures} 次，"
                f"暂停轮询 {self._COOLDOWN_TICKS * 10} 秒后自动恢复"
            )

    def _run_maintenance(self, active_codes_count: int | None = None):
        stats = {}
        if self.data_provider is not None:
            try:
                stats = self.data_provider.compact_runtime_caches()
            except Exception as exc:
                log.debug(f"[报价站] 运行时缓存清理失败: {exc}")

        if self._tick_count % self._heartbeat_every_ticks != 0:
            return

        log.info(
            "[报价站] heartbeat "
            f"active_codes={active_codes_count if active_codes_count is not None else '-'} "
            f"rt_cache={stats.get('rt_quote_cache_size', 0)} "
            f"history={stats.get('history_symbol_count', 0)} "
            f"failures={self._consecutive_failures} "
            f"cooldown={self._circuit_breaker_cooldown}"
        )

    @pyqtSlot()
    def _trigger_fetch(self):
        if self._closed:
            return
        self._tick_count += 1
        if self._is_fetching:
            # 超时保护：如果上一轮任务 hang 了超过 30 秒，强制释放锁避免报价站永久冻结
            import time
            if self._fetch_start_time > 0 and (time.time() - self._fetch_start_time) > 30:
                task_abandoned = task_manager.abandon_task("central_quotes")
                log.warning(
                    "[报价站] _is_fetching 已持续 30s+，判定为 hang，"
                    f"已强制释放锁并{'清理' if task_abandoned else '跳过'}旧任务占位"
                )
                self._is_fetching = False
                self._fetch_start_time = 0.0
                self._record_failure("后台拉取超时")
            else:
                return

        # 熔断冷却中：跳过本轮，倒计时 -1
        if self._circuit_breaker_cooldown > 0:
            self._circuit_breaker_cooldown -= 1
            if self._circuit_breaker_cooldown == 0:
                log.info("[报价站] 熔断冷却结束，恢复轮询")
            return

        if not self.data_provider or not self.data_provider.is_online():
            self._run_maintenance()
            return

        from core.market_calendar import MarketCalendar
        is_active = self._is_market_active
        quote_refreshable = MarketCalendar.is_quote_refresh_time()
            
        # 非交易时间降频到 30秒跑一次 (10秒一跳)
        if not is_active:
            if not getattr(self, '_first_off_market_fetch_done', False):
                self._first_off_market_fetch_done = True
                self._off_market_counter = 0
            else:
                if not hasattr(self, '_off_market_counter'):
                    self._off_market_counter = 0
                self._off_market_counter += 1
                if self._off_market_counter < 3: # 10 * 3 = 30s
                    return
                self._off_market_counter = 0

        codes = self._get_all_active_codes()
        if not codes:
            self._run_maintenance(active_codes_count=0)
            return
        self._run_maintenance(active_codes_count=len(codes))

        import time as _t
        self._is_fetching = True
        self._fetch_start_time = _t.time()
        self._fetch_generation += 1
        fetch_token = self._fetch_generation
        
        def _bg_task():
            try:
                # 交易时段与午休：都允许刷新报价快照。
                if quote_refreshable:
                    quotes = self.data_provider.fetch_realtime_quotes_batch(list(codes))
                else:
                    # 盘后/周末：不再联网，直接走本地兜底。
                    quotes = {}

                # 真正的非报价时段（周末、晚上）才强制从本地历史日线顶替。
                if not quote_refreshable:
                    cache = self.data_provider.get_all_valid_data()
                    # 防止缓存还在龟速加载时，导致被当成获取成功然后死等30秒
                    if not cache:
                        self._first_off_market_fetch_done = False
                        
                    for code in codes:
                        q = quotes.get(code, {})
                        price = float(q.get('close', 0) or 0)
                        if price == 0 and cache and code in cache:
                            _df = cache[code]
                            # 兼容 Pandas 和 Polars DataFrame
                            is_pandas = hasattr(_df, 'iloc')
                            is_empty = _df.empty if is_pandas else _df.is_empty()
                            
                            if not is_empty:
                                if is_pandas:
                                    c1 = float(_df.iloc[-1]['close'])
                                    c2 = float(_df.iloc[-2]['close']) if len(_df) >= 2 else c1
                                    o1 = float(_df.iloc[-1]['open'])
                                    h1 = float(_df.iloc[-1]['high'])
                                    l1 = float(_df.iloc[-1]['low'])
                                else:
                                    # Polars
                                    c1 = float(_df['close'][-1])
                                    c2 = float(_df['close'][-2]) if len(_df) >= 2 else c1
                                    o1 = float(_df['open'][-1])
                                    h1 = float(_df['high'][-1])
                                    l1 = float(_df['low'][-1])

                                q['close'] = c1
                                q['last_close'] = c2
                                q['open'] = o1
                                q['high'] = h1
                                q['low'] = l1
                        quotes[code] = q
                return quotes
            except Exception as e:
                log.error(f"[报价站] 批量拉取失败: {e}")
                return None

        def _on_result(quotes):
            if fetch_token != self._fetch_generation:
                log.debug("[报价站] 忽略过期报价任务回调(result)")
                return
            self._is_fetching = False
            self._fetch_start_time = 0.0
            if self._closed:
                return
            # 成功一次立即重置熔断计数器
            self._consecutive_failures = 0
            if quotes:
                has_valid = any(float(q.get('close', 0) or 0) > 0 for q in quotes.values())
                if has_valid:
                    event_bus.sig_rt_quotes.emit(quotes)

        def _on_error(err_msg):
            if fetch_token != self._fetch_generation:
                log.debug("[报价站] 忽略过期报价任务回调(error)")
                return
            # 关键兜底：无论后台发生什么异常，都必须释放锁，否则行情永久冻结
            self._is_fetching = False
            self._fetch_start_time = 0.0
            if self._closed:
                return
            next_failure = self._consecutive_failures + 1
            log.error(f"[报价站] 后台拉取异常(连续第{next_failure}次)，已释放锁: {err_msg}")
            self._record_failure("后台拉取异常")

        task_manager.run_in_background(
            _bg_task, on_success=_on_result, on_error=_on_error, task_id="central_quotes"
        )

    def shutdown(self):
        self._closed = True
        self._timer.stop()
        task_manager.abandon_task("central_quotes")
