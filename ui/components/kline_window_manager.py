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

import importlib.util
import os
import sys
import threading
import time
import weakref
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, cast

from app.services.kline_webengine_preflight import check_qt_webengine_available
from core.logger import get_logger
from core.observability import emit_structured_log, record_metric
from ui.kline_pool_state import (
    KLinePoolState,
    kline_pool_state_of,
)
from ui.kline_typing import KLineManagedWindowProtocol, KLinePoolParticipantProtocol
from ui.kline_webengine_page import stop_webengine_page

log = get_logger(__name__)

# 可配置的最大窗口数量
MAX_CHART_WINDOWS = 5
WEBENGINE_PREFLIGHT_TIMEOUT_S = 15
WEBENGINE_PREFLIGHT_MAX_ATTEMPTS = 2
WEBENGINE_PREFLIGHT_RETRY_DELAY_S = 0.25
WEBENGINE_PREFLIGHT_STARTUP_DELAY_MS = 8000
WEBENGINE_PREFLIGHT_IDLE_START_MS = 1500
WEBENGINE_PREFLIGHT_POLL_MS = 100
WEBENGINE_PREFLIGHT_SHUTDOWN_JOIN_MS = 750
WEBENGINE_PREWARM_LOAD_TIMEOUT_MS = 8000
FULL_WINDOW_PREWARM_POLL_MS = 25
FULL_WINDOW_PREWARM_SETTLE_MS = 300
FULL_WINDOW_RETURN_TIMEOUT_MS = 2_000
HIDDEN_PREWARM_ENV = "VCP_KLINE_HIDDEN_PREWARM"
KLINE_SHELL_READY_PROPERTY = "klineShellReady"
KLINE_SHELL_HTML_BYTES_PROPERTY = "klineShellHtmlBytes"


@dataclass(frozen=True, slots=True)
class PendingKlineOpenRequest:
    """Latest immutable-enough snapshot retained during WebEngine preparation."""

    main_window: object
    code: str
    name: str
    data_provider: object
    vcp_data: dict | None
    code_list: tuple
    current_idx: int
    open_context: object | None = None

    @classmethod
    def capture(
        cls, main_window, code, name, data_provider, vcp_data, code_list, current_idx, open_context=None
    ) -> "PendingKlineOpenRequest":
        return cls(
            main_window=main_window,
            code=str(code or ""),
            name=str(name or ""),
            data_provider=data_provider,
            vcp_data=None if vcp_data is None else dict(vcp_data),
            code_list=tuple(dict(item) if isinstance(item, dict) else item for item in (code_list or ())),
            current_idx=int(current_idx or 0),
            open_context=open_context,
        )

    def reopen(self, manager):
        return manager.open_chart(
            self.main_window,
            self.code,
            self.name,
            self.data_provider,
            self.vcp_data,
            list(self.code_list),
            self.current_idx,
            open_context=self.open_context,
        )


def _create_pending_open_bridge(callback):
    try:
        from PyQt6.QtCore import QCoreApplication, QObject, Qt, pyqtSignal, pyqtSlot

        app = QCoreApplication.instance()
        if app is None:
            return None

        class _PendingOpenBridge(QObject):
            requested = pyqtSignal()

            def __init__(self):
                super().__init__()
                cast(Any, self.requested).connect(
                    self._dispatch,
                    type=Qt.ConnectionType.QueuedConnection,
                )

            @pyqtSlot()
            def _dispatch(self):
                callback()

        bridge = _PendingOpenBridge()
        if bridge.thread() is not app.thread():
            bridge.moveToThread(app.thread())
        return bridge
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError):
        return None


class _PendingKlineOpenCoordinator:
    """Hold only the latest blocked request and resume it on the GUI thread."""

    def __init__(self, manager) -> None:
        self._manager = manager
        self._request: PendingKlineOpenRequest | None = None
        self._bridge = None
        self._lock = threading.RLock()

    @property
    def request(self) -> PendingKlineOpenRequest | None:
        with self._lock:
            return self._request

    def _ensure_bridge(self):
        if self._bridge is None:
            self._bridge = _create_pending_open_bridge(self.resume)
        return self._bridge

    def queue(self, request: PendingKlineOpenRequest) -> None:
        self._ensure_bridge()
        with self._lock:
            self._request = request

    def clear(self) -> None:
        with self._lock:
            self._request = None

    def request_resume(self) -> bool:
        bridge = self._bridge or self._ensure_bridge()
        if bridge is not None:
            bridge.requested.emit()
            return True
        if threading.current_thread() is threading.main_thread():
            self.resume()
            return True
        return False

    def resume(self):
        with self._lock:
            request, self._request = self._request, None
        if request is None or self._manager._shutting_down:
            return None
        return request.reopen(self._manager)

    def shutdown(self) -> None:
        self.clear()
        bridge, self._bridge = self._bridge, None
        if bridge is not None:
            with suppress(AttributeError, RuntimeError, TypeError):
                bridge.deleteLater()


def _load_kline_window_class():
    try:
        from ui.kline_window_qt import KLineChartWindow

        return KLineChartWindow
    except ModuleNotFoundError as exc:
        if exc.name not in {"ui", "ui.kline_window_qt"}:
            raise

        module_path = Path(__file__).resolve().parents[1] / "kline_window_qt.py"
        if not module_path.exists():
            raise

        spec = importlib.util.spec_from_file_location("ui.kline_window_qt", module_path)
        if spec is None or spec.loader is None:
            raise

        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module.KLineChartWindow


