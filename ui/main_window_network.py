from app.services.ui_task_lifecycle_service import task_lifecycle_for
from app.services.ui_task_service import (
    NETWORK_FORCE_RECONNECT,
    NETWORK_GO_ONLINE,
)
from core.logger import get_logger

log = get_logger(__name__)


def _resolve_status_dot_color(tone: str) -> str:
    mapping = {
        "online": "#22C55E",
        "busy": "#F59E0B",
        "offline": "#EF4444",
    }
    return mapping.get(tone, mapping["offline"])


def _set_status_tone(main_window, tone: str):
    status_bar_widget = getattr(main_window, "_status_bar_widget", None)
    if status_bar_widget is not None and hasattr(status_bar_widget, "set_status_tone"):
        status_bar_widget.set_status_tone(tone)

    status_dot = getattr(main_window, "status_dot", None)
    if status_dot is not None and hasattr(status_dot, "set_color"):
        status_dot.set_color(_resolve_status_dot_color(tone))


def _runtime_data_provider(main_window):
    provider = getattr(main_window, "data_provider", None)
    if provider is not None:
        return provider
    message = "数据服务初始化中，请稍后再试"
    status = getattr(main_window, "lbl_status", None)
    if status is not None and hasattr(status, "setText"):
        status.setText(message)
    _set_status_tone(main_window, "busy")
    log.info("[网络] %s", message)
    return None


def toggle_network(main_window):
    provider = _runtime_data_provider(main_window)
    if provider is None:
        return
    if not provider.is_online():

        def _go_online(_cancellation_token):
            provider.set_online_mode(True)
            return True

        def _on_error(error_message: str):
            log.error(f"[网络] 切换联网失败: {error_message}")
            main_window._call_in_ui(lambda: main_window._update_network_ui(False))

        from app.services.ui_task_service import background_job_runner as task_manager

        task_lifecycle_for(main_window, runner=task_manager).run_background(
            "network_mode",
            _go_online,
            task_id=NETWORK_GO_ONLINE,
            timeout_sec=30.0,
            on_success=lambda online: main_window._call_in_ui(
                lambda: main_window._update_network_ui(bool(online))
            ),
            on_error=_on_error,
            runner=task_manager,
        )
        return

    provider.set_online_mode(False)
    main_window._update_network_ui(False)


def update_network_ui(main_window, online: bool, detail: str = ""):
    _ = detail
    if not hasattr(main_window, "act_network"):
        return
    if online:
        main_window.act_network.setText("网络状态：在线")
        _set_status_tone(main_window, "online")
        return

    main_window.act_network.setText("网络状态：离线")
    _set_status_tone(main_window, "offline")


def force_reconnect(main_window):
    """重置同花顺主源及兼容回退的盘中实时行情连接。"""
    provider = _runtime_data_provider(main_window)
    if provider is None or not provider.is_online():
        return
    if hasattr(main_window, "_status_bar_widget") and main_window._status_bar_widget:
        main_window._status_bar_widget.set_status_tone("busy")

    def _reconnect_task(_cancellation_token):
        try:
            provider.force_reconnect_servers()
            probe_runner = getattr(provider, "test_network_with_probe", None)
            if callable(probe_runner):
                snapshot = probe_runner(timeout=3)
                if isinstance(snapshot, dict):
                    return {"ok": bool(snapshot.get("ok")), "probe": dict(snapshot)}
                return {"ok": bool(snapshot), "probe": {}}

            ok = bool(provider.test_network(timeout=3))
            probe_getter = getattr(provider, "get_last_network_probe", None)
            try:
                probe = probe_getter() if callable(probe_getter) else {}
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.debug("[网络] 读取重置探针明细失败: %s", exc)
                probe = {}
            return {"ok": ok, "probe": dict(probe) if isinstance(probe, dict) else {}}
        except (
            ConnectionError,
            OSError,
            RuntimeError,
            TimeoutError,
            TypeError,
            ValueError,
        ) as exc:
            log.error(f"强制重连异常: {exc}")
            return {"ok": False, "probe": {}}

    def _on_done(result):
        if isinstance(result, dict):
            ok = bool(result.get("ok"))
            probe = result.get("probe")
        else:
            ok = bool(result)
            probe = {}
        hithink_probe = str(probe.get("hithink_quote_probe") or "").strip() if isinstance(probe, dict) else ""
        main_window._update_network_ui(ok)
        from ui.components.toast_widget import show_toast

        if ok and hithink_probe == "ok":
            show_toast("同花顺已恢复，盘中实时行情主源可用。", "success", main_window, duration=2500)
            return

        if ok:
            show_toast("同花顺未通过，兼容回退可用。详情见系统日志。", "warning", main_window, duration=3500)
            return

        show_toast("盘中实时行情全部失败，请检查网络或查看系统日志。", "error", main_window, duration=3500)

    from app.services.ui_task_service import background_job_runner as task_manager

    task_lifecycle_for(main_window, runner=task_manager).run_background(
        "force_reconnect",
        _reconnect_task,
        on_success=lambda res: main_window._call_in_ui(lambda: _on_done(res)),
        task_id=NETWORK_FORCE_RECONNECT,
        timeout_sec=15.0,
        runner=task_manager,
    )
