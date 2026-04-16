from core.logger import get_logger

log = get_logger(__name__)


def toggle_network(main_window):
    if main_window.data_provider._offline:

        def _go_online():
            try:
                main_window.data_provider.set_online_mode(True)
                main_window._call_in_ui(lambda: main_window._update_network_ui(True))
            except (
                ConnectionError,
                OSError,
                RuntimeError,
                TimeoutError,
                TypeError,
                ValueError,
            ) as exc:
                log.error(f"[缃戠粶] 鍒囨崲鑱旂綉澶辫触: {exc}")
                main_window._call_in_ui(lambda: main_window._update_network_ui(False))

        from core.task_manager import task_manager

        task_manager.run_in_background(_go_online, task_id="go_online")
        return

    main_window.data_provider.set_online_mode(False)
    main_window._update_network_ui(False)


def update_network_ui(main_window, online: bool, detail: str = ""):
    _ = detail
    if not hasattr(main_window, "act_network"):
        return
    if online:
        main_window.act_network.setText("缃戠粶鐘舵€侊細鍦ㄧ嚎")
        if hasattr(main_window, "status_dot"):
            main_window.status_dot.set_color("#22C55E")
        return

    main_window.act_network.setText("缃戠粶鐘舵€侊細绂荤嚎")
    if hasattr(main_window, "status_dot"):
        main_window.status_dot.set_color("#EF4444")


def force_reconnect(main_window):
    """涓荤珯寮哄埗閲嶇疆涓滄柟璐㈠瘜瀹炴椂琛屾儏鏂规硶"""
    if not main_window.data_provider.is_online():
        return
    if hasattr(main_window, "status_dot"):
        main_window.status_dot.set_color("#F59E0B")

    def _reconnect_task():
        try:
            main_window.data_provider.force_reconnect_servers()
            return main_window.data_provider.test_network(timeout=2)
        except (
            ConnectionError,
            OSError,
            RuntimeError,
            TimeoutError,
            TypeError,
            ValueError,
        ) as exc:
            log.error(f"寮哄埗閲嶈繛寮傚父: {exc}")
            return False

    def _on_done(ok):
        main_window._update_network_ui(True)
        from ui.components.toast_widget import show_toast

        if ok:
            show_toast("涓滄柟璐㈠瘜瀹炴椂琛屾儏杩炴帴宸查噸缃€?", "success", main_window, duration=2500)
            return

        show_toast("涓滄柟璐㈠瘜瀹炴椂琛屾儏妫€娴嬪け璐ワ紝璇锋鏌ョ綉缁溿€?", "error", main_window, duration=3500)

    from core.task_manager import task_manager

    task_manager.run_in_background(
        _reconnect_task,
        on_success=lambda res: main_window._call_in_ui(lambda: _on_done(res)),
        task_id="force_reconnect",
    )
