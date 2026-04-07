from PyQt6.QtCore import QTimer, QObject, pyqtSignal, QThread
from datetime import datetime, timedelta
from .engine import EarningsEngine
import pandas as pd
from core.logger import get_logger

logger = get_logger()

class FetchWorker(QThread):
    sig_finished = pyqtSignal(object) 

    def __init__(self, engine, mode, missing_dates=None, target_date=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.mode = mode
        self.missing_dates = missing_dates
        self.target_date = target_date

    def run(self):
        try:
            if self.mode == "gap_fill" and self.missing_dates:
                logger.info(f"📡 发动机异步挂载：开始后台追扫 {len(self.missing_dates)} 天断档区间 -> {self.missing_dates}")
                all_missed_dfs = []
                for missed_day in self.missing_dates:
                    df_missed = self.engine.fetch_daily_surprises(target_publish_date=missed_day)
                    if not df_missed.empty:
                        all_missed_dfs.append(df_missed)
                if all_missed_dfs:
                    combined_df = pd.concat(all_missed_dfs, ignore_index=True)
                    logger.info(f"🎉 异步脱水完成！成功救回 {len(combined_df)} 条错失牛股。")
                    self.sig_finished.emit(combined_df)
                else:
                    self.sig_finished.emit(pd.DataFrame())
            
            elif self.mode == "single" and self.target_date:
                df_new = self.engine.fetch_daily_surprises(target_publish_date=self.target_date)
                self.sig_finished.emit(df_new)
                
            elif self.mode == "routine":
                df_new = self.engine.fetch_daily_surprises()
                self.sig_finished.emit(df_new)
        except Exception as e:
            logger.error(f"[线程抛锚] 异步抓取遭遇重型异常退出: {e}")
            self.sig_finished.emit(pd.DataFrame())

class EarningsScheduler(QObject):
    sig_new_surprises_found = pyqtSignal(object) 

    def __init__(self, parent=None):
        super().__init__(parent)
        self.engine = EarningsEngine()
        
        self.target_times = [(8, 30), (12, 0), (17, 0), (19, 0), (21, 0), (23, 0)]
        self.triggered_today = set()
        self.last_check_day = datetime.now().day
        self.active_workers = set()

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self._check_schedule)
        
    def _run_in_background(self, mode, missing_dates=None, target_date=None):
        worker = FetchWorker(self.engine, mode, missing_dates, target_date)
        self.active_workers.add(worker)
        
        worker.sig_finished.connect(self._on_worker_finished)
        # 用 deleteLater 确保 worker 断开信号后被 Qt 事件循环安全回收
        worker.finished.connect(lambda w=worker: (self.active_workers.discard(w), w.deleteLater()))
        worker.start()

    def _on_worker_finished(self, df):
        # 无论是否有新增数据，都必须通知 UI 结束加载状态
        self.sig_new_surprises_found.emit(df)

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
            self._run_in_background("gap_fill", missing_dates=missing_dates)

        # 第三步：挂载日常心跳时钟（30秒对次表），今天剩下的时间交给机器打理
        self.clock_timer.start(30000) 
        logger.info("✅ 业绩预告自动巡场机制已进入战备，严格盯防 6 个关键触发点。")

    def stop_patrol(self):
        self.clock_timer.stop()

    def force_manual_scan(self, date_list: list):
        logger.info(f"触发手动区间扫描（异步批量下发）: {date_list[0]} 到 {date_list[-1]}")
        self._run_in_background("gap_fill", missing_dates=date_list)

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
                    logger.info(f"📍 到达主线剧本节点 {time_key}，后台下发扫街任务...")
                    self._run_in_background("routine")
