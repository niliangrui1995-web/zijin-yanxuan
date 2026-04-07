from PyQt6.QtCore import QObject, QTimer, pyqtSlot
from core.event_bus import event_bus
from core.task_manager import task_manager
from core.logger import get_logger

log = get_logger(__name__)

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
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._trigger_fetch)
        # 盘中刷新频率：10秒（兼顾基本盯盘需求与极低系统消耗）
        self._timer.start(10000)
        self._is_fetching = False

        # 熔断器：连续失败 N 次后暂停轮询，冷却一段时间再恢复
        # 防止网络断开时每 10 秒打一次必然失败的请求，白费 CPU 和日志
        self._consecutive_failures = 0
        self._circuit_breaker_cooldown = 0
        self._FAILURE_THRESHOLD = 3   # 连续失败 3 次后触发熔断
        self._COOLDOWN_TICKS = 3      # 熔断后跳过 3 个周期（约 30 秒）

    @property
    def _is_market_active(self):
        from core.market_calendar import MarketCalendar
        return MarketCalendar.is_market_active()

    def _get_all_active_codes(self) -> set:
        codes = set()
        mw = self.main_window
        
        def _extract(model_data):
            for r in model_data:
                c = r.get("代码")
                if c: codes.add(str(c))

        if hasattr(mw, 'tab_scan') and mw.tab_scan.source_model:
            _extract(mw.tab_scan.source_model.row_data)
            
        if hasattr(mw, 'tab_rt') and mw.tab_rt.source_model:
            _extract(mw.tab_rt.source_model.row_data)
            
        if hasattr(mw, 'tab_watchlist') and getattr(mw.tab_watchlist, 'model', None):
            _extract(mw.tab_watchlist.model.row_data)
            
        if hasattr(mw, 'tab_ai_tracker') and hasattr(mw.tab_ai_tracker, '_ai_tracker_codes'):
            for c in mw.tab_ai_tracker._ai_tracker_codes: codes.add(str(c))
            
        if hasattr(mw, 'tab_foreign_block') and hasattr(mw.tab_foreign_block, '_block_trade_codes'):
            for c in mw.tab_foreign_block._block_trade_codes: codes.add(str(c))

        if hasattr(mw, 'tab_na_daily') and getattr(mw.tab_na_daily, 'model', None):
            _extract(mw.tab_na_daily.model.row_data)

        if hasattr(mw, 'tab_earnings') and getattr(mw.tab_earnings, 'model', None):
            _extract(mw.tab_earnings.model.row_data)
            
        return codes

    @pyqtSlot()
    def _trigger_fetch(self):
        if self._is_fetching:
            return

        # 熔断冷却中：跳过本轮，倒计时 -1
        if self._circuit_breaker_cooldown > 0:
            self._circuit_breaker_cooldown -= 1
            if self._circuit_breaker_cooldown == 0:
                log.info("[报价站] 熔断冷却结束，恢复轮询")
            return

        if not self.data_provider or not self.data_provider.is_online():
            return
            
        is_active = self._is_market_active
            
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
            return

        self._is_fetching = True
        
        def _bg_task():
            try:
                # 交易时段：去网络拉数据！
                if is_active:
                    quotes = self.data_provider.fetch_realtime_quotes_batch(list(codes))
                else:
                    # 非交易时段：彻底断网！组装空架子
                    quotes = {}

                # 如果在非活跃期（周末、晚上），强制从本地历史日线顶替为实时数据
                if not is_active:
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
            self._is_fetching = False
            # 成功一次立即重置熔断计数器
            self._consecutive_failures = 0
            if quotes:
                has_valid = any(float(q.get('close', 0) or 0) > 0 for q in quotes.values())
                if has_valid:
                    event_bus.sig_rt_quotes.emit(quotes)

        def _on_error(err_msg):
            # 关键兜底：无论后台发生什么异常，都必须释放锁，否则行情永久冻结
            self._is_fetching = False
            self._consecutive_failures += 1
            log.error(f"[报价站] 后台拉取异常(连续第{self._consecutive_failures}次)，已释放锁: {err_msg}")

            # 熔断触发：连续失败达到阈值，进入冷却
            if self._consecutive_failures >= self._FAILURE_THRESHOLD:
                self._circuit_breaker_cooldown = self._COOLDOWN_TICKS
                log.warning(
                    f"[报价站] ⚡ 熔断触发：连续失败 {self._consecutive_failures} 次，"
                    f"暂停轮询 {self._COOLDOWN_TICKS * 10} 秒后自动恢复"
                )

        task_manager.run_in_background(
            _bg_task, on_success=_on_result, on_error=_on_error, task_id="central_quotes"
        )
