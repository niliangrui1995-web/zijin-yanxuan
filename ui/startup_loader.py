# -*- coding: utf-8 -*-
"""
ui/startup_loader.py
负责接管应用冷启动与后台缓存加载流程，为“白屏克星”做框架铺垫
"""
import os
import datetime
import subprocess
import sys

from PyQt6.QtCore import QTimer

from core.logger import get_logger
from core.event_bus import event_bus
from core.task_manager import task_manager

log = get_logger(__name__)

class StartupLoader:
    """接管主窗口的加载流程"""
    def __init__(self, main_window):
        # 强引用 MainWindow 只是为了方便调用 UI UI 线程桥接 `_call_in_ui` 和引用核心部件
        # 未来可逐渐解耦
        self.mw = main_window
        self._closed = False
        self._deferred_timer = QTimer(main_window)
        self._deferred_timer.setSingleShot(True)
        self._deferred_timer.timeout.connect(self.deferred_data_load)
        self._smart_timer = QTimer(main_window)
        self._smart_timer.setSingleShot(True)
        self._smart_timer.timeout.connect(self.smart_startup)

    def schedule_startup(self):
        if self._closed:
            return
        self._deferred_timer.start(2500)
        self._smart_timer.start(4500)

    def shutdown(self):
        self._closed = True
        self._deferred_timer.stop()
        self._smart_timer.stop()

    def _alive(self):
        return (
            not self._closed and
            self.mw is not None and
            not getattr(self.mw, "_is_closing", False)
        )

    def _safe_call_in_ui(self, callback):
        if not self._alive():
            return
        try:
            self.mw._call_in_ui(lambda: callback() if self._alive() else None)
        except RuntimeError:
            pass

    def deferred_data_load(self):
        """延迟加载缓存数据（pkl + RT缓存 + RPS缓存），避免阻塞UI线程"""
        def _load_bg():
            if not self._alive():
                return
            try:
                cache_date = self.mw.data_provider.load_cache_from_disk()
                if cache_date and self._alive():
                    self.mw._cache_date = cache_date
                    count = len(self.mw.data_provider.cache_data)
                    self._safe_call_in_ui(
                        lambda: getattr(self.mw, 'lbl_code_count') and self.mw.lbl_code_count.setText(f"标的池: {count}") if hasattr(self.mw, 'lbl_code_count') else None
                    )
                    self._safe_call_in_ui(
                        lambda: self.mw.lbl_status.setText(f"已加载 {count} 只标的缓存 (日期: {cache_date})")
                    )
            except Exception as e:
                log.error(f"[启动] 延迟加载缓存异常: {e}")

            # 在UI线程恢复RT缓存
            self._safe_call_in_ui(
                lambda: self.mw.cache_manager.load_rt_cache(self.mw.table_rt, lambda msg: self.mw.lbl_status.setText(msg))
            )

            # 加载 RPS 预计算缓存
            try:
                self.mw.cache_manager.try_load_rps_from_disk(
                    self.mw.engine, 
                    set_status_callback=lambda msg: self._safe_call_in_ui(lambda: self.mw.lbl_status.setText(msg))
                )
            except Exception as e:
                log.error(f"[启动] RPS 缓存加载异常: {e}")

            # 通知各 Tab: 缓存数据已就绪，可以回填历史数据
            self._safe_call_in_ui(
                lambda: event_bus.sig_cache_loaded.emit()
            )

        task_manager.run_in_background(_load_bg, task_id="deferred_load")

        # 启动时静默检查并更新亚洲寡头历史 json 缓存
        def _check_asian_data_bg():
            if not self._alive():
                return
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            output_dir = os.path.join(project_root, "data", "Cache")
            json_cache = os.path.join(output_dir, "asian_klines_latest.json")
            script_path = os.path.join(project_root, "vcp", "fetchers", "asian_kline_fetcher.py")
            
            needs_update = False
            if not os.path.exists(json_cache):
                needs_update = True
            else:
                mtime = os.path.getmtime(json_cache)
                mdate = datetime.date.fromtimestamp(mtime)
                if mdate < datetime.date.today():
                    needs_update = True
                     
            if needs_update and os.path.exists(script_path):
                log.info("[启动] 亚洲市场 JSON 已非最新，后台静默拉取中(YF)...")
                try:
                    creationflags = 0x08000000 if os.name == 'nt' else 0
                    subprocess.run(
                        [sys.executable, script_path, "--output-dir", output_dir],
                        check=True,
                        creationflags=creationflags
                    )
                    log.info("[启动] 亚洲市场静默增量拉取完成，触发界面重载...")
                    self._safe_call_in_ui(lambda: event_bus.sig_asian_klines_ready.emit())
                except Exception as e:
                    log.error(f"[启动] 亚洲市场后台静默更新失败: {e}")

        task_manager.run_in_background(_check_asian_data_bg, task_id="asian_data_sync_bg")

    def smart_startup(self):
        """智能启动：异步检测网络，联网可用则自动切换联网模式并触发各Tab刷新"""
        def _check_and_go_online():
            try:
                if not self._alive():
                    return
                if self.mw.data_provider.test_network(timeout=3):
                    if not self._alive():
                        return
                    self.mw.data_provider.set_online_mode(True)
                    log.info("[智能启动] ✅ 网络可用，已自动切换到联网模式")
                    
                    try:
                        if not self._alive():
                            return
                        self.mw.data_provider.get_all_codes() 
                        self.mw.data_provider.code2name = self.mw.data_provider._get_codes_from_vipdoc()
                        
                        def _refresh_watchlist_names():
                            if hasattr(self.mw, 'tab_watchlist') and self.mw.tab_watchlist:
                                changed = False
                                for r in self.mw.tab_watchlist.model.row_data:
                                    code = str(r.get("代码", ""))
                                    name = str(r.get("名称", ""))
                                    if code and (not name or name == code):
                                        r["名称"] = self.mw.data_provider.code2name.get(code, code)
                                        changed = True
                                if changed:
                                    self.mw.tab_watchlist.model.layoutChanged.emit()
                        self._safe_call_in_ui(_refresh_watchlist_names)
                    except Exception as e:
                        log.error(f"[智能启动] 后台同步大盘代码名称映射表时失败: {e}")

                    self._safe_call_in_ui(lambda: self.mw._update_network_ui(True))
                    if hasattr(self.mw, '_on_smart_startup_online_done'):
                        self._safe_call_in_ui(self.mw._on_smart_startup_online_done)
                    self._safe_call_in_ui(self.auto_start_rt_if_ready)
                else:
                    log.info("[智能启动] 网络不可用，保持离线模式")
            except Exception as e:
                log.error(f"[智能启动] 网络检测异常: {e}")

        task_manager.run_in_background(_check_and_go_online, task_id="smart_startup")

    def auto_start_rt_if_ready(self):
        """智能启动后自动开启盘中监控（仅在交易日+交易时间且数据就绪时）"""
        try:
            if not self._alive():
                return
            from core.market_calendar import MarketCalendar
            if not MarketCalendar.is_market_active():
                log.info("[智能启动] 非交易日/非交易时间，跳过盘中自动监控")
                return
            if not self.mw.data_provider.cache_data or len(self.mw.data_provider.cache_data) < 100:
                log.info("[智能启动] 数据不足，跳过盘中自动监控")
                return
            if hasattr(self.mw, 'tab_rt') and hasattr(self.mw.tab_rt, '_toggle_rt_monitor'):
                self.mw.tab_rt._toggle_rt_monitor(auto=True)
                log.info("[智能启动] ✅ 盘中监控已自动启动")
        except Exception as e:
            log.error(f"[智能启动] 自动监控启动异常: {e}")
