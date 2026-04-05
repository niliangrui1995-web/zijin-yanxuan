import logging
from PyQt6.QtCore import QTimer, QObject, pyqtSignal
from datetime import datetime, timedelta
from .engine import EarningsEngine
import pandas as pd

logger = logging.getLogger("EarningsScheduler")

class EarningsScheduler(QObject):
    sig_new_surprises_found = pyqtSignal(object) 

    def __init__(self, parent=None):
        super().__init__(parent)
        self.engine = EarningsEngine()
        
        self.target_times = [(8, 30), (12, 0), (17, 0), (19, 0), (21, 0), (23, 0)]
        self.triggered_today = set()
        self.last_check_day = datetime.now().day

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._check_schedule)
        
    def start_patrol(self):
        """开机：先吐缓存 -> 计算断档脱水回填 -> 进入战备"""
        # 第一步：把硬盘里这 30 天内积攒的大金矿直接全量抛给前端 UI，瞬间填满界面
        cached_df = self.engine.get_cached_records()
        if not cached_df.empty:
            logger.info(f"📡 开机追溯：瞬间从底盘抽调 {len(cached_df)} 条过往累积的高增名录发布给 UI")
            self.sig_new_surprises_found.emit(cached_df)
            
        # 第二步：计算我们到底睡了几天，开启断档追更（无缝回填核心）
        last_sync = self.engine.last_sync_date
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        missing_dates = []
        try:
            start_dt = datetime.strptime(last_sync, "%Y-%m-%d")
            end_dt = datetime.strptime(today_str, "%Y-%m-%d")
            delta_days = (end_dt - start_dt).days
            
            # 如果断档超过 15 天，就只追近 15 天，防止开机卡死（可调整）
            if delta_days > 15:
                delta_days = 15
                start_dt = end_dt - timedelta(days=15)
                
            for i in range(1, delta_days + 1):
                missing_dates.append((start_dt + timedelta(days=i)).strftime("%Y-%m-%d"))
        except Exception as e:
            logger.warning(f"[巡逻] 日期解析失败，回退到仅查今天: {e}")
            missing_dates = [today_str] # 解析失败保底查今天
            
        # 补全中间漏掉的每一天，同时包含今天
        if today_str not in missing_dates:
             missing_dates.append(today_str)
             
        if missing_dates:
            logger.info(f"📡 自动发兵追扫漏网区间：需要补齐 {len(missing_dates)} 天的断档数据 -> {missing_dates}")
            
            all_missed_dfs = []
            for missed_day in missing_dates:
                # 引擎内嵌极其严格的防重盾，所以就算某天的数据在缓存里，再扫一次也绝对不会重复
                df_missed = self.engine.fetch_daily_surprises(target_publish_date=missed_day)
                if not df_missed.empty:
                    all_missed_dfs.append(df_missed)
                    
            if all_missed_dfs:
                combined_df = pd.concat(all_missed_dfs, ignore_index=True)
                self.sig_new_surprises_found.emit(combined_df)
                logger.info(f"🎉 断档回填结束！共成功救回 {len(combined_df)} 条错失的牛股资讯。")

        # 第三步：挂载日常心跳时钟（30秒对次表），今天剩下的时间交给机器打理
        self.clock_timer.start(30000) 
        logger.info("✅ 业绩预告自动巡场机制已进入战备，严格盯防 6 个关键触发点。")

    def stop_patrol(self):
        self.clock_timer.stop()

    def force_manual_scan(self, target_date: str):
        logger.info(f"触发手动时空扫描: {target_date}")
        df_new = self.engine.fetch_daily_surprises(target_publish_date=target_date)
        if not df_new.empty:
            self.sig_new_surprises_found.emit(df_new)

    def _check_schedule(self):
        now = datetime.now()
        
        if now.day != self.last_check_day:
            self.triggered_today.clear()
            self.last_check_day = now.day

        for t_hour, t_minute in self.target_times:
            if now.hour == t_hour and now.minute == t_minute:
                time_key = f"{t_hour}:{t_minute}"
                if time_key not in self.triggered_today:
                    self.triggered_today.add(time_key)
                    logger.info(f"📍 到达主线剧本节点 {time_key}，立刻唤醒发动机扫街...")
                    
                    df_new = self.engine.fetch_daily_surprises()
                    if not df_new.empty:
                        self.sig_new_surprises_found.emit(df_new)