def _hidden_prewarm_enabled() -> bool:
    value = str(os.environ.get(HIDDEN_PREWARM_ENV, "") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _set_browser_property(browser, name: str, value) -> None:
    try:
        browser.setProperty(name, value)
    except (AttributeError, RuntimeError, TypeError):
        with suppress(AttributeError, RuntimeError, TypeError):
            setattr(browser, f"_{name}", value)


def _browser_property(browser, name: str, default=None):
    try:
        value = browser.property(name)
    except (AttributeError, RuntimeError, TypeError):
        value = getattr(browser, f"_{name}", default)
    return default if value is None else value


def browser_has_ready_kline_shell(browser) -> bool:
    return bool(browser is not None and _browser_property(browser, KLINE_SHELL_READY_PROPERTY, False))


def _disconnect_keeper_termination(manager, page) -> bool:
    callback = getattr(manager, "_prewarm_termination_callback", None)
    if callback is None or page is None:
        manager._prewarm_termination_callback = None
        return True
    try:
        page.renderProcessTerminated.disconnect(callback)
    except TypeError:
        manager._prewarm_termination_callback = None
        return True
    except (AttributeError, RuntimeError):
        return False
    manager._prewarm_termination_callback = None
    return True


def _install_keeper_termination(manager, page) -> bool:
    if not _disconnect_keeper_termination(manager, page):
        return False

    def _on_terminated(*_args) -> None:
        if manager._prewarm_view is not page:
            return
        _set_browser_property(page, KLINE_SHELL_READY_PROPERTY, False)
        manager._prewarm_ready = False
        manager._prewarm_failure = "render_process_terminated"
        manager._dispose_prewarm_resource(reason="render_process_terminated")

    try:
        page.renderProcessTerminated.connect(_on_terminated)
    except (AttributeError, RuntimeError, TypeError):
        return False
    manager._prewarm_termination_callback = _on_terminated
    return True


def _complete_hidden_prewarm(manager, view, started_at: float, ok: bool) -> None:
    if manager._prewarm_view is not view:
        return
    callback = getattr(manager, "_prewarm_load_callback", None)
    if callback is not None:
        with suppress(AttributeError, RuntimeError, TypeError):
            view.loadFinished.disconnect(callback)
    manager._prewarm_load_callback = None
    manager._prewarm_started = False
    if manager._prewarm_cancelled or manager._shutting_down:
        return
    if not ok:
        manager._prewarm_failure = "load_failed"
        manager._dispose_prewarm_resource(reason="load_failed")
        record_metric("kline_webengine_prewarm_failed", 1, unit="count", tags={"reason": "load_failed"})
        manager._pending_open.request_resume()
        return

    _set_browser_property(view, KLINE_SHELL_READY_PROPERTY, True)
    if not _install_keeper_termination(manager, view):
        manager._prewarm_failure = "termination_guard_failed"
        manager._dispose_prewarm_resource(reason="termination_guard_failed")
        record_metric(
            "kline_webengine_prewarm_failed",
            1,
            unit="count",
            tags={"reason": "termination_guard_failed"},
        )
        manager._pending_open.request_resume()
        return
    manager._prewarm_ready = True
    manager._prewarm_failure = ""
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    record_metric("kline_webengine_prewarm_ms", elapsed_ms, unit="ms")
    emit_structured_log("kline.webengine_prewarm_ready", elapsed_ms=round(elapsed_ms, 3))
    manager._pending_open.request_resume()


def _expire_hidden_prewarm(manager, view) -> None:
    if manager._prewarm_view is not view or manager._prewarm_ready:
        return
    manager._prewarm_started = False
    manager._prewarm_failure = "load_timeout"
    manager._dispose_prewarm_resource(reason="load_timeout")
    record_metric("kline_webengine_prewarm_failed", 1, unit="count", tags={"reason": "load_timeout"})
    manager._pending_open.request_resume()


def _load_hidden_prewarm_view(manager, view) -> None:
    if manager._prewarm_view is not view:
        return
    if manager._prewarm_cancelled or manager._shutting_down:
        manager._dispose_prewarm_resource(reason="cancelled_before_load")
        return
    try:
        from PyQt6.QtCore import QTimer, QUrl

        from ui.kline_chart_payload import build_kline_shell_html, build_kline_theme_colors

        echarts_js_path = Path(__file__).resolve().parents[2] / "assets" / "echarts.min.js"
        theme_colors = build_kline_theme_colors()
        html = build_kline_shell_html(
            title="K线",
            echarts_js_path=str(echarts_js_path),
            theme_colors=theme_colors,
        )
        base_url = QUrl.fromLocalFile(str(echarts_js_path.parent).replace("\\", "/") + "/")
        _set_browser_property(view, KLINE_SHELL_READY_PROPERTY, False)
        _set_browser_property(view, KLINE_SHELL_HTML_BYTES_PROPERTY, len(html.encode("utf-8")))
        view.setHtml(
            html,
            base_url,
        )
        QTimer.singleShot(
            WEBENGINE_PREWARM_LOAD_TIMEOUT_MS,
            lambda: _expire_hidden_prewarm(manager, view),
        )
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        manager._prewarm_started = False
        manager._prewarm_failure = str(exc)
        manager._dispose_prewarm_resource(reason="load_start_failed")
        log.debug(f"[KLine] WebEngine prewarm load failed: {exc}")
        manager._pending_open.request_resume()


def _create_hidden_prewarm_view(manager, started_at: float) -> None:
    try:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWebEngineCore import QWebEnginePage
        from PyQt6.QtWidgets import QApplication

        if manager._prewarm_cancelled:
            manager._prewarm_started = False
            return
        app = QApplication.instance()
        if app is None or app.closingDown():
            manager._prewarm_started = False
            return
        if manager._prewarm_view is not None:
            return

        page = QWebEnginePage(app)
        page.setObjectName("klinePrewarmPage")
        manager._prewarm_view = page
        manager._prewarm_ready = False
        manager._prewarm_failure = ""

        def _on_load_finished(ok: bool) -> None:
            _complete_hidden_prewarm(manager, page, started_at, bool(ok))

        manager._prewarm_load_callback = _on_load_finished
        page.loadFinished.connect(_on_load_finished)
        QTimer.singleShot(0, lambda: _load_hidden_prewarm_view(manager, page))
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        manager._prewarm_started = False
        manager._prewarm_failure = str(exc)
        manager._dispose_prewarm_resource(reason="create_failed")
        log.debug(f"[K线管理] WebEngine 预热失败: {exc}")
        manager._pending_open.request_resume()


def _full_window_keeper_ready(chart) -> bool:
    try:
        return bool(chart._browser_is_pool_healthy())
    except (AttributeError, RuntimeError, TypeError):
        return False


def _transition_chart_pool_state(
    chart: KLinePoolParticipantProtocol,
    target: KLinePoolState,
    *,
    reason: str,
) -> bool:
    try:
        transition = getattr(chart, "transition", None)
        if not callable(transition):
            return False
        transition(target, reason=reason)
    except (AttributeError, RuntimeError, TypeError):
        return False
    return True


def _full_window_keeper_failed(chart) -> bool:
    try:
        return bool(
            kline_pool_state_of(chart) in {KLinePoolState.TAINTED, KLinePoolState.DISPOSED}
            or getattr(chart, "_last_shell_load_ok", None) is False
        )
    except (AttributeError, RuntimeError, TypeError):
        return True


def _full_window_renderer_settled(chart) -> bool:
    now = time.perf_counter()
    ready_at = getattr(chart, "_prewarm_renderer_ready_at", None)
    if ready_at is None:
        chart._prewarm_renderer_ready_at = now
        return False
    return (now - float(ready_at)) * 1000.0 >= FULL_WINDOW_PREWARM_SETTLE_MS


def _dispose_full_window(chart) -> bool:
    if chart is None:
        return True
    try:
        result = chart.final_dispose()
        return True if result is None else bool(result)
    except AttributeError:
        primary_failed = False
    except (RuntimeError, TypeError):
        primary_failed = True
    hidden = False
    deleted = False
    try:
        chart.hide()
        hidden = True
    except (AttributeError, RuntimeError, TypeError):
        pass
    try:
        chart.deleteLater()
        deleted = True
    except (AttributeError, RuntimeError, TypeError):
        pass
    return bool(hidden and deleted and not primary_failed)


def _finish_full_window_prewarm(manager, chart, started_at: float, original_geometry) -> None:
    if manager._prewarm_window is not chart:
        return
    restored = True
    try:
        chart.hide()
        chart.setWindowOpacity(1.0)
        chart.setGeometry(original_geometry)
    except (AttributeError, RuntimeError, TypeError):
        restored = False
    parked = False
    if restored and not manager._shutting_down and _full_window_keeper_ready(chart):
        try:
            parked = bool(chart.park_preheated_shell())
        except (AttributeError, RuntimeError, TypeError):
            parked = False
    if not parked:
        manager._prewarm_window = None
        manager._prewarm_started = False
        manager._prewarm_ready = False
        manager._prewarm_failure = "full_window_not_healthy"
        _dispose_full_window(chart)
        manager._pending_open.request_resume()
        return
    manager._prewarm_window = None
    manager._idle_chart = chart
    manager._prewarm_started = False
    if not manager._install_idle_chart_termination(chart):
        manager._idle_chart = None
        manager._prewarm_ready = False
        manager._prewarm_failure = "full_window_termination_guard_failed"
        _dispose_full_window(chart)
        manager._pending_open.request_resume()
        return
    manager._prewarm_ready = True
    manager._prewarm_failure = ""
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    record_metric("kline_full_window_prewarm_ms", elapsed_ms, unit="ms")
    emit_structured_log("kline.full_window_prewarm_ready", elapsed_ms=round(elapsed_ms, 3))
    manager._pending_open.request_resume()


def _poll_full_window_prewarm(manager, chart, started_at: float, original_geometry) -> None:
    if manager._prewarm_window is not chart:
        return
    if manager._shutting_down or manager._prewarm_cancelled:
        manager._prewarm_window = None
        manager._prewarm_started = False
        _dispose_full_window(chart)
        manager._pending_open.request_resume()
        return
    if _full_window_keeper_failed(chart):
        manager._prewarm_window = None
        manager._prewarm_started = False
        manager._prewarm_ready = False
        manager._prewarm_failure = "full_window_failed"
        _dispose_full_window(chart)
        manager._pending_open.request_resume()
        return
    if _full_window_keeper_ready(chart) and _full_window_renderer_settled(chart):
        _finish_full_window_prewarm(manager, chart, started_at, original_geometry)
        return
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    if elapsed_ms >= WEBENGINE_PREWARM_LOAD_TIMEOUT_MS:
        manager._prewarm_window = None
        manager._prewarm_started = False
        manager._prewarm_ready = False
        manager._prewarm_failure = "full_window_timeout"
        _dispose_full_window(chart)
        manager._pending_open.request_resume()
        return
    try:
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(
            FULL_WINDOW_PREWARM_POLL_MS,
            lambda: _poll_full_window_prewarm(manager, chart, started_at, original_geometry),
        )
    except (AttributeError, ImportError, RuntimeError, TypeError):
        manager._prewarm_window = None
        manager._prewarm_started = False
        manager._prewarm_failure = "full_window_poll_failed"
        _dispose_full_window(chart)
        manager._pending_open.request_resume()


def _create_hidden_full_window_keeper(manager, started_at: float) -> None:
    main_window = getattr(manager, "_prewarm_main_window", None)
    if main_window is None:
        _create_hidden_prewarm_view(manager, started_at)
        return
    chart = None
    try:
        chart_window_class = _load_kline_window_class()
        chart = chart_window_class(
            main_window=main_window,
            code="000000",
            name="K线准备",
            data_provider=None,
            vcp_data={"code": "000000", "name": "K线准备"},
            code_list=[],
            current_idx=0,
            pool_shell=True,
            open_started_at=started_at,
        )
        manager._prewarm_window = chart
        manager._prewarm_ready = False
        original_geometry = chart.geometry()
        chart.setWindowOpacity(0.0)
        chart.move(-32000, -32000)
        chart.show()
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(
            FULL_WINDOW_PREWARM_POLL_MS,
            lambda: _poll_full_window_prewarm(manager, chart, started_at, original_geometry),
        )
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        manager._prewarm_window = None
        manager._prewarm_started = False
        manager._prewarm_ready = False
        manager._prewarm_failure = str(exc)
        _dispose_full_window(chart)
        log.debug(f"[KLine] full window prewarm failed: {exc}")
        manager._pending_open.request_resume()


def _schedule_prewarm_retry(manager) -> None:
    try:
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(WEBENGINE_PREFLIGHT_POLL_MS, manager._run_prewarm)
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as exc:
        manager._prewarm_started = False
        log.debug(f"[K线管理] WebEngine 预热重试调度失败: {exc}")


def _start_scheduled_prewarm_preflight(manager) -> None:
    if manager._prewarm_cancelled or manager._shutting_down:
        return
    manager._start_webengine_preflight_async()


def _queue_pending_open(manager, pending_request) -> None:
    if pending_request is not None:
        manager._pending_open.queue(pending_request)


def _manager_keeper_transition_in_progress(manager) -> bool:
    return bool(
        (manager._prewarm_view is not None and not manager._prewarm_ready)
        or manager._prewarm_window is not None
        or manager._reclaiming_chart is not None
    )


def _defer_open_while_preparing(
    manager, main_window, code: str, name: str, pending_request
) -> bool:
    _queue_pending_open(manager, pending_request)
    manager._notify_webengine_preparing(main_window, code, name)
    return True


def _chart_open_is_blocked(
    manager, main_window, code: str, name: str, data_provider, pending_request=None
) -> bool:
    if manager._shutting_down:
        return True
    if data_provider is None and "." not in str(code or "").strip():
        manager.notify_data_provider_preparing(main_window, code, name)
        return True
    if _manager_keeper_transition_in_progress(manager):
        return _defer_open_while_preparing(
            manager, main_window, code, name, pending_request
        )
    webengine_available = getattr(manager, "_webengine_available", None)
    if webengine_available is None:
        manager._start_webengine_preflight_async()
        return _defer_open_while_preparing(
            manager, main_window, code, name, pending_request
        )
    if not webengine_available:
        manager._notify_webengine_unavailable(main_window, code, name)
        return True
    return False


def _resolve_chart_window_class(manager, main_window, code: str, name: str):
    try:
        return _load_kline_window_class()
    except ModuleNotFoundError as exc:
        manager._notify_kline_module_unavailable(main_window, code, name, exc)
        return None


def _prune_closed_charts(manager) -> None:
    manager._charts = [chart for chart in manager._charts if _is_alive(chart)]


def _notify_chart_limit(main_window, old_title: str) -> None:
    try:
        from ui.components.toast_widget import show_toast

        show_toast(
            f"K线窗口上限{MAX_CHART_WINDOWS}个，已自动关闭: {old_title}",
            "info",
            main_window,
            duration=2000,
        )
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.debug(f"[K线管理] toast 提示发送失败: {exc}")


def _enforce_chart_limit(manager, main_window) -> bool:
    while len(manager._charts) >= MAX_CHART_WINDOWS:
        oldest = manager._charts[0]
        try:
            old_title = oldest.windowTitle() or "未知"
            with suppress(AttributeError, RuntimeError, TypeError):
                _transition_chart_pool_state(
                    oldest,
                    KLinePoolState.TAINTED,
                    reason="active_window_limit_reached",
                )
            close_result = oldest.close()
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            emit_structured_log("kline.limit_close_failed", error=str(exc))
            return False
        if close_result is False or _is_alive(oldest):
            emit_structured_log("kline.limit_close_failed", error="oldest_window_remained_alive")
            return False
        manager._charts.pop(0)
        _notify_chart_limit(main_window, old_title)
    return True


def _activate_chart(chart) -> bool:
    try:
        chart.show()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.debug(f"[K线管理] 显示窗口失败: {exc}")
        return False
    try:
        chart.raise_()
        chart.activateWindow()
    except RuntimeError:
        return True
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        log.debug(f"[K线管理] 置前激活窗口失败: {exc}")
    return True


def _record_chart_open(manager, chart, code: str, name: str, started_at: float) -> None:
    manager._charts.append(chart)
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    record_metric("kline_open_ms", elapsed_ms, unit="ms", tags={"code": str(code or "").strip()})
    record_metric("kline_active_windows", len(manager._charts), unit="count")
    emit_structured_log(
        "kline.opened",
        code=str(code or "").strip(),
        name=str(name or "").strip(),
        active_windows=len(manager._charts),
        elapsed_ms=round(elapsed_ms, 3),
    )


class _WebEnginePreflightRun:
    def __init__(self) -> None:
        self.cancellation_event = threading.Event()
        self.process_cleanup_ok: bool | None = None


def _record_preflight_process_cleanup(
    preflight_run: _WebEnginePreflightRun | None,
    result: dict | None = None,
) -> None:
    if preflight_run is None:
        return
    preflight_run.process_cleanup_ok = (
        True if result is None else bool(result.get("process_cleanup_ok", True))
    )


def _log_preflight_shutdown_failure(*, thread_clean: bool, timeout_ms: int) -> None:
    if thread_clean:
        log.warning("[KLine] WebEngine preflight child was not reaped before the hard cleanup deadline")
        return
    log.warning(f"[KLine] WebEngine preflight thread did not stop within {int(timeout_ms)}ms")


def _join_preflight_thread(thread, *, timeout_ms: int) -> bool:
    if thread is not None and thread is not threading.current_thread():
        thread.join(max(0, int(timeout_ms)) / 1000.0)
    return thread is None or not thread.is_alive()


def _finalize_preflight_join(
    manager,
    *,
    thread,
    preflight_run: _WebEnginePreflightRun | None,
    thread_clean: bool,
) -> bool:
    process_clean = preflight_run is None or preflight_run.process_cleanup_ok is not False
    clean = thread_clean and process_clean
    with manager._webengine_preflight_lock:
        if thread_clean:
            if manager._webengine_preflight_thread is thread:
                manager._webengine_preflight_thread = None
            manager._webengine_preflight_started = False
        if clean and manager._webengine_preflight_run is preflight_run:
            manager._webengine_preflight_run = None
    return clean


def _webengine_preflight_cancelled(
    manager,
    cancellation_event: threading.Event | None,
    result: dict | None = None,
) -> bool:
    return (
        manager._shutting_down
        or (cancellation_event is not None and cancellation_event.is_set())
        or bool(result is not None and result.get("cancelled"))
    )


def _commit_webengine_preflight_result(
    manager,
    cancellation_event: threading.Event | None,
    result: dict,
) -> bool:
    if _webengine_preflight_cancelled(manager, cancellation_event, result):
        return False
    manager._webengine_preflight_diagnostics = dict(result)
    ok = bool(result.get("ok"))
    manager._webengine_available = ok
    manager._webengine_failure = "" if ok else str(result.get("reason", "") or "unknown")
    elapsed_ms = result.get("elapsed_ms")
    if elapsed_ms is not None:
        record_metric(
            "kline_webengine_preflight_ms",
            elapsed_ms,
            unit="ms",
            tags={"ok": str(ok).lower()},
        )
    manager._pending_open.request_resume()
    return ok


def _preflight_attempt_summary(attempt: int, result: dict) -> dict:
    return {
        "attempt": int(attempt),
        "reason": str(result.get("reason") or ""),
        "elapsed_ms": result.get("elapsed_ms"),
        "timeout": result.get("timeout") is True,
        "returncode": result.get("returncode"),
        "process_cleanup_ok": result.get("process_cleanup_ok") is True,
        "stderr_tail": str(result.get("stderr") or "")[-500:],
    }


def _preflight_timeout_is_retryable(
    result: dict,
    *,
    attempt: int,
    cancellation_event: threading.Event | None,
) -> bool:
    return bool(
        attempt < WEBENGINE_PREFLIGHT_MAX_ATTEMPTS
        and result.get("timeout") is True
        and result.get("process_cleanup_ok") is True
        and not _cancel_requested(cancellation_event)
    )


def _cancel_requested(cancellation_event: threading.Event | None) -> bool:
    return cancellation_event is not None and cancellation_event.is_set()


def _wait_before_preflight_retry(cancellation_event: threading.Event | None) -> bool:
    if cancellation_event is not None:
        return cancellation_event.wait(WEBENGINE_PREFLIGHT_RETRY_DELAY_S)
    time.sleep(WEBENGINE_PREFLIGHT_RETRY_DELAY_S)
    return False


def _run_webengine_preflight_attempts(
    cancellation_event: threading.Event | None,
) -> dict:
    attempts = []
    result = {}
    for attempt in range(1, WEBENGINE_PREFLIGHT_MAX_ATTEMPTS + 1):
        result = check_qt_webengine_available(
            timeout_s=WEBENGINE_PREFLIGHT_TIMEOUT_S,
            cancellation_event=cancellation_event,
        )
        attempts.append(_preflight_attempt_summary(attempt, result))
        if not _preflight_timeout_is_retryable(
            result,
            attempt=attempt,
            cancellation_event=cancellation_event,
        ):
            break
        if _wait_before_preflight_retry(cancellation_event):
            break
    return {**result, "attempt_count": len(attempts), "attempts": attempts}


def _manager_can_prewarm(manager) -> bool:
    return not (
        manager._shutting_down
        or manager._prewarm_started
        or manager._prewarm_view is not None
        or manager._prewarm_window is not None
        or manager._idle_chart is not None
        or manager._reclaiming_chart is not None
        or bool(manager._charts)
    )


def _prepare_manager_prewarm(manager, *, main_window, hidden_view: bool | None) -> None:
    manager._prewarm_started = True
    manager._prewarm_cancelled = False
    manager._prewarm_ready = False
    manager._prewarm_failure = ""
    manager._prewarm_main_window = main_window
    manager._prewarm_hidden_view_enabled = (
        _hidden_prewarm_enabled() if hidden_view is None else bool(hidden_view)
    )


def _schedule_manager_prewarm(manager, delay_ms: int) -> bool:
    try:
        from PyQt6.QtCore import QTimer

        delay_ms = max(0, int(delay_ms))
        if delay_ms > WEBENGINE_PREFLIGHT_IDLE_START_MS:
            QTimer.singleShot(
                WEBENGINE_PREFLIGHT_IDLE_START_MS,
                lambda: _start_scheduled_prewarm_preflight(manager),
            )
        QTimer.singleShot(delay_ms, manager._run_prewarm)
        return True
    except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as exc:
        manager._prewarm_started = False
        manager._prewarm_cancelled = True
        log.debug(f"[K线管理] WebEngine 预热调度失败: {exc}")
        return False


def _prewarm_manager(
    manager,
    *,
    main_window,
    delay_ms: int,
    hidden_view: bool | None,
) -> bool:
    if not _manager_can_prewarm(manager):
        return False
    _prepare_manager_prewarm(
        manager,
        main_window=main_window,
        hidden_view=hidden_view,
    )
    return _schedule_manager_prewarm(manager, delay_ms)


def _detach_manager_prewarm_resources(manager):
    page = manager._prewarm_view
    prewarm_window = manager._prewarm_window
    callback = manager._prewarm_load_callback
    manager._prewarm_view = None
    manager._prewarm_window = None
    manager._prewarm_started = False
    manager._prewarm_cancelled = False
    manager._prewarm_ready = False
    manager._prewarm_load_callback = None
    return page, prewarm_window, callback


def _disconnect_prewarm_load_callback(page, callback) -> bool:
    if callback is None:
        return True
    try:
        page.loadFinished.disconnect(callback)
    except TypeError:
        return True
    except (AttributeError, RuntimeError):
        return False
    return True


def _try_prewarm_page_action(page, method_name: str, *args) -> bool:
    try:
        getattr(page, method_name)(*args)
    except (AttributeError, RuntimeError, TypeError):
        return False
    return True


def _dispose_detached_prewarm_page(manager, page, callback) -> bool:
    clean = _disconnect_prewarm_load_callback(page, callback)
    clean = _disconnect_keeper_termination(manager, page) and clean
    _set_browser_property(page, KLINE_SHELL_READY_PROPERTY, False)
    clean = stop_webengine_page(page) and clean
    clean = _try_prewarm_page_action(page, "setParent", None) and clean
    clean = _try_prewarm_page_action(page, "deleteLater") and clean
    manager._prewarm_termination_callback = None
    return clean


def _dispose_manager_prewarm_resource(manager, *, reason: str) -> bool:
    page, prewarm_window, callback = _detach_manager_prewarm_resources(manager)
    clean = True
    if prewarm_window is not None:
        clean = _dispose_full_window(prewarm_window) and clean
    if page is None:
        return clean
    clean = _dispose_detached_prewarm_page(manager, page, callback) and clean
    record_metric(
        "kline_webengine_prewarm_released",
        1,
        unit="count",
        tags={"reason": reason},
    )
    return clean


def _chart_can_return_to_pool(manager, chart, cleanup_ok: bool) -> bool:
    return not (
        chart is None
        or not cleanup_ok
        or manager._shutting_down
        or manager._idle_chart is not None
        or manager._reclaiming_chart is not None
        or manager._prewarm_window is not None
        or not _full_window_keeper_ready(chart)
    )


def _set_chart_pool_delete_policy(chart) -> bool:
    try:
        from PyQt6.QtCore import Qt

        chart.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    except (AttributeError, ImportError, RuntimeError, TypeError):
        return False
    return True


def _start_chart_pool_return(manager, chart) -> str:
    if not manager._install_idle_chart_termination(chart):
        return "termination_guard_failed"
    if not _set_chart_pool_delete_policy(chart):
        return "delete_policy_failed"
    if not manager._start_chart_return_timeout():
        return "timeout_guard_failed"
    try:
        started = chart.reset_browser_for_pool(
            lambda healthy, owned=chart: manager._complete_chart_return(owned, healthy)
        )
    except (AttributeError, RuntimeError, TypeError):
        return "reset_exception"
    return "" if started else "reset_not_started"


def _release_manager_chart(manager, chart, *, cleanup_ok: bool) -> bool:
    if not _chart_can_return_to_pool(manager, chart, cleanup_ok):
        return False
    manager._reclaiming_chart = chart
    manager._remove_chart_ref(chart)
    failure = _start_chart_pool_return(manager, chart)
    if failure:
        manager._dispose_reclaiming_chart(chart, reason=failure)
    return True


def _page_can_return_to_pool(manager, page, shell_ready: bool) -> bool:
    return not (
        page is None
        or manager._shutting_down
        or manager._prewarm_view is not None
        or manager._idle_chart is not None
        or manager._reclaiming_chart is not None
        or manager._prewarm_window is not None
        or not shell_ready
    )


def _attach_page_to_application(page) -> bool:
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None or app.closingDown():
            return False
        page.setParent(app)
        return page.parent() is app
    except (AttributeError, ImportError, RuntimeError, TypeError):
        return False


def _prepare_returned_prewarm_page(manager, page, html_bytes: int) -> None:
    _set_browser_property(page, KLINE_SHELL_READY_PROPERTY, True)
    _set_browser_property(
        page,
        KLINE_SHELL_HTML_BYTES_PROPERTY,
        max(0, int(html_bytes or 0)),
    )
    manager._prewarm_view = page
    manager._prewarm_started = False
    manager._prewarm_cancelled = False
    manager._prewarm_hidden_view_enabled = True
    manager._prewarm_ready = False
    manager._prewarm_failure = ""
    manager._prewarm_load_callback = None


def _release_manager_page(
    manager,
    page,
    *,
    shell_ready: bool,
    html_bytes: int,
) -> bool:
    if not _page_can_return_to_pool(manager, page, shell_ready):
        return False
    if not _attach_page_to_application(page):
        return False
    _prepare_returned_prewarm_page(manager, page, html_bytes)
    if not _install_keeper_termination(manager, page):
        manager._prewarm_view = None
        manager._prewarm_failure = "termination_guard_failed"
        _set_browser_property(page, KLINE_SHELL_READY_PROPERTY, False)
        return False
    manager._prewarm_ready = True
    record_metric("kline_webengine_page_returned", 1, unit="count")
    return True


@dataclass(frozen=True, slots=True)
class _ManagerShutdownResources:
    charts: list
    pooled: tuple
    return_timer: object | None


def _begin_manager_shutdown(manager) -> _ManagerShutdownResources:
    with manager._webengine_preflight_lock:
        manager._shutting_down = True
        manager._webengine_preflight_cancel_event.set()
    manager._pending_open.shutdown()
    charts, manager._charts = list(manager._charts), []
    pooled = tuple(
        dict.fromkeys(
            chart
            for chart in (manager._idle_chart, manager._reclaiming_chart)
            if chart is not None
        )
    )
    manager._idle_chart = None
    manager._reclaiming_chart = None
    manager._stop_chart_return_timeout()
    return_timer, manager._chart_return_timer = manager._chart_return_timer, None
    return _ManagerShutdownResources(charts, pooled, return_timer)


def _dispose_chart_return_timer(return_timer) -> bool:
    if return_timer is None:
        return True
    try:
        return_timer.deleteLater()
    except (AttributeError, RuntimeError, TypeError):
        return False
    return True


def _reset_shutdown_prewarm_state(manager) -> None:
    manager._prewarm_cancelled = True
    manager._prewarm_started = False
    manager._prewarm_main_window = None
    manager._prewarm_hidden_view_enabled = False


def _close_active_manager_charts(charts) -> tuple[bool, int, int]:
    close_succeeded = 0
    fallback_disposed = 0
    clean = True
    for chart in charts:
        try:
            close_result = chart.close()
        except (AttributeError, RuntimeError, TypeError):
            clean = False
            fallback_disposed += int(_dispose_full_window(chart))
            continue
        close_diagnostics = getattr(chart, "_close_diagnostics", None)
        chart_clean = not isinstance(close_diagnostics, dict) or bool(
            close_diagnostics.get("clean") is True
        )
        if close_result is False or not chart_clean:
            clean = False
            fallback_disposed += int(_dispose_full_window(chart))
        else:
            close_succeeded += 1
    return clean, close_succeeded, fallback_disposed


def _dispose_pooled_manager_charts(pooled) -> bool:
    clean = True
    for chart in pooled:
        clean = _dispose_full_window(chart) and clean
    return clean


def _build_manager_shutdown_diagnostics(
    manager,
    *,
    resources: _ManagerShutdownResources,
    active_result: tuple[bool, int, int],
    pooled_clean: bool,
    prewarm_dispose_clean: bool,
    timer_clean: bool,
    idle_guard_clean: bool,
    preflight_clean: bool,
) -> dict:
    active_clean, close_succeeded, fallback_disposed = active_result
    return {
        "active_close_clean": active_clean,
        "active_close_attempted": len(resources.charts),
        "active_close_succeeded": close_succeeded,
        "active_fallback_disposed": fallback_disposed,
        "pooled_dispose_clean": pooled_clean,
        "prewarm_dispose_clean": prewarm_dispose_clean,
        "return_timer_clean": timer_clean,
        "idle_guard_clean": idle_guard_clean,
        "preflight_clean": preflight_clean,
        "active_windows": len(manager._charts),
        "managed_keepers": manager.managed_webengine_keeper_count,
        "pending_open": manager._pending_open.request is not None,
        "prewarm_main_window_retained": manager._prewarm_main_window is not None,
    }


def _manager_shutdown_is_clean(diagnostics: dict) -> bool:
    return all(
        (
            diagnostics["active_close_clean"],
            diagnostics["pooled_dispose_clean"],
            diagnostics["prewarm_dispose_clean"],
            diagnostics["return_timer_clean"],
            diagnostics["idle_guard_clean"],
            diagnostics["preflight_clean"],
            diagnostics["active_windows"] == 0,
            diagnostics["managed_keepers"] == 0,
            not diagnostics["pending_open"],
            not diagnostics["prewarm_main_window_retained"],
        )
    )


def _shutdown_manager(manager) -> bool:
    resources = _begin_manager_shutdown(manager)
    timer_clean = _dispose_chart_return_timer(resources.return_timer)
    idle_guard_clean = manager._disconnect_idle_chart_termination()
    prewarm_dispose_clean = manager._dispose_prewarm_resource(reason="shutdown")
    _reset_shutdown_prewarm_state(manager)
    active_result = _close_active_manager_charts(resources.charts)
    pooled_clean = _dispose_pooled_manager_charts(resources.pooled)
    manager._idle_termination_callback = None
    preflight_clean = manager._join_webengine_preflight()
    diagnostics = _build_manager_shutdown_diagnostics(
        manager,
        resources=resources,
        active_result=active_result,
        pooled_clean=pooled_clean,
        prewarm_dispose_clean=prewarm_dispose_clean,
        timer_clean=timer_clean,
        idle_guard_clean=idle_guard_clean,
        preflight_clean=preflight_clean,
    )
    clean = _manager_shutdown_is_clean(diagnostics)
    diagnostics["clean"] = clean
    manager._last_shutdown_diagnostics = diagnostics
    emit_structured_log("kline.manager_shutdown", **diagnostics)
    return clean


@dataclass(frozen=True, slots=True)
class _ChartOpenArguments:
    main_window: object
    code: str
    name: str
    data_provider: object
    vcp_data: dict | None
    code_list: list | None
    current_idx: int
    open_context: object | None
    started_at: float


@dataclass(frozen=True, slots=True)
class _ChartOpenPreparation:
    chart_window_class: Callable[..., Any]
    gate_ready_at: float
    class_ready_at: float
    pruned_at: float
    limit_ready_at: float


def _prepare_chart_open(
    manager,
    arguments: _ChartOpenArguments,
    pending_request: PendingKlineOpenRequest,
) -> _ChartOpenPreparation | None:
    if _chart_open_is_blocked(
        manager,
        arguments.main_window,
        arguments.code,
        arguments.name,
        arguments.data_provider,
        pending_request=pending_request,
    ):
        return None
    gate_ready_at = time.perf_counter()
    manager._pending_open.clear()
    chart_window_class = _resolve_chart_window_class(
        manager,
        arguments.main_window,
        arguments.code,
        arguments.name,
    )
    if chart_window_class is None:
        return None
    class_ready_at = time.perf_counter()
    _prune_closed_charts(manager)
    pruned_at = time.perf_counter()
    if not _enforce_chart_limit(manager, arguments.main_window):
        record_metric("kline_window_limit_close_failed", 1, unit="count")
        return None
    return _ChartOpenPreparation(
        chart_window_class,
        gate_ready_at,
        class_ready_at,
        pruned_at,
        time.perf_counter(),
    )


def _chart_open_common_kwargs(arguments: _ChartOpenArguments) -> dict:
    return {
        "main_window": arguments.main_window,
        "code": arguments.code,
        "name": arguments.name,
        "data_provider": arguments.data_provider,
        "vcp_data": arguments.vcp_data
        if arguments.vcp_data is not None
        else {"code": arguments.code, "name": arguments.name},
        "code_list": arguments.code_list or [],
        "current_idx": arguments.current_idx,
        "open_started_at": arguments.started_at,
        "open_context": arguments.open_context,
    }


def _activate_warm_chart(warm_chart, arguments: _ChartOpenArguments):
    if warm_chart is None:
        return None
    activated = warm_chart.activate_lease(**_chart_open_common_kwargs(arguments))
    if activated:
        return warm_chart
    _dispose_full_window(warm_chart)
    return None


def _discard_unreturned_prewarm_page(page) -> None:
    stop_webengine_page(page)
    with suppress(AttributeError, RuntimeError, TypeError):
        page.setUpdatesEnabled(False)
        page.hide()
    with suppress(AttributeError, RuntimeError, TypeError):
        page.deleteLater()


def _recover_failed_chart_construction(manager, warm_page) -> None:
    if warm_page is None:
        return
    returned = manager.release_page(
        warm_page,
        shell_ready=browser_has_ready_kline_shell(warm_page),
        html_bytes=int(
            _browser_property(warm_page, KLINE_SHELL_HTML_BYTES_PROPERTY, 0) or 0
        ),
    )
    if not returned:
        _discard_unreturned_prewarm_page(warm_page)


def _construct_managed_chart(
    manager,
    preparation: _ChartOpenPreparation,
    arguments: _ChartOpenArguments,
    warm_chart,
    warm_page,
):
    try:
        warm_chart = _activate_warm_chart(warm_chart, arguments)
        return warm_chart or preparation.chart_window_class(
            browser=None,
            browser_page=warm_page,
            **_chart_open_common_kwargs(arguments),
        )
    except Exception:
        if warm_chart is not None:
            _dispose_full_window(warm_chart)
        _recover_failed_chart_construction(manager, warm_page)
        raise


def _initial_chart_open_diagnostics(
    preparation: _ChartOpenPreparation,
    *,
    started_at: float,
    keeper_ready_at: float,
    constructed_at: float,
) -> dict:
    return {
        "gate_ms": round((preparation.gate_ready_at - started_at) * 1000.0, 3),
        "resolve_class_ms": round(
            (preparation.class_ready_at - preparation.gate_ready_at) * 1000.0,
            3,
        ),
        "prune_ms": round(
            (preparation.pruned_at - preparation.class_ready_at) * 1000.0,
            3,
        ),
        "limit_ms": round(
            (preparation.limit_ready_at - preparation.pruned_at) * 1000.0,
            3,
        ),
        "take_keeper_ms": round(
            (keeper_ready_at - preparation.limit_ready_at) * 1000.0,
            3,
        ),
        "construct_ms": round((constructed_at - keeper_ready_at) * 1000.0, 3),
    }


def _install_chart_destroyed_hook(manager, chart) -> None:
    if bool(getattr(chart, "_manager_destroyed_hook_installed", False)):
        return
    with suppress(AttributeError, RuntimeError, TypeError):
        chart_ref = weakref.ref(chart)
        chart.destroyed.connect(
            lambda _obj=None, ref=chart_ref: manager._remove_chart_ref(ref())
        )
        chart._manager_destroyed_hook_installed = True


def _activate_managed_chart(chart) -> bool:
    try:
        activated = _activate_chart(chart)
    except Exception:
        _dispose_full_window(chart)
        raise
    if not activated:
        _dispose_full_window(chart)
        return False
    return True


def _finish_chart_open(
    manager,
    chart,
    arguments: _ChartOpenArguments,
    constructed_at: float,
) -> None:
    activated_at = time.perf_counter()
    chart._manager_open_diagnostics.update(
        {
            "activate_ms": round((activated_at - constructed_at) * 1000.0, 3),
            "total_ms": round((activated_at - arguments.started_at) * 1000.0, 3),
        }
    )
    emit_structured_log(
        "kline.open_dispatch",
        code=str(arguments.code or "").strip(),
        **chart._manager_open_diagnostics,
    )
    _record_chart_open(
        manager,
        chart,
        arguments.code,
        arguments.name,
        arguments.started_at,
    )


def _capture_chart_open_request(
    main_window,
    code: str,
    name: str,
    data_provider,
    vcp_data: dict | None,
    code_list: list | None,
    current_idx: int,
    open_context,
) -> tuple[_ChartOpenArguments, PendingKlineOpenRequest]:
    started_at = time.perf_counter()
    arguments = _ChartOpenArguments(
        main_window,
        code,
        name,
        data_provider,
        vcp_data,
        code_list,
        current_idx,
        open_context,
        started_at,
    )
    pending_request = PendingKlineOpenRequest.capture(
        main_window,
        code,
        name,
        data_provider,
        vcp_data,
        code_list,
        current_idx,
        open_context,
    )
    return arguments, pending_request


def _open_manager_chart(
    manager,
    main_window,
    code: str,
    name: str,
    data_provider,
    vcp_data: dict | None,
    code_list: list | None,
    current_idx: int,
    open_context,
):
    arguments, pending_request = _capture_chart_open_request(
        main_window,
        code,
        name,
        data_provider,
        vcp_data,
        code_list,
        current_idx,
        open_context,
    )
    preparation = _prepare_chart_open(manager, arguments, pending_request)
    if preparation is None:
        return None
    warm_chart = manager._take_ready_idle_chart()
    warm_page = None if warm_chart is not None else manager._take_ready_prewarm_page()
    keeper_ready_at = time.perf_counter()
    chart = _construct_managed_chart(
        manager,
        preparation,
        arguments,
        warm_chart,
        warm_page,
    )
    constructed_at = time.perf_counter()
    chart._manager_open_diagnostics = _initial_chart_open_diagnostics(
        preparation,
        started_at=arguments.started_at,
        keeper_ready_at=keeper_ready_at,
        constructed_at=constructed_at,
    )
    _install_chart_destroyed_hook(manager, chart)
    if not _activate_managed_chart(chart):
        return None
    _finish_chart_open(manager, chart, arguments, constructed_at)
    return chart


class _KLineManagerPrewarmLifecycle:
    """K 线图窗口池管理器 — 全局单例"""

    _instance: ClassVar[Any] = None
    _charts: list[KLineManagedWindowProtocol]
    _prewarm_view: Any | None
    _prewarm_window: KLineManagedWindowProtocol | None
    _prewarm_main_window: Any | None
    _idle_chart: KLineManagedWindowProtocol | None
    _reclaiming_chart: KLineManagedWindowProtocol | None
    _chart_return_timer: Any | None
    _idle_termination_callback: tuple[Any, Callable[..., object]] | None
    _prewarm_started: bool
    _prewarm_cancelled: bool
    _prewarm_hidden_view_enabled: bool
    _prewarm_ready: bool
    _prewarm_failure: str
    _prewarm_load_callback: Callable[..., object] | None
    _prewarm_termination_callback: Callable[..., object] | None
    _webengine_available: bool | None
    _webengine_failure: str
    _webengine_preflight_diagnostics: dict[str, object]
    _webengine_preflight_started: bool
    _webengine_preflight_thread: threading.Thread | None
    _webengine_preflight_run: _WebEnginePreflightRun | None
    _webengine_preflight_cancel_event: threading.Event
    _webengine_preflight_lock: threading.RLock
    _shutting_down: bool
    _last_shutdown_diagnostics: dict[str, object]
    _pending_open: _PendingKlineOpenCoordinator

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._charts = []
            cls._instance._prewarm_view = None
            cls._instance._prewarm_window = None
            cls._instance._prewarm_main_window = None
            cls._instance._idle_chart = None
            cls._instance._reclaiming_chart = None
            cls._instance._chart_return_timer = None
            cls._instance._idle_termination_callback = None
            cls._instance._prewarm_started = False
            cls._instance._prewarm_cancelled = False
            cls._instance._prewarm_hidden_view_enabled = False
            cls._instance._prewarm_ready = False
            cls._instance._prewarm_failure = ""
            cls._instance._prewarm_load_callback = None
            cls._instance._prewarm_termination_callback = None
            cls._instance._webengine_available = None
            cls._instance._webengine_failure = ""
            cls._instance._webengine_preflight_diagnostics = {}
            cls._instance._webengine_preflight_started = False
            cls._instance._webengine_preflight_thread = None
            cls._instance._webengine_preflight_run = None
            cls._instance._webengine_preflight_cancel_event = threading.Event()
            cls._instance._webengine_preflight_lock = threading.RLock()
            cls._instance._shutting_down = False
            cls._instance._last_shutdown_diagnostics = {}
            cls._instance._pending_open = _PendingKlineOpenCoordinator(cls._instance)
        return cls._instance

    def prewarm(
        self,
        *,
        main_window=None,
        delay_ms: int = WEBENGINE_PREFLIGHT_STARTUP_DELAY_MS,
        hidden_view: bool | None = None,
    ) -> bool:
        """Run WebEngine preflight and create a hidden warm-up view during idle time."""
        return _prewarm_manager(
            self,
            main_window=main_window,
            delay_ms=delay_ms,
            hidden_view=hidden_view,
        )

    def _ensure_webengine_available(
        self,
        cancellation_event: threading.Event | None = None,
        *,
        preflight_run: _WebEnginePreflightRun | None = None,
    ) -> bool:
        with self._webengine_preflight_lock:
            cached = getattr(self, "_webengine_available", None)
            if cached is not None:
                _record_preflight_process_cleanup(preflight_run)
                return bool(cached)
            if _webengine_preflight_cancelled(self, cancellation_event):
                _record_preflight_process_cleanup(preflight_run)
                return False

        result = _run_webengine_preflight_attempts(cancellation_event)
        _record_preflight_process_cleanup(preflight_run, result)
        with self._webengine_preflight_lock:
            return _commit_webengine_preflight_result(self, cancellation_event, result)

    def _start_webengine_preflight_async(self) -> bool:
        with self._webengine_preflight_lock:
            if self._shutting_down or getattr(self, "_webengine_available", None) is not None:
                return False
            existing = self._webengine_preflight_thread
            if getattr(self, "_webengine_preflight_started", False) or (
                existing is not None and existing.is_alive()
            ):
                return False

            preflight_run = _WebEnginePreflightRun()
            cancellation_event = preflight_run.cancellation_event
            self._webengine_preflight_run = preflight_run
            self._webengine_preflight_cancel_event = cancellation_event
            self._webengine_preflight_started = True
            thread: threading.Thread | None = None

            def _run() -> None:
                try:
                    self._ensure_webengine_available(
                        cancellation_event,
                        preflight_run=preflight_run,
                    )
                finally:
                    with self._webengine_preflight_lock:
                        self._webengine_preflight_started = False
                        if self._webengine_preflight_thread is thread:
                            self._webengine_preflight_thread = None

            thread = threading.Thread(
                target=_run,
                name="KLineWebEnginePreflight",
                daemon=False,
            )
            self._webengine_preflight_thread = thread
            try:
                thread.start()
            except (OSError, RuntimeError):
                cancellation_event.set()
                self._webengine_preflight_thread = None
                self._webengine_preflight_run = None
                self._webengine_preflight_started = False
                return False
            return True

    def _join_webengine_preflight(
        self,
        *,
        timeout_ms: int = WEBENGINE_PREFLIGHT_SHUTDOWN_JOIN_MS,
    ) -> bool:
        started_at = time.perf_counter()
        with self._webengine_preflight_lock:
            self._webengine_preflight_cancel_event.set()
            thread = self._webengine_preflight_thread
            preflight_run = self._webengine_preflight_run

        thread_clean = _join_preflight_thread(thread, timeout_ms=timeout_ms)
        clean = _finalize_preflight_join(
            self,
            thread=thread,
            preflight_run=preflight_run,
            thread_clean=thread_clean,
        )

        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        record_metric(
            "kline_webengine_preflight_shutdown_ms",
            elapsed_ms,
            unit="ms",
            tags={"clean": str(clean).lower()},
        )
        emit_structured_log(
            "kline.webengine_preflight_shutdown",
            clean=clean,
            elapsed_ms=round(elapsed_ms, 3),
        )
        if not clean:
            _log_preflight_shutdown_failure(thread_clean=thread_clean, timeout_ms=timeout_ms)
        return clean

    def _notify_webengine_unavailable(self, main_window, code: str, name: str) -> None:
        reason = str(getattr(self, "_webengine_failure", "") or "unknown")
        message = "K线图组件自检失败，已阻止打开以避免程序崩溃"
        try:
            from ui.components.toast_widget import show_toast

            show_toast(f"{message}：{reason}", "warning", main_window, duration=3500)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.debug(f"[K线管理] WebEngine 不可用提示失败: {exc}")
        with suppress(AttributeError, RuntimeError, TypeError, ValueError):
            status_bar = main_window.statusBar() if main_window is not None else None
            if status_bar is not None:
                status_bar.showMessage(f"{message}：{reason}", 5000)
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

    def _notify_webengine_preparing(self, main_window, code: str, name: str) -> None:
        message = "K线图组件正在准备，请稍后再打开"
        try:
            from ui.components.toast_widget import show_toast

            show_toast(message, "info", main_window, duration=2200)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.debug(f"[K线管理] WebEngine 准备中提示失败: {exc}")
        with suppress(AttributeError, RuntimeError, TypeError, ValueError):
            status_bar = main_window.statusBar() if main_window is not None else None
            if status_bar is not None:
                status_bar.showMessage(message, 3000)
        record_metric(
            "kline_webengine_preparing",
            1,
            unit="count",
            tags={"code": str(code or "").strip()},
        )
        emit_structured_log(
            "kline.webengine_preparing",
            code=str(code or "").strip(),
            name=str(name or "").strip(),
        )

    def notify_data_provider_preparing(self, main_window, code: str, name: str) -> None:
        message = "数据服务正在初始化，请稍后重试"
        with suppress(AttributeError, RuntimeError, TypeError, ValueError):
            schedule_runtime = getattr(main_window, "_schedule_post_paint_runtime", None)
            if callable(schedule_runtime):
                schedule_runtime()
        try:
            from ui.components.toast_widget import show_toast

            show_toast(message, "info", main_window, duration=2200)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.debug(f"[K线管理] 数据服务准备中提示发送失败: {exc}")
        with suppress(AttributeError, RuntimeError, TypeError, ValueError):
            status_bar = main_window.statusBar() if main_window is not None else None
            if status_bar is not None:
                status_bar.showMessage(message, 3000)
        record_metric("kline_data_provider_preparing", 1, unit="count")
        emit_structured_log(
            "kline.data_provider_preparing",
            code=str(code or "").strip(),
            name=str(name or "").strip(),
        )

    def _notify_kline_module_unavailable(self, main_window, code: str, name: str, error: Exception) -> None:
        reason = str(error or "unknown")
        message = "K线窗口模块加载失败，请重启程序后再试"
        try:
            from ui.components.toast_widget import show_toast

            show_toast(f"{message}：{reason}", "warning", main_window, duration=3500)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.debug(f"[K线管理] K线模块加载失败提示发送失败: {exc}")
        with suppress(AttributeError, RuntimeError, TypeError, ValueError):
            status_bar = main_window.statusBar() if main_window is not None else None
            if status_bar is not None:
                status_bar.showMessage(f"{message}：{reason}", 5000)
        record_metric(
            "kline_window_module_unavailable",
            1,
            unit="count",
            tags={"code": str(code or "").strip()},
        )
        emit_structured_log(
            "kline.module_unavailable",
            code=str(code or "").strip(),
            name=str(name or "").strip(),
            reason=reason,
        )

    def _dispose_prewarm_resource(self, *, reason: str) -> bool:
        return _dispose_manager_prewarm_resource(self, reason=reason)


def _unique_managed_kline_windows(manager) -> tuple:
    windows: dict[int, object] = {}
    for chart in (
        *tuple(manager._charts),
        manager._idle_chart,
        manager._reclaiming_chart,
        manager._prewarm_window,
    ):
        if chart is not None:
            windows.setdefault(id(chart), chart)
    return tuple(windows.values())


def _safe_chart_browser(chart):
    try:
        return getattr(chart, "browser", None)
    except (AttributeError, RuntimeError, TypeError):
        return None


def _safe_browser_page(browser):
    try:
        return browser.page()
    except (AttributeError, RuntimeError, TypeError):
        return None


def _managed_kline_browsers_and_pages(manager) -> tuple[dict[int, object], dict[int, object]]:
    browsers: dict[int, object] = {}
    for chart in _unique_managed_kline_windows(manager):
        browser = _safe_chart_browser(chart)
        if browser is not None:
            browsers[id(browser)] = browser

    pages: dict[int, object] = {}
    for browser in browsers.values():
        page = _safe_browser_page(browser)
        if page is not None:
            pages[id(page)] = page
    if manager._prewarm_view is not None:
        pages[id(manager._prewarm_view)] = manager._prewarm_view
    return browsers, pages


class _KLineManagerWindowPoolLifecycle(_KLineManagerPrewarmLifecycle):
    def _disconnect_idle_chart_termination(self) -> bool:
        binding = self._idle_termination_callback
        if not binding:
            return True
        page, callback = binding
        try:
            page.renderProcessTerminated.disconnect(callback)
        except TypeError:
            self._idle_termination_callback = None
            return True
        except (AttributeError, RuntimeError):
            return False
        self._idle_termination_callback = None
        return True

    def _install_idle_chart_termination(self, chart: KLineManagedWindowProtocol) -> bool:
        if not self._disconnect_idle_chart_termination():
            return False
        try:
            page = chart.browser.page()
        except (AttributeError, RuntimeError, TypeError):
            return False

        def _on_terminated(*_args) -> None:
            if self._idle_chart is not chart and self._reclaiming_chart is not chart:
                return
            _transition_chart_pool_state(
                chart,
                KLinePoolState.TAINTED,
                reason="idle_render_process_terminated",
            )
            if self._reclaiming_chart is chart:
                self._prewarm_failure = "idle_render_process_terminated"
                self._dispose_reclaiming_chart(chart, reason="render_process_terminated")
                return
            if self._idle_chart is chart:
                self._idle_chart = None
            self._prewarm_ready = False
            self._prewarm_failure = "idle_render_process_terminated"
            self._disconnect_idle_chart_termination()
            _dispose_full_window(chart)
            self._idle_termination_callback = None
            self._pending_open.request_resume()

        try:
            page.renderProcessTerminated.connect(_on_terminated)
        except (AttributeError, RuntimeError, TypeError):
            return False
        self._idle_termination_callback = (page, _on_terminated)
        return True

    def _take_ready_idle_chart(self):
        chart = self._idle_chart
        if chart is None:
            return None
        if not _full_window_keeper_ready(chart):
            self._idle_chart = None
            self._prewarm_ready = False
            self._disconnect_idle_chart_termination()
            _dispose_full_window(chart)
            self._idle_termination_callback = None
            return None
        if not self._disconnect_idle_chart_termination():
            self._idle_chart = None
            self._prewarm_ready = False
            _transition_chart_pool_state(
                chart,
                KLinePoolState.TAINTED,
                reason="idle_termination_guard_disconnect_failed",
            )
            _dispose_full_window(chart)
            self._idle_termination_callback = None
            return None
        self._idle_chart = None
        self._prewarm_ready = False
        record_metric("kline_full_window_keeper_consumed", 1, unit="count")
        return chart

    def _stop_chart_return_timeout(self) -> None:
        timer = self._chart_return_timer
        if timer is None:
            return
        with suppress(AttributeError, RuntimeError, TypeError):
            timer.stop()

    def _start_chart_return_timeout(self) -> bool:
        try:
            if self._chart_return_timer is None:
                from PyQt6.QtCore import QTimer

                timer = QTimer()
                timer.setSingleShot(True)
                timer.timeout.connect(self._on_chart_return_timeout)
                self._chart_return_timer = timer
            self._chart_return_timer.start(FULL_WINDOW_RETURN_TIMEOUT_MS)
        except (AttributeError, ImportError, RuntimeError, TypeError):
            return False
        return True

    def _dispose_reclaiming_chart(
        self, chart: KLineManagedWindowProtocol, *, reason: str
    ) -> bool:
        if self._reclaiming_chart is not chart:
            return False
        self._stop_chart_return_timeout()
        self._disconnect_idle_chart_termination()
        self._reclaiming_chart = None
        self._prewarm_ready = False
        with suppress(AttributeError, ImportError, RuntimeError, TypeError):
            from PyQt6.QtCore import Qt

            chart.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        _dispose_full_window(chart)
        self._idle_termination_callback = None
        record_metric(
            "kline_full_window_keeper_rejected",
            1,
            unit="count",
            tags={"reason": str(reason or "unknown")},
        )
        self._pending_open.request_resume()
        return True

    def _on_chart_return_timeout(self) -> None:
        chart = self._reclaiming_chart
        if chart is not None:
            self._dispose_reclaiming_chart(chart, reason="reset_timeout")

    def _complete_chart_return(
        self, chart: KLineManagedWindowProtocol, healthy: bool
    ) -> None:
        if self._reclaiming_chart is not chart:
            return
        self._stop_chart_return_timeout()
        accepted = False
        if healthy and not self._shutting_down and self._idle_chart is None:
            try:
                accepted = bool(chart.complete_pool_return())
            except (AttributeError, RuntimeError, TypeError):
                accepted = False
        if not accepted:
            self._dispose_reclaiming_chart(chart, reason="reset_rejected")
            return
        self._reclaiming_chart = None
        self._idle_chart = chart
        self._prewarm_ready = True
        record_metric("kline_full_window_keeper_returned", 1, unit="count")
        self._pending_open.request_resume()

    def release_chart(self, chart, *, cleanup_ok: bool = True) -> bool:
        """Begin fail-closed async return of one complete physical window."""
        return _release_manager_chart(self, chart, cleanup_ok=cleanup_ok)

    def _take_ready_prewarm_page(self):
        page = self._prewarm_view
        if (
            page is None
            or not self._prewarm_ready
            or not browser_has_ready_kline_shell(page)
        ):
            if page is not None:
                self._dispose_prewarm_resource(reason="invalid_keeper_page")
            return None
        if not _disconnect_keeper_termination(self, page):
            self._dispose_prewarm_resource(reason="keeper_guard_disconnect_failed")
            self._prewarm_termination_callback = None
            return None
        self._prewarm_view = None
        self._prewarm_started = False
        self._prewarm_ready = False
        self._prewarm_failure = ""
        self._prewarm_load_callback = None
        record_metric("kline_webengine_prewarm_consumed", 1, unit="count")
        return page

    def release_page(self, page, *, shell_ready: bool, html_bytes: int = 0) -> bool:
        """Return one healthy static-shell page to the bounded keeper slot."""
        return _release_manager_page(
            self,
            page,
            shell_ready=shell_ready,
            html_bytes=html_bytes,
        )

    @property
    def managed_webengine_keeper_count(self) -> int:
        """Return the bounded hidden Chromium keeper count for diagnostics."""
        keepers = {
            id(resource)
            for resource in (
                self._idle_chart,
                self._reclaiming_chart,
                self._prewarm_window,
                self._prewarm_view,
            )
            if resource is not None
        }
        return len(keepers)

    @property
    def managed_webengine_keeper_ready(self) -> bool:
        """Return whether the hidden keeper has completed its first page load."""
        if self._idle_chart is not None:
            return bool(self._prewarm_ready and _full_window_keeper_ready(self._idle_chart))
        return self._prewarm_view is not None and bool(self._prewarm_ready)

    @property
    def active_chart_view_count(self) -> int:
        count = 0
        for chart in self._charts:
            with suppress(AttributeError, RuntimeError, TypeError):
                count += getattr(chart, "browser", None) is not None
        return count

    def runtime_health_snapshot(self):
        """Return a read-only count of managed KLine browser/page objects."""
        from types import MappingProxyType

        browsers, pages = _managed_kline_browsers_and_pages(self)

        return MappingProxyType(
            {
                "browser_count": len(browsers),
                "page_count": len(pages),
                "active_window_count": len(tuple(getattr(self, "_charts", ()))),
                "keeper_count": self.managed_webengine_keeper_count,
            }
        )

    def _run_prewarm(self) -> None:
        started_at = time.perf_counter()
        if self._prewarm_cancelled or self._shutting_down:
            self._prewarm_started = False
            return
        webengine_available = getattr(self, "_webengine_available", None)
        if webengine_available is None:
            self._start_webengine_preflight_async()
            _schedule_prewarm_retry(self)
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
        if getattr(self, "_prewarm_main_window", None) is not None:
            record_metric("kline_webengine_prewarm_mode", 1, unit="count", tags={"mode": "full_window"})
            _create_hidden_full_window_keeper(self, started_at)
        else:
            record_metric("kline_webengine_prewarm_mode", 1, unit="count", tags={"mode": "page_only"})
            _create_hidden_prewarm_view(self, started_at)


class KLineWindowManager(_KLineManagerWindowPoolLifecycle):
    """K 线图窗口池管理器 — 全局单例。"""

    def shutdown(self) -> bool:
        """Release every top-level chart and delayed prewarm resource."""
        return _shutdown_manager(self)

    @property
    def shutdown_diagnostics(self) -> dict:
        return dict(self._last_shutdown_diagnostics)

    def _remove_chart_ref(self, chart) -> None:
        alive = []
        for item in self._charts:
            if chart is not None and item is chart:
                continue
            if _is_alive(item):
                alive.append(item)
        self._charts = alive

    def open_chart(
        self,
        main_window,
        code: str,
        name: str,
        data_provider,
        vcp_data: dict | None = None,
        code_list: list | None = None,
        current_idx: int = 0,
        open_context=None,
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
        return _open_manager_chart(
            self,
            main_window,
            code,
            name,
            data_provider,
            vcp_data,
            code_list,
            current_idx,
            open_context,
        )

    @property
    def active_count(self) -> int:
        """当前活跃的 K 线窗口数量"""
        self._charts = [c for c in self._charts if _is_alive(c)]
        return len(self._charts)


def _is_alive(chart) -> bool:
    """Check QObject lifetime; a temporarily hidden window is still managed."""
    if bool(getattr(chart, "_closing", False)):
        return False
    try:
        from PyQt6 import sip

        if sip.isdeleted(chart):
            return False
    except (ImportError, TypeError):
        pass
    try:
        chart.windowTitle()
    except (AttributeError, RuntimeError, TypeError):
        return False
    if not hasattr(chart, "metaObject"):
        try:
            return bool(chart.isVisible())
        except (AttributeError, RuntimeError, TypeError):
            return False
    return True


# 全局单例
kline_manager = KLineWindowManager()
