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

import time

from core.logger import get_logger
from core.observability import emit_structured_log, record_metric

log = get_logger(__name__)

# 可配置的最大窗口数量
MAX_CHART_WINDOWS = 5


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
        return cls._instance

    def prewarm(self, *, delay_ms: int = 2500) -> bool:
        """Warm up QWebEngine during idle time so the first K-line opens faster."""
        if self._prewarm_started or self._prewarm_view is not None:
            return False
        self._prewarm_started = True
        self._prewarm_cancelled = False
        try:
            from PyQt6.QtCore import QTimer

            QTimer.singleShot(max(0, int(delay_ms)), self._run_prewarm)
            return True
        except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as exc:
            self._prewarm_started = False
            log.debug(f"[K线管理] WebEngine 预热调度失败: {exc}")
            return False

    def _run_prewarm(self) -> None:
        started_at = time.perf_counter()
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
        self._prewarm_view = None
        if view is None:
            return None
        try:
            # QWebEngineView is a native child window on Windows. Reparenting the
            # hidden warm-up view into the real chart can paint the first frame at
            # stale geometry, so use warm-up only to start WebEngine and create a
            # fresh view for the visible window.
            view.hide()
            view.setParent(None)
            view.deleteLater()
        except RuntimeError:
            pass
        except (AttributeError, TypeError):
            pass
        self._prewarm_started = False
        return None

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
            vcp_data = {'code': code, 'name': name}

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
