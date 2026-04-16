from datetime import datetime, timedelta

import pandas as pd
from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from core.logger import get_logger
from core.market_calendar import MarketCalendar

from .engine import EarningsEngine

logger = get_logger()
# 启动期断档追扫非常重，默认关闭以避免 UI 假死；手动扫描和定时巡检仍可用。
ENABLE_STARTUP_GAP_FILL = False
_SCHEDULER_ERRORS = (
    AttributeError,
    KeyError,
    OSError,
    RuntimeError,
    TimeoutError,
    TypeError,
    ValueError,
    ArithmeticError,
)

class FetchWorker(QThread):
    sig_finished = pyqtSignal(object, str)

    def __init__(self, engine, mode, missing_dates=None, target_date=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.mode = mode
        self.missing_dates = missing_dates
        self.target_date = target_date

    def run(self):
        try:
            if self.mode == "warm_cache":
                cached_df = self.engine.get_cached_records()
                if cached_df is not None and not cached_df.empty:
                    logger.info(f"[业绩调度] 📡 恢复缓存 {len(cached_df)} 条记录")
                self.sig_finished.emit(cached_df if cached_df is not None else pd.DataFrame(), self.mode)

            elif self.mode == "gap_fill" and self.missing_dates:
                logger.info(f"[业绩调度] 开始补扫 {len(self.missing_dates)} 天断档")
                all_missed_dfs = []
                for missed_day in self.missing_dates:
                    df_missed = self.engine.fetch_daily_surprises(target_publish_date=missed_day)
                    if not df_missed.empty:
                        all_missed_dfs.append(df_missed)
                if all_missed_dfs:
                    combined_df = pd.concat(all_missed_dfs, ignore_index=True)
                    logger.info(f"[业绩调度] ✅ 补扫完成，新增 {len(combined_df)} 条")
                    self.sig_finished.emit(combined_df, self.mode)
                else:
                    self.sig_finished.emit(pd.DataFrame(), self.mode)

            elif self.mode == "single" and self.target_date:
                df_new = self.engine.fetch_daily_surprises(target_publish_date=self.target_date)
                self.sig_finished.emit(df_new, self.mode)

            elif self.mode == "routine":
                df_new = self.engine.fetch_daily_surprises()
                self.sig_finished.emit(df_new, self.mode)
        except _SCHEDULER_ERRORS as e:
            logger.error(f"[业绩调度] ❌ 后台抓取异常退出: {e}")
            self.sig_finished.emit(pd.DataFrame(), self.mode)

class EarningsScheduler(QObject):
    sig_new_surprises_found = pyqtSignal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.engine = EarningsEngine()

        self.target_times = [(8, 30), (12, 0), (17, 0), (19, 0), (21, 0), (23, 0)]
        self.triggered_today = set()
        self.last_check_day = MarketCalendar.today("CN")
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

    def _on_worker_finished(self, df, mode):
        # 无论是否有新增数据，都必须通知 UI 结束加载状态
        self.sig_new_surprises_found.emit(df, mode)

    def start_patrol(self):
        """开机：先吐缓存 -> 计算断档脱水回填 -> 进入战备"""
        # 第一步：后台吐缓存，避免在 UI 线程里做重型历史数据处理。
        self._run_in_background("warm_cache")

        # 第二步：计算我们到底睡了几天，开启断档追更（无缝回填核心）
        last_sync = self.engine.last_sync_date
        today_str = MarketCalendar.today("CN").strftime("%Y-%m-%d")

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
        except (TypeError, ValueError, OverflowError) as e:
            logger.warning(f"[业绩调度] 日期解析失败，回退仅查今天: {e}")
            missing_dates = [today_str] # 解析失败保底查今天

        # 补全中间漏掉的每一天，同时包含今天
        if today_str not in missing_dates:
             missing_dates.append(today_str)

        if missing_dates and ENABLE_STARTUP_GAP_FILL:
            self._run_in_background("gap_fill", missing_dates=missing_dates)
        elif missing_dates:
            logger.info("[业绩调度] 跳过开机补扫（防卡），后续定时巡检")

        # 第三步：挂载日常心跳时钟（30秒对次表），今天剩下的时间交给机器打理
        self.clock_timer.start(30000)
        logger.info("[业绩调度] ✅ 定时巡检已启动 (6 个巡检时间点)")

    def stop_patrol(self):
        self.clock_timer.stop()

    def force_manual_scan(self, date_list: list):
        logger.info(f"[业绩调度] 手动扫描: {date_list[0]} ~ {date_list[-1]}")
        self._run_in_background("gap_fill", missing_dates=date_list)

    def _check_schedule(self):
        now = MarketCalendar.now("CN")

        if now.date() != self.last_check_day:
            self.triggered_today.clear()
            self.last_check_day = now.date()

        for t_hour, t_minute in self.target_times:
            if now.hour == t_hour and now.minute == t_minute:
                time_key = f"{t_hour}:{t_minute}"
                if time_key not in self.triggered_today:
                    self.triggered_today.add(time_key)
                    logger.info(f"[业绩调度] ⏰ 触发 {time_key} 定时巡检")
                    self._run_in_background("routine")
