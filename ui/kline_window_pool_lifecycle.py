# -*- coding: utf-8 -*-
"""Fail-closed lifecycle for reusable physical K-line windows."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast

from PyQt6.QtCore import Qt

from ui.kline_pool_state import (
    KLinePoolState,
    initialize_kline_pool_state,
    kline_pool_state_of,
    transition_kline_pool_state,
)

if TYPE_CHECKING:
    from ui.kline_typing import (
        KLineBrowserProtocol,
        KLineButtonProtocol,
        KLineGeometryProtocol,
        KLineOpenStagesProtocol,
        KLineRuntimeLifecycleProtocol,
        SupportsClose,
        SupportsStop,
    )

__all__ = [
    "KLinePoolState",
    "KLineWindowPoolLifecycleMixin",
    "initialize_kline_pool_state",
    "kline_pool_state_of",
    "transition_kline_pool_state",
]

_REUSED_ZERO_TIMINGS = {
    "browser_create_ms": 0.0,
    "page_handoff_slice_ms": 0.0,
    "browser_attach_sync_ms": 0.0,
    "browser_attach_total_ms": 0.0,
    "hierarchy_slice_ms": 0.0,
    "activation_queue_ms": 0.0,
    "activation_slice_ms": 0.0,
    "max_sync_slice_ms": 0.0,
    "pipeline_total_ms": 0.0,
    "host_adopt_ms": 0.0,
    "event_filters_ms": 0.0,
    "signal_ms": 0.0,
    "layout_commit_ms": 0.0,
    "surface_show_ms": 0.0,
    "layout_show_ms": 0.0,
    "placeholder_ms": 0.0,
    "recovery_ms": 0.0,
    "browser_theme_ms": 0.0,
    "stage_record_ms": 0.0,
    "load_shell_ms": 0.0,
    "load_shell_schedule_ms": 0.0,
    "load_shell_queue_ms": 0.0,
    "load_shell_dispatch_ms": 0.0,
    "total_ms": 0.0,
}


def _qt_api():
    # Resolve at call time so tests and diagnostics can patch the public Qt module.
    from ui import kline_window_qt

    return kline_window_qt


def _reset_physical_window_lease(
    window,
    main_window,
    code,
    name,
    data_provider,
    vcp_data,
    code_list,
    current_idx,
    open_context,
) -> None:
    _qt_api().reset_kline_window_lease_state(
        window,
        main_window=main_window,
        code=code,
        name=name,
        data_provider=data_provider,
        vcp_data=vcp_data,
        code_list=code_list,
        current_idx=current_idx,
        open_context=open_context,
    )


def _refresh_lease_chrome(window) -> None:
    window.setWindowTitle(f"{window.name} ({window.code}) - K线图")
    window._update_nav_buttons()
    window._apply_qt_theme()
    window._check_fav_status()
    window._set_status_message("正在准备图表...", tone="loading")


def _clear_closed_lease_state(window) -> None:
    window.df = None
    window._history_frame = None
    window._pending_frame = None
    window._fallback_snapshot_key = None
    window._latest_rt_quote = None
    window._rt_prepare_owner = None


def _release_chart_to_pool(window, pending_browser, pending_page, *, cleanup_ok: bool) -> bool:
    if (
        kline_pool_state_of(window) is not KLinePoolState.CLOSING
        or pending_browser is not None
        or pending_page is not None
    ):
        return False
    try:
        from ui.components.kline_window_manager import kline_manager

        return bool(kline_manager.release_chart(window, cleanup_ok=cleanup_ok))
    except (AttributeError, ImportError, RuntimeError, TypeError):
        return False


def _recovery_cleanup_clean(window) -> bool:
    browser = getattr(window, "browser", None)
    return browser is None or bool(_qt_api().uninstall_render_process_recovery(browser))


def _build_close_diagnostics(
    *,
    tasks_clean: bool,
    signals_clean: bool,
    recovery_clean: bool,
    pooled: bool,
    browser_clean: bool,
) -> dict:
    diagnostics = {
        "tasks_clean": bool(tasks_clean),
        "signals_clean": bool(signals_clean),
        "recovery_guard_clean": bool(recovery_clean),
        "pooled": bool(pooled),
        "browser_released": bool(browser_clean),
    }
    diagnostics["clean"] = bool(
        tasks_clean and signals_clean and recovery_clean and (pooled or browser_clean)
    )
    return diagnostics


def _call_next_close_event(window: Any, event: object) -> None:
    """Dispatch to the next concrete Qt base without constraining mixin order."""
    super(KLineWindowPoolLifecycleMixin, window).closeEvent(event)


class KLineWindowPoolLifecycleMixin:
    """Own signal, lease, pool-return, and close transitions for one window."""

    if TYPE_CHECKING:
        _apply_chart_glass_mode: Callable[[], object]
        _apply_chart_market_state: Callable[[], object]
        _apply_chart_theme: Callable[..., object]
        _browser_attach_diagnostics: dict[str, object]
        _close_diagnostics: dict[str, object]
        _fullscreen_geometry: KLineGeometryProtocol | None
        _last_shell_load_ok: bool
        _lease_signals_connected: bool
        _load_controller: SupportsClose
        _magnetically_attached: bool
        _on_global_rt_quotes: Callable[..., object]
        _on_theme_changed: Callable[..., object]
        _open_stages: KLineOpenStagesProtocol
        _pool_shell_mode: bool
        _rt_timer: SupportsStop | None
        _runtime_active: bool
        _runtime_lifecycle: KLineRuntimeLifecycleProtocol
        _shell_loaded: bool
        _snapping_to_main_window: bool
        browser: KLineBrowserProtocol
        btn_fullscreen: KLineButtonProtocol
        chart_host: object
        code: str

    @property
    def pool_state(self) -> KLinePoolState:
        return kline_pool_state_of(self)

    @property
    def _closing(self) -> bool:
        return self.pool_state in {
            KLinePoolState.CLOSING,
            KLinePoolState.IDLE,
            KLinePoolState.DISPOSED,
        }

    @property
    def _pool_idle(self) -> bool:
        return self.pool_state is KLinePoolState.IDLE

    @property
    def _pool_tainted(self) -> bool:
        return self.pool_state is KLinePoolState.TAINTED

    @property
    def _force_dispose(self) -> bool:
        return self.pool_state in {KLinePoolState.TAINTED, KLinePoolState.DISPOSED}

    def transition(self, target: KLinePoolState, *, reason: str) -> KLinePoolState:
        return transition_kline_pool_state(self, target, reason=reason)

    def _connect_lease_signals(self) -> bool:
        if self._lease_signals_connected:
            return True
        qt_api = _qt_api()
        quotes_connected = False
        try:
            qt_api.event_bus.sig_rt_quotes.connect(self._on_global_rt_quotes)
            quotes_connected = True
            qt_api.theme_manager.sig_theme_changed.connect(self._on_theme_changed)
        except (AttributeError, RuntimeError, TypeError):
            rollback_clean = True
            if quotes_connected:
                try:
                    qt_api.event_bus.sig_rt_quotes.disconnect(self._on_global_rt_quotes)
                except TypeError:
                    pass
                except (AttributeError, RuntimeError):
                    rollback_clean = False
            self._lease_signals_connected = not rollback_clean
            raise
        self._lease_signals_connected = True
        return True

    def _disconnect_lease_signals(self) -> bool:
        if not self._lease_signals_connected:
            return True
        qt_api = _qt_api()
        clean = True
        try:
            qt_api.theme_manager.sig_theme_changed.disconnect(self._on_theme_changed)
        except TypeError:
            pass
        except (AttributeError, RuntimeError):
            clean = False
        try:
            qt_api.event_bus.sig_rt_quotes.disconnect(self._on_global_rt_quotes)
        except TypeError:
            pass
        except (AttributeError, RuntimeError):
            clean = False
        self._lease_signals_connected = not clean
        return clean

    def _browser_is_pool_healthy(self) -> bool:
        browser = getattr(self, "browser", None)
        if browser is None or self.pool_state in {
            KLinePoolState.TAINTED,
            KLinePoolState.DISPOSED,
        }:
            return False
        try:
            return bool(
                self._shell_loaded
                and self._last_shell_load_ok is True
                and browser.property("klineShellReady")
                and browser.parentWidget() is self.chart_host
                and browser.page() is not None
            )
        except (AttributeError, RuntimeError, TypeError):
            return False

    def park_preheated_shell(self) -> bool:
        """Turn one fully rendered physical shell into an idle keeper."""
        qt_api = _qt_api()
        if not self._browser_is_pool_healthy():
            return False
        signals_clean = self._disconnect_lease_signals()
        recovery_clean = qt_api.uninstall_render_process_recovery(self.browser)
        if not signals_clean or not recovery_clean:
            self.transition(KLinePoolState.TAINTED, reason="preheated_shell_cleanup_failed")
            return False
        self._open_stages.stop()
        self._load_controller.close()
        self._runtime_lifecycle.begin_close()
        self._runtime_active = False
        qt_api._cancel_header_resize_refresh(self)
        self.transition(KLinePoolState.IDLE, reason="preheated_shell_parked")
        self._pool_shell_mode = False
        widget = cast(Any, self)
        widget.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        widget.hide()
        return True

    def activate_lease(
        self,
        *,
        main_window,
        code,
        name,
        data_provider,
        vcp_data=None,
        code_list=None,
        current_idx=0,
        open_started_at: float | None = None,
        open_context=None,
    ) -> bool:
        """Bind a new logical request without moving or rebuilding WebEngine."""
        if self.pool_state is not KLinePoolState.IDLE or not self._browser_is_pool_healthy():
            return False
        _reset_physical_window_lease(
            self, main_window, code, name, data_provider, vcp_data, code_list, current_idx, open_context
        )
        self.transition(KLinePoolState.ACTIVE, reason="lease_activated")
        cast(Any, self).setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._connect_lease_signals()
        if not _qt_api().install_render_process_recovery(self, self.browser):
            self.transition(KLinePoolState.TAINTED, reason="recovery_guard_install_failed")
            self._disconnect_lease_signals()
            return False
        _refresh_lease_chrome(self)
        self._apply_chart_theme(animate=False)
        self._apply_chart_market_state()
        self._apply_chart_glass_mode()
        self._browser_attach_diagnostics = {
            "full_window_reused": True, "page_reused": True, "hierarchy_unchanged": True,
            "page_handoff_ms": 0.0, "set_parent_ms": 0.0, "set_page_ms": 0.0,
            "load_shell_deferred": False, "load_shell_dispatch_ok": True,
            **_REUSED_ZERO_TIMINGS,
        }
        self._open_stages.reset_for_lease(open_started_at)
        return True

    def final_dispose(self) -> bool:
        """Destroy a parked/reclaiming shell without offering it back."""
        self.transition(KLinePoolState.DISPOSED, reason="final_dispose")
        _qt_api()._cancel_header_resize_refresh(self)
        self._disconnect_lease_signals()
        pending_browser, pending_page = self._open_stages.stop()
        browser_clean = _qt_api()._dispose_kline_browser(
            self,
            pending_browser,
            pending_page,
            allow_page_reuse=False,
        )
        widget = cast(Any, self)
        widget.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        widget.hide()
        widget.deleteLater()
        return browser_clean

    def reset_browser_for_pool(self, callback) -> bool:
        """Clear the previous lease in JS and report a fail-closed acknowledgement."""
        if not self._browser_is_pool_healthy():
            return False

        def _finished(ack) -> None:
            healthy_ack = isinstance(ack, dict) and ack.get("ok") is True and ack.get("reset") is True
            callback(bool(healthy_ack and self._browser_is_pool_healthy()))

        try:
            self.browser.page().runJavaScript(_qt_api().build_reset_lease_script("K线"), _finished)
        except (AttributeError, RuntimeError, TypeError):
            return False
        return True

    def complete_pool_return(self) -> bool:
        if self.pool_state is not KLinePoolState.CLOSING or not self._browser_is_pool_healthy():
            return False
        self._normalize_window_for_pool_return()
        self.transition(KLinePoolState.IDLE, reason="pool_return_complete")
        self._runtime_active = False
        cast(Any, self).setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        return True

    def _normalize_window_for_pool_return(self) -> None:
        """Clear lease-specific top-level state while the physical window is hidden."""
        geometry = self._fullscreen_geometry
        widget = cast(Any, self)
        if geometry is None:
            with suppress(AttributeError, RuntimeError, TypeError):
                candidate = widget.normalGeometry()
                if candidate is not None and not candidate.isNull():
                    geometry = candidate
        widget.hide()
        with suppress(AttributeError, RuntimeError, TypeError):
            widget.setWindowState(Qt.WindowState.WindowNoState)
        widget.setMinimumSize(0, 0)
        widget.setMaximumSize(16777215, 16777215)
        if geometry is not None and not geometry.isNull():
            widget.setGeometry(geometry)
        self._fullscreen_geometry = None
        self._magnetically_attached = False
        self._snapping_to_main_window = False
        self.btn_fullscreen.setText("□")
        self.btn_fullscreen.setToolTip("全屏 / 还原 K 线图 (F11)")

    def closeEvent(self, event):
        """Stop owned work and either reclaim or destroy this physical window."""
        if self._closing:
            _call_next_close_event(self, event)
            return
        if self.pool_state is KLinePoolState.TAINTED:
            self.transition(KLinePoolState.DISPOSED, reason="tainted_window_closed")
        else:
            self.transition(KLinePoolState.CLOSING, reason="window_close_requested")
        _qt_api()._cancel_header_resize_refresh(self)
        qt_api = _qt_api()
        signals_clean = self._disconnect_lease_signals()
        pending_browser, pending_page = self._open_stages.stop()
        if self._rt_timer is not None:
            self._rt_timer.stop()
        qt_api.cancel_snapshot_render_confirmation(self)
        tasks_clean = qt_api._shutdown_kline_window_tasks(self)
        _clear_closed_lease_state(self)
        recovery_clean = _recovery_cleanup_clean(self)
        pooled = _release_chart_to_pool(
            self,
            pending_browser,
            pending_page,
            cleanup_ok=bool(tasks_clean and signals_clean and recovery_clean),
        )
        browser_clean = True
        if not pooled:
            if self.pool_state is not KLinePoolState.DISPOSED:
                self.transition(KLinePoolState.DISPOSED, reason="pool_return_rejected")
            cast(Any, self).setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            browser_clean = qt_api._dispose_kline_browser(
                self, pending_browser, pending_page, allow_page_reuse=False
            )
        self._close_diagnostics = _build_close_diagnostics(
            tasks_clean=tasks_clean, signals_clean=signals_clean, recovery_clean=recovery_clean,
            pooled=pooled, browser_clean=browser_clean,
        )
        qt_api.log.debug(f"[K线] {self.code} 窗口关闭")
        _call_next_close_event(self, event)
