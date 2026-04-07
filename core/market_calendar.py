import datetime
import os
from vcp.constants import CACHE_DIR
from core.task_manager import task_manager
from core.logger import get_logger

log = get_logger(__name__)

class MarketCalendar:
    _trade_dates = None
    
    @classmethod
    def load_trade_dates(cls):
        from core.data_store import DataStore
        now = datetime.datetime.now()
        cur_month = now.strftime("%Y-%m")
        
        try:
            data = DataStore().load_json("trade_dates")
            if data and data.get("month") == cur_month:
                return set(data.get("dates", []))
        except Exception as _e:
            log.debug(f"[交易日历] DataStore 读取失败: {_e}")

        # === 旧版本 JSON 自动迁移兜底 ===
        cache_file = os.path.join(CACHE_DIR, 'trade_dates.json')
        if os.path.exists(cache_file):
            try:
                import json
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data and data.get("month") == cur_month:
                    DataStore().save_json("trade_dates", data)
                    os.rename(cache_file, cache_file + '.migrated') # 打上废弃标记
                    return set(data.get("dates", []))
            except Exception as _e:
                log.debug(f"[交易日历] 旧 JSON 迁移失败: {_e}")
        # ==============================
                
        def _bg_fetch_calendar():
            try:
                import akshare as ak
                df = ak.tool_trade_date_hist_sina()
                dates = [str(d)[:10] for d in df['trade_date']]
                DataStore().save_json("trade_dates", {"month": cur_month, "dates": dates})
                # 热加载成功后，自动更新内存引用
                cls._trade_dates = set(dates)
            except Exception as e:
                log.error(f"[交易日历] 后台同步失败: {e}")
                
        task_manager.run_in_background(_bg_fetch_calendar)
        return None

    @classmethod
    def is_market_active(cls):
        """判断当前是否为活跃交易时间：交易日且 9:15-16:00 之间"""
        now = datetime.datetime.now()
        
        # 1. 判断时段 (放宽到 16:00 确保数据彻底落地)
        h, m = now.hour, now.minute
        hour_min = h * 100 + m
        if not (915 <= hour_min <= 1600):
            return False
            
        # 2. 判断交易日历
        if cls._trade_dates is None:
            cls._trade_dates = cls.load_trade_dates()
            
        if cls._trade_dates:
            today_date = now.date().strftime("%Y-%m-%d")
            return today_date in cls._trade_dates
        else:
            return now.weekday() < 5
