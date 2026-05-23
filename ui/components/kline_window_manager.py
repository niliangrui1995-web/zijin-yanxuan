# -*- coding: utf-8 -*-
"""
ui/components/kline_window_manager.py
K 线窗口管理器 — 单例模式 (#1)

为什么要单独管理？
原先 MainWindow 里有两处几乎相同的 K 线窗口创建与清理逻辑
(_on_table_double_click 和 _on_show_kline_with_list)。
窗口数量限制、RuntimeError 防御、静默关闭无反馈等问题散落在两处代码中。
现在统一收口到这里，任何人想开 K 线图只需调用 open_chart()。
"""

import os
import threading
import time
import weakref

from app.services.kline_webengine_preflight import check_qt_webengine_available
from core.logger import get_logger
from core.observability import emit_structured_log, record_metric

log = get_logger(__name__)

# 可配置的最大窗口数量
MAX_CHART_WINDOWS = 5
PREWARM_VIEW_TTL_MS = 120_000
WEBENGINE_PREFLIGHT_TIMEOUT_S = 8
HIDDEN_PREWARM_ENV = "VCP_KLINE_HIDDEN_PREWARM"


def _hidden_prewarm_enabled() -> bool:
    value = str(os.environ.get(HIDDEN_PREWARM_ENV, "") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


class KLineWindowManager:
    """K 线图窗口池管理器 — 全局单例"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._charts = []
            cls._instance._prewarm_view = None
            cls._instance._prewarm_started = False
            cls._instance._prewarm_cancelled = False
            cls._instance._prewarm_expire_timer = None
            cls._instance._prewarm_hidden_view_enabled = False
            cls._instance._webengine_available = None
            cls._instance._webengine_failure = ""
            cls._instance._webengine_preflight_started = False
            cls._instance._post_close_collect_scheduled = False
        return cls._instance

    def prewarm(
        self,
        *,
        delay_ms: int = 2500,
        ttl_ms: int = PREWARM_VIEW_TTL_MS,
        hidden_view: bool | None = None,
    ) -> bool:
        """Run WebEngine preflight during idle time; hidden-view warm-up is opt-in."""
        if self._prewarm_started or self._prewarm_view is not None:
            return False
        self._prewarm_started = True
        self._prewarm_cancelled = False
        self._prewarm_hidden_view_enabled = _hidden_prewarm_enabled() if hidden_view is None else bool(hidden_view)
        self._prewarm_ttl_ms = max(0, int(ttl_ms or 0))
        try:
            from PyQt6.QtCore import QTimer

            QTimer.singleShot(max(0, int(delay_ms)), self._run_prewarm)
            return True
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as exc:
            self._prewarm_started = False
            log.debug(f"[K线管理] WebEngine 预热调度失败: {exc}")
            return False

    def _ensure_webengine_available(self) -> bool:
        cached = getattr(self, "_webengine_available", None)
        if cached is not None:
            return bool(cached)

        result = check_qt_webengine_available(timeout_s=WEBENGINE_PREFLIGHT_TIMEOUT_S)
        ok = bool(result.get("ok"))
        self._webengine_available = ok
        self._webengine_failure = "" if ok else str(result.get("reason", "") or "unknown")
        elapsed_ms = result.get("elapsed_ms")
        if elapsed_ms is not None:
            record_metric(
                "kline_webengine_preflight_ms",
                elapsed_ms,
                unit="ms",
                tags={"ok": str(ok).lower()},
            )
        return ok

    def _start_webengine_preflight_async(self) -> bool:
        if getattr(self, "_webengine_available", None) is not None:
            return False
        if getattr(self, "_webengine_preflight_started", False):
            return False
        self._webengine_preflight_started = True

        def _run() -> None:
            try:
                self._ensure_webengine_available()
            finally:
                self._webengine_preflight_started = False

        thread = threading.Thread(target=_run, name="KLineWebEnginePreflight", daemon=True)
        thread.start()
        return True

    def _notify_webengine_unavailable(self, main_window, code: str, name: str) -> None:
        reason = str(getattr(self, "_webengine_failure", "") or "unknown")
        message = "K线图组件自检失败，已阻止打开以避免程序崩溃"
        try:
            from ui.components.toast_widget import show_toast

            show_toast(f"{message}：{reason}", "warning", main_window, duration=3500)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.debug(f"[K线管理] WebEngine 不可用提示失败: {exc}")
        try:
            status_bar = main_window.statusBar() if main_window is not None else None
            if status_bar is not None:
                status_bar.showMessage(f"{message}：{reason}", 5000)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        record_metric(
            "kline_webengine_unavailable",
            1,
            unit="count",
            tags={"code": str(code or "").strip()},
        )
        emit_structured_log(
            "kline.webengine_unavailable",
            code=str(code or "").strip(),
            name=str(name or "").strip(),
            reason=reason,
        )

    def _cancel_prewarm_expire_timer(self) -> None:
        timer = getattr(self, "_prewarm_expire_timer", None)
        self._prewarm_expire_timer = None
        if timer is None:
            return
        try:
            timer.stop()
            timer.deleteLater()
        except RuntimeError:
            pass
        except (AttributeError, TypeError):
            pass

    def _schedule_prewarm_expiry(self) -> None:
        ttl_ms = max(0, int(getattr(self, "_prewarm_ttl_ms", PREWARM_VIEW_TTL_MS) or 0))
        if ttl_ms <= 0:
            self._expire_prewarm()
            return
        try:
            from PyQt6.QtCore import QTimer

            self._cancel_prewarm_expire_timer()
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(self._expire_prewarm)
            self._prewarm_expire_timer = timer
            timer.start(ttl_ms)
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as exc:
            log.debug(f"[K线管理] WebEngine 预热过期计时器创建失败: {exc}")

    def _dispose_prewarm_view(self, *, reason: str) -> None:
        view = self._prewarm_view
        self._prewarm_view = None
        self._prewarm_started = False
        self._prewarm_cancelled = False
        self._cancel_prewarm_expire_timer()
        if view is None:
            return
        try:
            view.hide()
            view.setParent(None)
            view.deleteLater()
        except RuntimeError:
            pass
        except (AttributeError, TypeError):
            pass
        record_metric(
            "kline_webengine_prewarm_released",
            1,
            unit="count",
            tags={"reason": reason},
        )

    def _expire_prewarm(self) -> None:
        self._dispose_prewarm_view(reason="ttl")

    def _run_prewarm(self) -> None:
        started_at = time.perf_counter()
        webengine_available = getattr(self, "_webengine_available", None)
        if webengine_available is None:
            self._prewarm_started = False
            self._start_webengine_preflight_async()
            log.debug("[KLine] start WebEngine preflight before prewarm")
            return
        if webengine_available is not True:
            self._prewarm_started = False
            log.debug("[KLine] skip WebEngine prewarm before successful preflight")
            return
        if not getattr(self, "_prewarm_hidden_view_enabled", False):
            self._prewarm_started = False
            record_metric("kline_webengine_prewarm_preflight_only", 1, unit="count")
            return
        try:
            from PyQt6.QtCore import QUrl
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            from PyQt6.QtWidgets import QApplication

            if self._prewarm_cancelled:
                self._prewarm_started = False
                return
            app = QApplication.instance()
            if app is None or app.closingDown():
                self._prewarm_started = False
                return
            if self._prewarm_view is not None:
                return

            view = QWebEngineView()
            view.setObjectName("klinePrewarmWebEngine")
            view.resize(16, 16)
            view.hide()
            view.setHtml(
                "<!doctype html><html><body style='margin:0;background:#0f172a'></body></html>",
                QUrl("about:blank"),
            )
            self._prewarm_view = view
            self._schedule_prewarm_expiry()
            record_metric(
                "kline_webengine_prewarm_ms",
                (time.perf_counter() - started_at) * 1000.0,
                unit="ms",
            )
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._prewarm_started = False
            log.debug(f"[K线管理] WebEngine 预热失败: {exc}")

    def take_prewarmed_browser(self):
        view = self._prewarm_view
        if view is None:
            return None
        # QWebEngineView is a native child window on Windows. Reparenting the
        # hidden warm-up view into the real chart can paint the first frame at
        # stale geometry, so use warm-up only to start WebEngine and create a
        # fresh view for the visible window.
        self._dispose_prewarm_view(reason="consumed")
        return None

    def _remove_chart_ref(self, chart) -> None:
        alive = []
        for item in self._charts:
            if chart is not None and item is chart:
                continue
            if _is_alive(item):
                alive.append(item)
        self._charts = alive
        self._schedule_post_close_collect()

    def _schedule_post_close_collect(self) -> None:
        if getattr(self, "_post_close_collect_scheduled", False):
            return
        self._post_close_collect_scheduled = True
        try:
            from PyQt6.QtCore import QTimer

            QTimer.singleShot(1500, self._run_post_close_collect)
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
            self._run_post_close_collect()

    def _run_post_close_collect(self) -> None:
        self._post_close_collect_scheduled = False
        active_count = self.active_count
        record_metric(
            "kline_post_close_gc_skipped",
            1,
            unit="count",
            tags={"active_windows": str(active_count)},
        )

    def open_chart(
        self,
        main_window,
        code: str,
        name: str,
        data_provider,
        vcp_data: dict = None,
        code_list: list = None,
        current_idx: int = 0,
    ):
        """打开一个 K 线图窗口，自动管理窗口池数量

        参数:
            main_window: 主窗口引用(KLineChartWindow 需要)
            code: 股票代码
            name: 股票名称
            data_provider: 数据提供器
            vcp_data: VCP 分析数据(可选)
            code_list: 上下文列表(支持翻页)
            current_idx: 当前索引
        """
        started_at = time.perf_counter()
        preflight_running = getattr(self, "_webengine_available", None) is None and getattr(
            self, "_webengine_preflight_started", False
        )
        if not preflight_running and not self._ensure_webengine_available():
            self._notify_webengine_unavailable(main_window, code, name)
            return None

        from ui.kline_window_qt import KLineChartWindow

        # 清理已关闭/已销毁的窗口
        alive = []
        for chart in self._charts:
            try:
                if chart.isVisible():
                    alive.append(chart)
            except RuntimeError:
                # C++ 对象已被底层销毁，忽略即可
                pass
        self._charts = alive

        # 窗口数量到达上限时，关闭最旧的并给出 toast 提示
        while len(self._charts) >= MAX_CHART_WINDOWS:
            oldest = self._charts.pop(0)
            try:
                # 提取旧窗口的标题用于 toast 提示
                old_title = oldest.windowTitle() or "未知"
                oldest.close()
                # 通知用户（需要在 UI 线程中调用）
                try:
                    from ui.components.toast_widget import show_toast

                    show_toast(
                        f"K线窗口上限{MAX_CHART_WINDOWS}个，已自动关闭: {old_title}",
                        "info",
                        main_window,
                        duration=2000,
                    )
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as _e:
                    log.debug(f"[K线管理] toast 提示发送失败: {_e}")
            except RuntimeError:
                pass

        # 构建 vcp_data 兜底
        if vcp_data is None:
            vcp_data = {"code": code, "name": name}

        browser = self.take_prewarmed_browser()
        if browser is None and self._prewarm_started:
            self._prewarm_cancelled = True

        chart = KLineChartWindow(
            main_window=main_window,
            code=code,
            name=name,
            data_provider=data_provider,
            vcp_data=vcp_data,
            code_list=code_list or [],
            current_idx=current_idx,
            browser=browser,
        )
        try:
            chart_ref = weakref.ref(chart)
            chart.destroyed.connect(lambda _obj=None, ref=chart_ref: self._remove_chart_ref(ref()))
        except (AttributeError, RuntimeError, TypeError):
            pass
        chart.show()
        try:
            chart.raise_()
            chart.activateWindow()
        except RuntimeError:
            pass
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as _e:
            log.debug(f"[K线管理] 置前激活窗口失败: {_e}")
        self._charts.append(chart)
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        record_metric(
            "kline_open_ms",
            elapsed_ms,
            unit="ms",
            tags={"code": str(code or "").strip()},
        )
        record_metric("kline_active_windows", len(self._charts), unit="count")
        emit_structured_log(
            "kline.opened",
            code=str(code or "").strip(),
            name=str(name or "").strip(),
            active_windows=len(self._charts),
            elapsed_ms=round(elapsed_ms, 3),
        )
        return chart

    @property
    def active_count(self) -> int:
        """当前活跃的 K 线窗口数量"""
        self._charts = [c for c in self._charts if _is_alive(c)]
        return len(self._charts)


def _is_alive(chart) -> bool:
    """检查窗口是否存活"""
    try:
        return chart.isVisible()
    except RuntimeError:
        return False


# 全局单例
kline_manager = KLineWindowManager()
