import datetime
import time
from PyQt6.QtCore import QObject, QTimer, pyqtSlot
from core.event_bus import event_bus
from core.event_types import DataEvent
from core.task_manager import task_manager
from core.logger import get_logger
from vcp.constants import MARKET_OPEN_AM, MARKET_CLOSE_PM

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
        # 写死3秒高刷新，但内部通过交易时间进行短路判断
        self._timer.start(3000)
        self._is_fetching = False

    def _is_market_open(self):
        now = datetime.datetime.now()
        if now.weekday() >= 5:
            return False
            
        # 加上盘前竞价时间和延长收盘，大致视为 9:15 到 15:05
        h, m = now.hour, now.minute
        hour_min = h * 100 + m
        if 915 <= hour_min <= 1505:
            return True
        return False

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

        # 美股日报只含 A 股标的，纳入统一广播
        if hasattr(mw, 'tab_na_daily') and getattr(mw.tab_na_daily, 'model', None):
            _extract(mw.tab_na_daily.model.row_data)

        # 业绩异动同理
        if hasattr(mw, 'tab_earnings') and getattr(mw.tab_earnings, 'model', None):
            _extract(mw.tab_earnings.model.row_data)
            
        return codes

    @pyqtSlot()
    def _trigger_fetch(self):
        if self._is_fetching:
            return
            
        if not self.data_provider or not self.data_provider.is_online():
            return
            
        # 优化：盘后不以3秒高频拉取，改为60秒拉取一次（保持收盘数据最终一致性即可）
        if not self._is_market_open():
            if not hasattr(self, '_off_market_counter'):
                self._off_market_counter = 0
            self._off_market_counter += 1
            if self._off_market_counter < 20: # 3 * 20 = 60s
                return
            self._off_market_counter = 0

        codes = self._get_all_active_codes()
        if not codes:
            return

        self._is_fetching = True
        
        def _bg_task():
            try:
                return self.data_provider.fetch_realtime_quotes_batch(list(codes))
            except Exception as e:
                log.error(f"[中央广播站] 拉取行情失败: {e}")
                return None

        def _on_result(quotes):
            self._is_fetching = False
            if quotes:
                # 只有成功拿到数据才全局空投！
                event_bus.sig_data_updated.emit(DataEvent.RT_QUOTES_BROADCAST.value, quotes)

        task_manager.run_in_background(_bg_task, on_success=_on_result, task_id="central_quotes")
