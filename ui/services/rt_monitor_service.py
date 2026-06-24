# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

from app.services.ui_config_service import app_config
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signals
from app.services.ui_market_calendar_service import MarketCalendar
from app.services.ui_task_service import background_job_runner as task_manager
from app.services.ui_task_service import task_registry
from core.logger import get_logger
from ui.components.thread_shutdown import request_thread_shutdown
from ui.workers.rt_scan_worker import RtScanWorker

log = get_logger(__name__)

_OPENING_AUTO_START_DEFER_STATUSES = frozenset({"开盘集合竞价", "开市前时段"})


class RtMonitorService(QObject):
    sig_results_ready = pyqtSignal(object)
    sig_status_changed = pyqtSignal(object)
    sig_scan_count_changed = pyqtSignal(int, int)
    sig_progress = pyqtSignal(str)
    sig_running_changed = pyqtSignal(bool)

    def __init__(self, data_provider, engine, parent=None):
        super().__init__(parent)
        self.data_provider = data_provider
        self.engine = engine
        self._settings = app_config.section("rt", legacy_scope="RtMonitorTab")
        self._manual_stop_requested = False
        self._manual_stop_trade_date = ""
        self._rt_stop_requested = False
        self._rt_worker = None
        self._latest_results = []
        self._last_auto_start_skip_reason = ""

    @property
    def latest_results(self) -> list:
        return list(self._latest_results or [])

    def is_running(self) -> bool:
        worker = self._rt_worker
        try:
            return bool(worker is not None and worker.isRunning())
        except RuntimeError:
            return False

    def _emit_status(
        self,
        state: str,
        detail: str = "",
        next_step: str = "",
        *,
        running: bool | None = None,
        touch: bool = True,
    ) -> None:
        payload = {
            "state": str(state or "").strip() or "working",
            "detail": str(detail or "").strip(),
            "next_step": str(next_step or "").strip(),
            "running": self.is_running() if running is None else bool(running),
            "touch": bool(touch),
        }
        self.sig_status_changed.emit(payload)

    def _get_interval_seconds(self) -> int:
        interval_text = str(self._settings.value("interval", "30秒"))
        interval_map = {"30秒": 30, "1分钟": 60, "3分钟": 180, "5分钟": 300}
        return interval_map.get(interval_text, 30)

    def _precomputed_rps_ready(self) -> bool:
        get_precomputed_rps = getattr(self.engine, "get_precomputed_rps", None)
        if not callable(get_precomputed_rps):
            return True
        try:
            bundle = get_precomputed_rps()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return False
        if not isinstance(bundle, dict):
            return False
        return bundle.get("rps120") is not None and bundle.get("rps250") is not None

    def _auto_start_readiness(self) -> tuple[bool, str, str, str]:
        try:
            market_status = str(MarketCalendar.get_market_status("CN") or "").strip()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            market_status = ""
        if market_status in _OPENING_AUTO_START_DEFER_STATUSES:
            return False, "opening_auction", f"等待连续竞价({market_status})", "09:30 后会自动启动"
        cache_data = getattr(self.data_provider, "cache_data", None) or {}
        try:
            cache_count = len(cache_data)
        except TypeError:
            cache_count = 0
        if cache_count < 100:
            return False, "cache_not_ready", "等待历史缓存预热", "缓存预热完成后会自动启动"
        if not self._precomputed_rps_ready():
            return False, "rps_not_ready", "等待预计算 RPS", "RPS 预热完成后会自动启动"
        return True, "", "", ""

    def _skip_auto_start(self, reason: str, detail: str, next_step: str) -> bool:
        reason = str(reason or "not_ready").strip()
        if self._last_auto_start_skip_reason != reason:
            log.info(f"[盘中监控] 自动启动等待: {detail}")
            self._last_auto_start_skip_reason = reason
        self._emit_status("idle", detail, next_step, running=False, touch=False)
        return False

    @staticmethod
    def _manual_stop_reference_date() -> str:
        try:
            trade_date = MarketCalendar.get_latest_trade_date("CN")
            if trade_date is not None:
                return trade_date.isoformat()
            return MarketCalendar.today("CN").isoformat()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return datetime.now().date().isoformat()

    def _clear_expired_manual_stop(self) -> bool:
        if not self._manual_stop_requested:
            self._manual_stop_trade_date = ""
            return False
        current_trade_date = self._manual_stop_reference_date()
        if self._manual_stop_trade_date and self._manual_stop_trade_date == current_trade_date:
            return False
        self._manual_stop_requested = False
        self._manual_stop_trade_date = ""
        log.info("[盘中监控] 手动停止标记已跨交易日失效，恢复自动启动")
        return True

    def start(self, *, auto: bool = False) -> bool:
        if self.is_running():
            return False
        if auto and self._manual_stop_requested and not self._clear_expired_manual_stop():
            self._emit_status("idle", "今日已手动停止", "下一交易日会自动启动", running=False, touch=False)
            return False
        if auto:
            ready, reason, detail, next_step = self._auto_start_readiness()
            if not ready:
                return self._skip_auto_start(reason, detail, next_step)
            self._last_auto_start_skip_reason = ""
        if not auto:
            self._manual_stop_requested = False
            self._manual_stop_trade_date = ""
            self._last_auto_start_skip_reason = ""
        self._rt_stop_requested = False

        if not getattr(self.data_provider, "cache_data", None):
            self._emit_status("cache", "加载本地缓存", "就绪后连接行情")
            try:
                cache_date = self.data_provider.load_cache_from_disk()
                if cache_date and self.data_provider.cache_data:
                    log.info(f"[盘中监控] 缓存为空，已从磁盘自动加载(日期: {cache_date})")
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.error(f"[盘中监控] 磁盘缓存自动加载失败: {exc}")

        if not getattr(self.data_provider, "cache_data", None):
            self._emit_status("fallback", "无可用缓存", "先执行扫描或F5", running=False)
            event_bus.sig_system_log.emit("warning", "[盘中监控] 无可用缓存，跳过自动启动")
            return False

        is_online = getattr(self.data_provider, "is_online", None)
        if callable(is_online) and not is_online():
            self._emit_status("working", "连接行情服务器", "成功后自动启动", running=False)
            task_id = task_registry.workspace("rt_connect").task_id
            if task_manager.is_active_task(task_id):
                return False

            def _try_connect():
                ok = self.data_provider.test_network(timeout=5)
                if ok:
                    self.data_provider.set_online_mode(True)
                return ok

            task_manager.run_in_background(
                _try_connect,
                on_success=self._on_network_probe_result,
                on_error=self._on_network_probe_error,
                task_id=task_id,
            )
            return True

        self._start_worker()
        return True

    def stop(self, *, auto: bool = False) -> bool:
        if not self.is_running():
            self._manual_stop_requested = not auto
            self._manual_stop_trade_date = self._manual_stop_reference_date() if self._manual_stop_requested else ""
            return False
        self._manual_stop_requested = not auto
        self._manual_stop_trade_date = self._manual_stop_reference_date() if self._manual_stop_requested else ""
        self._rt_stop_requested = True
        try:
            self._rt_worker.stop()
        except (AttributeError, RuntimeError):
            return False
        if auto:
            self._emit_status("working", "自动停止中", "下个交易时段会自动启动", running=True)
        else:
            self._emit_status("working", "正在停止", "稍后可重新启动", running=True)
        return True

    def toggle(self, *, auto: bool = False) -> bool:
        if self.is_running():
            return self.stop(auto=auto)
        return self.start(auto=auto)

    def sync_to_market_state(self) -> str:
        self._clear_expired_manual_stop()
        is_active = MarketCalendar.is_market_active("CN")
        is_running = self.is_running()
        if is_active and not is_running and not self._manual_stop_requested and not self._rt_stop_requested:
            ready, reason, detail, next_step = self._auto_start_readiness()
            if not ready:
                self._skip_auto_start(reason, detail, next_step)
                return "skipped"
            log.info("[盘中监控] 交易时段到达，触发自动启动")
            return "started" if self.start(auto=True) else "skipped"
        if not is_active and is_running and not self._rt_stop_requested:
            log.info("[盘中监控] 非交易时段，触发自动停止")
            self._manual_stop_requested = False
            return "stopped" if self.stop(auto=True) else "skipped"
        return "skipped"

    def _start_worker(self) -> None:
        interval_sec = self._get_interval_seconds()
        rps_threshold = int(self._settings.value("rps", 80))
        worker = RtScanWorker(self.data_provider, self.engine, interval=interval_sec, rps_threshold=rps_threshold)
        self._rt_worker = worker
        worker.rt_result_ready.connect(self._on_results_ready)
        worker.progress.connect(self._on_worker_progress)
        worker.scan_count.connect(self._on_scan_count_updated)
        worker.scan_count.connect(
            lambda n, pool: event_bus.sig_system_log.emit("info", f"[监控] 第{n}轮 | 待突破池 {pool} 只")
        )
        worker.finished.connect(self._on_worker_finished)
        self._rt_stop_requested = False
        worker.start()
        self._emit_status("realtime", "已启动", "正在执行首轮任务", running=True)
        self.sig_running_changed.emit(True)
        ui_signals.sig_task_progress.emit("rt_monitor", 1, "start")

    def _on_network_probe_result(self, ok: bool) -> None:
        if ok:
            event_bus.sig_network_status_changed.emit(True, "Online")
            event_bus.sig_system_log.emit("info", "[盘中监控] 启动前自动联网成功")
            self._emit_status("realtime", "联网成功", "正在启动", running=False)
            self._start_worker()
            return
        self._on_network_probe_error("无法连接东方财富实时行情")

    def _on_network_probe_error(self, error_message: str) -> None:
        event_bus.sig_system_log.emit("error", f"[盘中监控] 联网异常: {error_message}")
        self._emit_status("error", "联网失败", "检查网络后重试", running=False)
        self.sig_running_changed.emit(False)

    def _on_worker_progress(self, raw_msg: str) -> None:
        self.sig_progress.emit(str(raw_msg or ""))

    def _on_scan_count_updated(self, round_no: int, pool_size: int) -> None:
        self.sig_scan_count_changed.emit(int(round_no or 0), int(pool_size or 0))

    def _on_results_ready(self, results) -> None:
        self._latest_results = list(results or [])
        self.sig_results_ready.emit(self._latest_results)
        event_bus.sig_rt_quotes_refreshed.emit(self._latest_results)

    def _on_worker_finished(self) -> None:
        self._rt_stop_requested = False
        worker = self._rt_worker
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
            self._rt_worker = None

        try:
            active = MarketCalendar.is_market_active("CN")
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            active = False

        if self._manual_stop_requested:
            next_step = "点击“启动盘中监控”可恢复"
        elif not active:
            next_step = "下个交易时段会自动启动"
        else:
            next_step = "可手动重新启动"

        self._emit_status("idle", "已停止", next_step, running=False)
        self.sig_running_changed.emit(False)
        ui_signals.sig_task_progress.emit("rt_monitor", 0, "stop")

    def shutdown(self) -> None:
        worker = self._rt_worker
        if worker is None:
            return
        self._manual_stop_requested = False
        self._rt_stop_requested = True
        request_thread_shutdown(
            worker,
            label="RT monitor worker",
            stop=getattr(worker, "stop", None),
            timeout_ms=2000,
            logger=log,
        )
        self._rt_worker = None
