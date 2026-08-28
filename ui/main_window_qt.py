import os
import time
from contextlib import suppress

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QFrame,
    QLineEdit,
    QMainWindow,
    QVBoxLayout,
    QWidget,
)

from app.services.f5_snapshot_service import active_rps_cache_mtime
from app.services.runtime_constants import APP_VERSION, RPS_CACHE_FILE
from app.services.ui_diagnostics_service import install_ui_stall_probe, ui_stall_span
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signal_hub
from app.services.ui_task_lifecycle_service import task_lifecycle_for
from app.services.ui_task_service import STARTUP_DATA_PROVIDER, STARTUP_F5_RETENTION
from app.services.ui_task_service import background_job_runner as task_manager
from core.logger import get_logger
from ui.components.kline_window_manager import WEBENGINE_PREFLIGHT_STARTUP_DELAY_MS, kline_manager
from ui.components.tooltip_popup import hide_floating_tooltip, show_floating_tooltip
from ui.components.vector_icons import set_button_svg_icon
from ui.main_window_host_port import MainWindowHostPortMixin
from ui.main_window_tables import install_table_copy_hooks
from ui.shell import (
    DraggableTitleBar,
    MainWindowStatusBar,
    inject_standalone_tabbar,
    setup_custom_titlebar,
    setup_system_menu,
)
from ui.theme_tokens import build_ui_tokens
from ui.window_flags import (
    apply_windows_frameless_taskbar_fix,
    build_frameless_main_window_flags,
    enable_windows_native_shadow,
    enable_windows_system_backdrop,
)

# 核心引擎与数据层

log = get_logger(__name__)
POST_PAINT_TAB_ACTIVATION_DELAY_MS = 120
KLINE_PREWARM_SHELL_NAV_QUIET_SEC = 8.0
KLINE_PREWARM_USER_INPUT_QUIET_SEC = 8.0
KLINE_PREWARM_BUSY_RETRY_DELAY_MS = 1000

__all__ = ["DraggableTitleBar", "MainWindowQT"]


def create_data_provider(*, offline: bool = True):
    """Defer the market-data stack while preserving the main-window test seam."""
    from app.services.runtime_services import create_data_provider as factory

    return factory(offline=offline)


_DEFAULT_DATA_PROVIDER_FACTORY = create_data_provider


def create_startup_orchestrator(main_window, job_runner=None):
    from app.services.runtime_services import create_startup_orchestrator as factory

    return factory(main_window, job_runner=job_runner)


def create_scan_engine():
    from app.services.scan_runtime_service import create_scan_engine as factory

    return factory()


def _read_window_setting(config, *, key: str, attr: str, default, value_type):
    getter = getattr(config, "get", None)
    if callable(getter):
        return getter(key, default, value_type)
    return getattr(config, attr, default)


def _write_window_setting(config, *, key: str, attr: str, value) -> None:
    setter = getattr(config, "set", None)
    if callable(setter):
        setter(key, value)
        return
    setattr(config, attr, value)


def _workspace_tab_specs(workspace) -> list[dict]:
    tab_specs = getattr(workspace, "tab_specs", None)
    return list(tab_specs() or []) if callable(tab_specs) else []


def _resolve_last_tab_restore_target(config, workspace) -> str | int:
    specs = _workspace_tab_specs(workspace)
    valid_keys = {str(spec.get("key") or "").strip() for spec in specs}
    saved_key = str(
        _read_window_setting(
            config,
            key="window/last_active_tab_key",
            attr="last_active_tab_key",
            default="",
            value_type=str,
        )
        or ""
    ).strip()
    if saved_key in valid_keys:
        return saved_key
    try:
        legacy_index = int(
            _read_window_setting(
                config,
                key="window/last_active_tab",
                attr="last_active_tab",
                default=0,
                value_type=int,
            )
            or 0
        )
    except (TypeError, ValueError):
        legacy_index = 0
    if not specs:
        return max(0, legacy_index)
    return legacy_index if 0 <= legacy_index < len(specs) else 0


def _remember_active_tab_settings(config, workspace, index: int) -> None:
    try:
        tab_index = int(index)
    except (TypeError, ValueError):
        return
    _write_window_setting(
        config,
        key="window/last_active_tab",
        attr="last_active_tab",
        value=tab_index,
    )
    specs = _workspace_tab_specs(workspace)
    if not (0 <= tab_index < len(specs)):
        return
    tab_key = str(specs[tab_index].get("key") or "").strip()
    if tab_key:
        _write_window_setting(
            config,
            key="window/last_active_tab_key",
            attr="last_active_tab_key",
            value=tab_key,
        )


def _activate_workspace_tab(workspace, target: str | int, *, delay_ms: int = 0) -> None:
    schedule_restore = getattr(workspace, "schedule_restore_last_tab", None)
    if callable(schedule_restore):
        schedule_restore(target, delay_ms=delay_ms)
        return
    restore = getattr(workspace, "restore_last_tab", None)
    if callable(restore):
        restore(target)


def _queue_workspace_tab_activation(main_window, workspace) -> None:
    target = (
        _resolve_last_tab_restore_target(main_window._app_config, workspace)
        if main_window._restore_last_tab_enabled
        else 0
    )
    if getattr(main_window, "_post_paint_runtime_started", False):
        _activate_workspace_tab(workspace, target)
        return
    main_window._pending_workspace_tab_activation = (workspace, target)


def _activate_pending_workspace_tab(main_window) -> bool:
    pending = getattr(main_window, "_pending_workspace_tab_activation", None)
    if not pending or getattr(main_window, "_workspace", None) is not pending[0]:
        main_window._pending_workspace_tab_activation = None
        return True
    try:
        _activate_workspace_tab(*pending, delay_ms=POST_PAINT_TAB_ACTIVATION_DELAY_MS)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        log.exception("[startup] 首屏工作区激活失败")
        return False
    main_window._pending_workspace_tab_activation = None
    return True


def _run_post_paint_stage(main_window, *, flag: str, label: str, action) -> bool:
    if getattr(main_window, flag, False):
        return True
    try:
        completed = action()
    except Exception:
        log.exception("[startup] post-paint stage failed: %s", label)
        return False
    if completed is False:
        return False
    setattr(main_window, flag, True)
    return True


def _start_post_paint_scheduler(main_window) -> bool:
    scheduler = getattr(main_window, "auto_refresh_scheduler", None)
    if scheduler is None:
        return not bool(getattr(main_window, "_auto_refresh_enabled", False)) or bool(
            getattr(main_window, "_post_paint_auto_refresh_initialized", False)
        )
    scheduler.start()
    return True


def _preload_post_paint_data_runtime(*, startup_enabled: bool):
    provider = create_data_provider(offline=True)
    if startup_enabled:
        from importlib import import_module

        import_module("app.bootstrap.startup_orchestrator")
    return provider


def _resume_post_paint_runtime(main_window) -> None:
    if getattr(main_window, "_is_closing", False):
        return
    timer = getattr(main_window, "_post_paint_runtime_timer", None)
    if timer is None:
        return
    timer.stop()
    timer.start(0)


def _attach_post_paint_data_provider(main_window, provider) -> None:
    if getattr(main_window, "_is_closing", False):
        return
    main_window.data_provider = provider
    workspace = getattr(main_window, "_workspace", None)
    if workspace is not None:
        attach_runtime_services = getattr(workspace, "attach_runtime_services", None)
        if callable(attach_runtime_services):
            attach_runtime_services(data_provider=provider)
        else:
            workspace.data_provider = provider
    main_window._post_paint_data_provider_initialization_started = False
    main_window._refresh_code_count_label_from_provider()
    _resume_post_paint_runtime(main_window)


def _handle_post_paint_data_provider_error(main_window, message: str) -> None:
    main_window._post_paint_data_provider_initialization_started = False
    log.warning("[startup] data provider initialization failed: %s", message)


def _initialize_post_paint_data_provider(main_window) -> bool:
    if not hasattr(main_window, "data_provider") or main_window.data_provider is not None:
        return True
    if getattr(main_window, "_post_paint_data_provider_initialization_started", False):
        return False
    if create_data_provider is _DEFAULT_DATA_PROVIDER_FACTORY:
        from app.services.runtime_services import (
            initialize_native_dataframe_runtime,
            is_native_dataframe_runtime_ready,
        )

        if not is_native_dataframe_runtime_ready():
            log.warning("[startup] native dataframe runtime was not preinitialized; repairing on the main thread")
            initialize_native_dataframe_runtime()
    main_window._post_paint_data_provider_initialization_started = True
    startup_enabled = bool(getattr(main_window, "_startup_enabled", False))
    task_lifecycle_for(main_window, runner=task_manager).run_background(
        "startup-data-provider",
        lambda _token: _preload_post_paint_data_runtime(startup_enabled=startup_enabled),
        task_id=STARTUP_DATA_PROVIDER,
        timeout_sec=30.0,
        on_success=lambda provider: _attach_post_paint_data_provider(main_window, provider),
        on_error=lambda message: _handle_post_paint_data_provider_error(main_window, message),
        runner=task_manager,
    )
    return False


def _initialize_post_paint_startup_orchestrator(main_window) -> bool:
    if not hasattr(main_window, "startup_orchestrator") or main_window.startup_orchestrator is not None:
        return True
    main_window.startup_orchestrator = create_startup_orchestrator(main_window)
    return True


def _initialize_post_paint_scan_engine(main_window) -> bool:
    if not hasattr(main_window, "engine"):
        return True
    if main_window.engine is None:
        main_window.engine = create_scan_engine()
    workspace = getattr(main_window, "_workspace", None)
    if workspace is not None:
        attach_runtime_services = getattr(workspace, "attach_runtime_services", None)
        if callable(attach_runtime_services):
            attach_runtime_services(engine=main_window.engine)
        else:
            workspace.engine = main_window.engine
    _replay_pending_f5_request(main_window)
    return True


def _initialize_post_paint_central_quotes(main_window) -> bool:
    if not hasattr(main_window, "central_quotes_svc"):
        return True
    if main_window.central_quotes_svc is None:
        main_window._init_central_broadcaster()
    return True


def _apply_post_paint_native_window_effects(main_window) -> bool:
    if not hasattr(main_window, "_native_taskbar_fix_applied"):
        return True
    if main_window._native_taskbar_fix_applied:
        return True
    main_window._native_taskbar_fix_applied = True
    apply_windows_frameless_taskbar_fix(main_window)
    enable_windows_native_shadow(main_window)
    enable_windows_system_backdrop(main_window, backdrop="mica", dark=bool(build_ui_tokens()["is_dark"]))
    return True


def _f5_runtime_dependencies_ready(main_window) -> bool:
    return getattr(main_window, "data_provider", None) is not None and getattr(main_window, "engine", None) is not None


def _replay_pending_f5_request(main_window) -> None:
    if not getattr(main_window, "_pending_f5_request", False) or not _f5_runtime_dependencies_ready(main_window):
        return
    main_window._pending_f5_request = False

    def _replay() -> None:
        if not getattr(main_window, "_is_closing", False):
            main_window._action_refresh_f5()

    QTimer.singleShot(0, _replay)


def _schedule_f5_startup_retention(main_window) -> bool:
    from app.services.f5_runtime_maintenance import prune_startup_f5_runtime

    task_lifecycle_for(main_window, runner=task_manager).run_background(
        "f5_startup_retention",
        lambda _token: prune_startup_f5_runtime(),
        task_id=STARTUP_F5_RETENTION,
        timeout_sec=30.0,
        on_error=lambda message: log.warning("[F5] startup retention skipped: %s", message),
        runner=task_manager,
    )
    return True


def _schedule_post_paint_retry(main_window) -> None:
    if getattr(main_window, "_is_closing", False):
        return
    timer = getattr(main_window, "_post_paint_runtime_timer", None)
    if timer is None or timer.isActive():
        return
    attempt = int(getattr(main_window, "_post_paint_retry_attempt", 0) or 0) + 1
    main_window._post_paint_retry_attempt = attempt
    delay_ms = min(5000, 250 * (2 ** min(attempt - 1, 5)))
    timer.start(delay_ms)


def _schedule_post_paint_continuation(main_window) -> None:
    if getattr(main_window, "_is_closing", False):
        return
    timer = getattr(main_window, "_post_paint_runtime_timer", None)
    if timer is not None and not timer.isActive():
        timer.start(0)


def _disconnect_main_window_runtime_signals(
    main_window,
    *,
    domain_bus=event_bus,
    ui_bus=ui_signal_hub,
    theme_bus=None,
) -> None:
    if getattr(main_window, "_runtime_signals_disconnected", False):
        return
    if theme_bus is None:
        from ui.theme import theme_manager

        theme_bus = theme_manager
    bindings = (
        (domain_bus.sig_network_status_changed, getattr(main_window, "_update_network_ui", None)),
        (domain_bus.sig_rt_quotes, getattr(main_window, "_on_rt_quotes_pulse", None)),
        (ui_bus.sig_task_progress, getattr(main_window, "_on_task_progress", None)),
        (ui_bus.sig_show_kline, getattr(main_window, "_on_show_kline", None)),
        (ui_bus.sig_show_kline_with_list, getattr(main_window, "_on_show_kline_with_list", None)),
        (theme_bus.sig_theme_changed, getattr(main_window, "_apply_theme", None)),
    )
    for signal, slot in bindings:
        if slot is None:
            continue
        with suppress(AttributeError, RuntimeError, TypeError):
            signal.disconnect(slot)
    main_window._runtime_signals_disconnected = True


def _background_tab_preload_busy(workspace, *, now: float) -> bool:
    if workspace is None or not bool(getattr(workspace, "_background_prewarm_enabled", False)):
        return False
    if not bool(getattr(workspace, "_background_prewarm_finished", False)):
        return True
    try:
        finished_age = now - float(getattr(workspace, "_background_prewarm_finished_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        return True
    return 0.0 <= finished_age < KLINE_PREWARM_SHELL_NAV_QUIET_SEC


def _background_tab_preload_settled(workspace) -> bool:
    if workspace is None:
        return False
    status_reader = getattr(workspace, "background_preload_status", None)
    if not callable(status_reader):
        return False
    try:
        status = status_reader()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    if not isinstance(status, dict):
        return False
    if status.get("enabled") is False:
        return True
    return all(
        (
            status.get("enabled") is True,
            status.get("finished") is True,
            not str(status.get("active_key") or "").strip(),
            not list(status.get("remaining_keys") or []),
            not list(status.get("pending_priority_keys") or []),
            not str(status.get("cancelling_key") or "").strip(),
            status.get("active_step_count") == 0,
        )
    )


def _asian_market_tab_visible(main_window) -> bool:
    workspace = getattr(main_window, "_workspace", None)
    current_tab_key = getattr(workspace, "current_tab_key", None)
    if not callable(current_tab_key):
        return False
    try:
        return str(current_tab_key() or "").strip() == "asian_market"
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _kline_prewarm_runtime_busy(main_window) -> bool:
    workspace = getattr(main_window, "_workspace", None)
    now = time.perf_counter()
    if _background_tab_preload_busy(workspace, now=now):
        return True
    last_shell_nav_at = getattr(workspace, "_last_shell_nav_load_at", 0.0)
    try:
        shell_nav_age = now - float(last_shell_nav_at or 0.0)
    except (TypeError, ValueError):
        shell_nav_age = KLINE_PREWARM_SHELL_NAV_QUIET_SEC
    if last_shell_nav_at and 0.0 <= shell_nav_age < KLINE_PREWARM_SHELL_NAV_QUIET_SEC:
        return True
    last_user_input_at = getattr(main_window, "_last_user_interaction_at", 0.0)
    try:
        user_input_age = now - float(last_user_input_at or 0.0)
    except (TypeError, ValueError):
        user_input_age = KLINE_PREWARM_USER_INPUT_QUIET_SEC
    if last_user_input_at and 0.0 <= user_input_age < KLINE_PREWARM_USER_INPUT_QUIET_SEC:
        return True
    if bool(
        getattr(main_window, "_pending_f5_request", False)
        or getattr(main_window, "_f5_precompute_start_pending", False)
    ):
        return True
    controller = getattr(main_window, "_f5_job_controller", None)
    running = getattr(controller, "is_running", False)
    try:
        return bool(running() if callable(running) else running)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _try_post_paint_kline_prewarm(main_window) -> None:
    if getattr(main_window, "_is_closing", False) or not bool(
        getattr(main_window, "_kline_prewarm_enabled", False)
    ):
        return
    if _kline_prewarm_runtime_busy(main_window):
        QTimer.singleShot(
            KLINE_PREWARM_BUSY_RETRY_DELAY_MS,
            lambda: _try_post_paint_kline_prewarm(main_window),
        )
        return
    kline_manager.prewarm(delay_ms=0, hidden_view=True)


def _schedule_post_paint_kline_prewarm(main_window) -> bool:
    if getattr(main_window, "_is_closing", False) or not bool(
        getattr(main_window, "_kline_prewarm_enabled", False)
    ):
        return True
    QTimer.singleShot(
        WEBENGINE_PREFLIGHT_STARTUP_DELAY_MS,
        lambda: _try_post_paint_kline_prewarm(main_window),
    )
    return True


def _post_paint_stage_specs(main_window):
    return (
        ("_post_paint_native_effects_applied", "native_window_effects", lambda: _apply_post_paint_native_window_effects(main_window)),
        ("_post_paint_data_provider_initialized", "data_provider", lambda: _initialize_post_paint_data_provider(main_window)),
        ("_post_paint_startup_orchestrator_initialized", "startup_orchestrator", lambda: _initialize_post_paint_startup_orchestrator(main_window)),
        ("_post_paint_scan_engine_initialized", "scan_engine", lambda: _initialize_post_paint_scan_engine(main_window)),
        ("_post_paint_central_quotes_initialized", "central_quotes", lambda: _initialize_post_paint_central_quotes(main_window)),
        ("_post_paint_tab_activation_finished", "tab_activation", lambda: _activate_pending_workspace_tab(main_window)),
        ("_post_paint_f5_retention_scheduled", "f5_startup_retention", lambda: _schedule_f5_startup_retention(main_window)),
        (
            "_post_paint_auto_refresh_initialized",
            "auto_refresh_initialization",
            lambda: main_window._initialize_auto_refresh_services() if main_window._auto_refresh_enabled else None,
        ),
        (
            "_post_paint_startup_scheduled",
            "startup_schedule",
            lambda: main_window.startup_orchestrator.schedule_startup() if main_window._startup_enabled else None,
        ),
        ("_post_paint_scheduler_started", "auto_refresh_start", lambda: _start_post_paint_scheduler(main_window)),
        (
            "_post_paint_kline_prewarm_scheduled",
            "kline_prewarm",
            lambda: _schedule_post_paint_kline_prewarm(main_window),
        ),
    )


def _run_post_paint_stages(main_window) -> tuple[bool, ...]:
    pending = [spec for spec in _post_paint_stage_specs(main_window) if not getattr(main_window, spec[0], False)]
    if not pending:
        return (True,)
    flag, label, action = pending[0]
    ready = _run_post_paint_stage(main_window, flag=flag, label=label, action=action)
    main_window._post_paint_stage_progressed = bool(ready)
    return (bool(ready and len(pending) == 1),)


def _run_post_paint_runtime(main_window) -> None:
    if main_window._is_closing or main_window._post_paint_runtime_started:
        return
    started_at = time.perf_counter()
    main_window._post_paint_stage_progressed = False
    stage_results = _run_post_paint_stages(main_window)
    succeeded = all(stage_results)
    main_window._post_paint_runtime_started = succeeded
    if succeeded:
        main_window._post_paint_retry_attempt = 0
    elif main_window._post_paint_stage_progressed:
        _schedule_post_paint_continuation(main_window)
    else:
        _schedule_post_paint_retry(main_window)

    from core.observability import emit_structured_log, record_metric

    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    record_metric("main_window_post_paint_runtime_ms", elapsed_ms, unit="ms")
    emit_structured_log(
        "main_window.post_paint_runtime",
        elapsed_ms=round(elapsed_ms, 3),
        succeeded=succeeded,
    )


def _update_titlebar_quote_status(sync_widget, payload: object) -> None:
    pulse = getattr(sync_widget, "pulse_quotes", None)
    if callable(pulse):
        pulse()
    set_quote_status = getattr(sync_widget, "set_quote_status", None)
    if callable(set_quote_status):
        set_quote_status(payload)


class MainWindowQT(MainWindowHostPortMixin, QMainWindow):
    """紫金研选主窗口 — 纯外壳控制器（Phase 2 重构后）"""

    _sig_f5_done = pyqtSignal(int, float)
    _sig_ui_call = pyqtSignal(object)

    @pyqtSlot(object)
    def _run_ui_callback(self, callback):
        try:
            callback()
        except (AttributeError, RuntimeError, TypeError, ValueError) as e:
            log.error(f"[UI回调] 异常: {e}")

    def _call_in_ui(self, callback):
        if getattr(self, "_is_closing", False):
            return
        self._sig_ui_call.emit(callback)

    # nativeEvent 已移除：PyQt6 的 sip.voidptr 与 ctypes 内存布局不兼容，
    # 会导致 Windows 段错误。边缘缩放改用纯 Python 鼠标事件实现。

    def __init__(
        self,
        splash=None,
        *,
        startup_enabled: bool = True,
        auto_refresh_enabled: bool | None = None,
        background_prewarm: bool = True,
        kline_prewarm_enabled: bool = True,
        central_quotes_enabled: bool = True,
        restore_last_tab_enabled: bool = True,
        controlled_startup_probe_guard: bool | None = None,
    ):
        super().__init__()
        self._project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._launch_started_at = time.perf_counter()

        from app.bootstrap import ApplicationBootstrap
        from app.services.ui_config_service import app_config
        from app.use_cases import WindowCommandService
        from core.cache_manager import CacheManager
        from core.process_watchdog import ProcessWatchdog, log_process_snapshot

        self._first_paint_recorded = False
        self._post_paint_runtime_started = False
        self._post_paint_runtime_timer = QTimer(self)
        self._post_paint_runtime_timer.setSingleShot(True)
        self._post_paint_runtime_timer.setInterval(0)
        self._post_paint_runtime_timer.timeout.connect(self._start_post_paint_runtime)
        self._is_closing = False
        self._startup_enabled = bool(startup_enabled)
        self._auto_refresh_enabled = self._startup_enabled if auto_refresh_enabled is None else bool(auto_refresh_enabled)
        self._workspace_background_prewarm = bool(background_prewarm)
        self._kline_prewarm_enabled = bool(kline_prewarm_enabled)
        self._central_quotes_enabled = bool(central_quotes_enabled)
        self._restore_last_tab_enabled = bool(restore_last_tab_enabled)
        self._controlled_startup_probe_guard = (
            not self._startup_enabled
            if controlled_startup_probe_guard is None
            else bool(controlled_startup_probe_guard)
        )
        self._native_taskbar_fix_applied = False
        self._app_cursor_filter_installed = False
        self._splash = splash
        self.setWindowTitle("紫金研选")

        # 记录默认逻辑工作区
        self.setWindowIcon(QIcon(os.path.join(os.path.dirname(os.path.dirname(__file__)), "bull_icon.ico")))
        # 无边框改造：去掉原生标题栏，由自定义标题栏接管
        self.setWindowFlags(build_frameless_main_window_flags())
        self.setMinimumSize(1000, 600)
        self._sig_ui_call.connect(self._run_ui_callback)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._app_cursor_filter_installed = True
        self._process_watchdog = ProcessWatchdog(
            project_root=self._project_root,
            logger=log,
        )
        self._process_watchdog.start(self)
        log_process_snapshot(
            "main_window.init.begin",
            logger=log,
            project_root=self._project_root,
            direct_watchdog=True,
        )

        # 绑定系统级全局网络状态变更，确保所有角色的状态与UI强同步
        event_bus.sig_network_status_changed.connect(self._update_network_ui)

        self.startup_orchestrator = None
        self.cache_manager = CacheManager()
        self._f5_cancelled = self._pending_f5_request = False
        self._f5_precompute_ui_grace_until = 0.0
        self._last_user_interaction_at = 0.0
        self._titlebar_sync_state = "idle"
        self._last_sync_freshness = ""
        self._command_palette = None
        self._runtime_health_dialog = None
        self._app_config = app_config
        self._settings = self._app_config.section("window", legacy_scope="MainWindowQT")
        self._workspace = None
        self.tabs = None
        self._ui_stall_probe = install_ui_stall_probe(
            app,
            parent=self,
            context_provider=self._ui_stall_context,
        )
        self._bootstrap = ApplicationBootstrap(self)
        self._command_service = WindowCommandService(self)

        self._splash_update(60, "正在构建主界面模块...")
        self.data_provider = None
        self.engine = None
        self.central_quotes_svc = None
        self.na_daily_service = None
        self.asian_market_service = None
        self.earnings_refresh_service = None
        self.auto_refresh_scheduler = None

        # 正常入口已在创建启动页前安装全局 QSS；直接构造窗口的诊断入口仅在缺失时补装。
        app_instance = QApplication.instance()
        if app_instance is not None and not app_instance.styleSheet():
            from ui.styles.global_qss import generate_global_qss

            app_instance.setStyleSheet(generate_global_qss())
        # 监听主题切换信号，实时刷新全局样式
        from ui.theme import theme_manager

        theme_manager.sig_theme_changed.connect(self._apply_theme)

        main_widget = QWidget()
        main_widget.setObjectName("mainWindowFrame")
        main_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCentralWidget(main_widget)

        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 自定义标题栏：品牌文字 + Tab导航 + 窗口控制按钮 合并一行
        self._init_custom_titlebar(main_layout)

        self._splash_update(75, "组件注册中...")

        self._init_right_panel()
        main_layout.addWidget(self.tabs_wrapper, 1)
        self._init_global_shortcuts()

        self._status_bar_widget = MainWindowStatusBar(f"v{APP_VERSION}", self)
        self.status_dot = self._status_bar_widget.status_dot
        self.lbl_status = self._status_bar_widget.lbl_status
        self.lbl_code_count = self._status_bar_widget.lbl_code_count
        self.lbl_clock = self._status_bar_widget.lbl_clock
        self.lbl_version = self._status_bar_widget.lbl_version
        self._refresh_code_count_label_from_provider()
        main_layout.addWidget(self._status_bar_widget, 0)

        # 9. 恢复之前的界面布局、列宽、表格排序
        self._restore_ui_state()

        self._splash_update(90, "正在加载数据...")
        if not self._startup_enabled:
            log.info("[startup] startup timers disabled for controlled window construction")

        if not self._auto_refresh_enabled:
            log.info("[startup] auto refresh scheduler disabled for controlled window construction")
        self._update_last_f5_time()
        log_process_snapshot(
            "main_window.init.ready",
            logger=log,
            project_root=self._project_root,
            direct_watchdog=True,
        )

    def _initialize_auto_refresh_services(self) -> None:
        if not self._auto_refresh_enabled or self.auto_refresh_scheduler is not None:
            return

        from app.services.na_daily_service import NADailyRefreshService
        from ui.services.asian_market_runtime_service import AsianMarketRuntimeService
        from ui.services.auto_refresh_scheduler import AutoRefreshScheduler
        from ui.services.earnings_refresh_service import EarningsRefreshService

        self.na_daily_service = NADailyRefreshService(parent=self)
        self.asian_market_service = AsianMarketRuntimeService(parent=self)
        self.earnings_refresh_service = EarningsRefreshService(parent=self)
        self.auto_refresh_scheduler = AutoRefreshScheduler(
            data_provider=self.data_provider,
            engine=self.engine,
            na_daily_service=self.na_daily_service,
            asian_market_service=self.asian_market_service,
            earnings_service=self.earnings_refresh_service,
            asian_market_visible_checker=lambda: _asian_market_tab_visible(self),
            background_preload_settled_checker=lambda: _background_tab_preload_settled(
                getattr(self, "_workspace", None)
            ),
            parent=self,
        )

    def _schedule_post_paint_runtime(self) -> None:
        if self._is_closing or self._post_paint_runtime_started:
            return
        if not self._post_paint_runtime_timer.isActive():
            self._post_paint_runtime_timer.start()

    @pyqtSlot()
    def _start_post_paint_runtime(self) -> None:
        _run_post_paint_runtime(self)

    def _init_central_broadcaster(self):
        if not self._central_quotes_enabled:
            self.central_quotes_svc = None
            log.info("[UI] central quotes disabled for controlled window construction")
            return
        self._bootstrap.install_central_quotes()

    def _refresh_code_count_label_from_provider(self) -> int:
        provider = getattr(self, "data_provider", None)
        cache_data = getattr(provider, "cache_data", None) or {}
        code_name_map = getattr(provider, "code2name", None) or {}
        count = len(cache_data)
        if count <= 0:
            count = sum(1 for raw_code in code_name_map if self._is_display_a_share_code(raw_code))
        if count > 0 and hasattr(self, "lbl_code_count"):
            self.lbl_code_count.setText(f"标的池: {count} 只")
        return count

    @staticmethod
    def _is_display_a_share_code(raw_code) -> bool:
        code = str(raw_code or "").strip()
        return len(code) == 6 and code.isdigit() and code.startswith(("60", "68", "00", "30"))

    # 联网成功后的各 Tab 刷新逻辑由 _on_smart_startup_online_done 负责

    def _on_smart_startup_online_done(self):
        """智能启动联网成功后，触发各Tab的实时数据刷新"""
        from ui.main_window_runtime import safe_run_post_online_refresh

        safe_run_post_online_refresh(self, task_manager)

    def _toggle_network(self):
        from ui.main_window_network import toggle_network

        toggle_network(self)

    def _update_network_ui(self, online: bool, detail: str = ""):
        from ui.main_window_network import update_network_ui

        update_network_ui(self, online, detail=detail)

    def _force_reconnect(self):
        from ui.main_window_network import force_reconnect

        force_reconnect(self)

    def _splash_update(self, value: int, status: str = ""):
        """update progress"""
        if self._splash:
            self._splash.set_progress(value, status)

    def _init_gear_menu(self):
        """在标题栏右侧注入系统菜单。"""
        setup_system_menu(self)
        self._update_last_f5_time()

    def _init_global_shortcuts(self):
        self._shortcut_f5 = QShortcut(QKeySequence("F5"), self)
        self._shortcut_f5.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_f5.activated.connect(self._action_refresh_f5)

        self._shortcut_command_palette = QShortcut(QKeySequence("Ctrl+K"), self)
        self._shortcut_command_palette.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_command_palette.activated.connect(self._open_command_palette)

        self._shortcut_escape = QShortcut(QKeySequence("Esc"), self)
        self._shortcut_escape.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_escape.activated.connect(self._handle_escape_shortcut)

    def _handle_escape_shortcut(self):
        if self._command_palette is not None and self._command_palette.isVisible():
            self._command_palette.reject()
            return

        app = QApplication.instance()
        popup = app.activePopupWidget() if app is not None else None
        if popup is not None and popup is not self:
            popup.close()
            return

        modal = app.activeModalWidget() if app is not None else None
        if modal is not None and modal is not self and hasattr(modal, "reject"):
            modal.reject()
            return

        focus_widget = self.focusWidget()
        if isinstance(focus_widget, QLineEdit):
            focus_widget.clearFocus()

    def _activate_workspace_tab(self, tab_index: int):
        if self.tabs is not None and 0 <= int(tab_index) < self.tabs.count():
            workspace = getattr(self, "_workspace", None)
            activate_tab = getattr(workspace, "activate_tab", None)
            if callable(activate_tab) and activate_tab(int(tab_index), reason="command"):
                return
            self.tabs.setCurrentIndex(int(tab_index))

    def trigger_global_sync(self):
        self._action_refresh_f5()

    def activate_workspace_tab(self, tab_index: int):
        self._activate_workspace_tab(tab_index)

    def apply_table_density(self, mode: str, persist: bool = True):
        self._apply_table_density(mode, persist=persist)

    def open_security_chart(self, code: str):
        self._on_show_kline(code)

    def theme_names(self) -> list[str]:
        from ui.theme import theme_manager

        return list(theme_manager.theme_names())

    def switch_theme(self, theme_name: str):
        from ui.theme import theme_manager

        theme_manager.switch_theme(theme_name)

    def create_workspace(self, parent=None):
        from ui.workspaces import ClassicWorkspace

        with ui_stall_span("MainWindowQT.create_workspace", tab=self._current_workspace_tab_key()):
            return ClassicWorkspace(
                self.data_provider,
                self.engine,
                host=self,
                parent=parent if parent is not None else self.tabs_wrapper,
                background_prewarm=self._workspace_background_prewarm,
                watchlist_startup_tasks=self._startup_enabled,
                controlled_startup_probe_guard=self._controlled_startup_probe_guard,
            )

    def _current_workspace_tab_key(self) -> str:
        workspace = getattr(self, "_workspace", None)
        tabs = getattr(self, "tabs", None)
        if workspace is None or tabs is None:
            return ""
        try:
            index = int(tabs.currentIndex())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return ""
        specs = list(getattr(workspace, "tab_specs", lambda: [])() or [])
        if 0 <= index < len(specs):
            return str(specs[index].get("key") or "").strip()
        return ""

    def _ui_stall_context(self) -> dict:
        tab_key = self._current_workspace_tab_key()
        context = {"window": self.__class__.__name__}
        if tab_key:
            context["tab"] = tab_key
        if tab_key == "system_log":
            try:
                if time.perf_counter() < float(getattr(self, "_f5_precompute_ui_grace_until", 0.0) or 0.0):
                    context["background"] = "f5_precompute"
            except (TypeError, ValueError):
                pass
        tabs = getattr(self, "tabs", None)
        current_widget = None
        try:
            current_widget = tabs.currentWidget() if tabs is not None else None
        except (AttributeError, RuntimeError, TypeError, ValueError):
            current_widget = None
        if current_widget is not None:
            context["widget"] = current_widget.__class__.__name__
        return context

    def _rebind_workspace_chrome(self) -> None:
        tabs = getattr(self, "tabs", None)
        if tabs is not None:
            try:
                tabs.tabBar().setVisible(False)
            except (AttributeError, RuntimeError, TypeError) as exc:
                log.debug(f"[UI] 隐藏工作区原生标签栏失败: {exc}")

        nav_widget = getattr(self, "_shell_navigation_widget", None)
        bind_workspace = getattr(nav_widget, "bind_workspace", None)
        if callable(bind_workspace):
            bind_workspace(getattr(self, "_workspace", None), tabs)
            return

        try:
            self._standalone_tabbar = inject_standalone_tabbar(self)
        except (AttributeError, RuntimeError, TypeError) as exc:
            log.warning(f"[UI] 重新绑定标题栏导航失败: {exc}")

    def _refresh_central_quote_code_supplier(self) -> None:
        service = getattr(self, "central_quotes_svc", None)
        if service is None:
            return
        workspace = getattr(self, "_workspace", None)
        code_supplier = getattr(workspace, "get_realtime_quote_coverage", None)
        if not callable(code_supplier):
            code_supplier = getattr(workspace, "get_realtime_quote_codes", None)
        setter = getattr(service, "set_code_supplier", None)
        if callable(setter):
            setter(code_supplier)
            return
        log.warning("[UI] 中央报价服务不支持刷新 code_supplier")

    def replace_workspace(self, workspace):
        with ui_stall_span("MainWindowQT.replace_workspace", tab=self._current_workspace_tab_key()):
            return self._replace_workspace_impl(workspace)

    def _replace_workspace_impl(self, workspace):
        existing_workspace = getattr(self, "_workspace", None)
        existing_tabs = getattr(existing_workspace, "tabs", None)
        previous_tabs = getattr(self, "tabs", None)
        if existing_workspace is not None:
            with suppress(AttributeError, TypeError, RuntimeError):
                existing_tabs.currentChanged.disconnect(self._remember_last_active_tab)

        self._tabs_wrapper_layout.addWidget(workspace, 1)
        self._workspace = workspace
        self.tabs = workspace.tabs
        try:
            _queue_workspace_tab_activation(self, workspace)
            self.install_workspace_table_copy_hooks()
            self.tabs.currentChanged.connect(self._remember_last_active_tab)
            self._rebind_workspace_chrome()
            self._refresh_central_quote_code_supplier()
        except Exception:
            with suppress(AttributeError, TypeError, RuntimeError):
                self.tabs.currentChanged.disconnect(self._remember_last_active_tab)
            try:
                workspace.shutdown()
            except (AttributeError, OSError, RuntimeError, TypeError) as exc:
                log.warning(f"[UI] 清理失败的新工作区时 shutdown 失败: {exc}")
            self._tabs_wrapper_layout.removeWidget(workspace)
            delete_later = getattr(workspace, "deleteLater", None)
            if callable(delete_later):
                delete_later()
            self._workspace = existing_workspace
            self.tabs = previous_tabs
            try:
                self._rebind_workspace_chrome()
            except (AttributeError, RuntimeError, TypeError) as exc:
                log.warning(f"[UI] 恢复旧工作区标题栏导航失败: {exc}")
            if existing_tabs is not None:
                with suppress(AttributeError, TypeError, RuntimeError):
                    existing_tabs.currentChanged.connect(self._remember_last_active_tab)
            raise

        if existing_workspace is not None:
            try:
                existing_workspace.shutdown()
            except (AttributeError, OSError, RuntimeError, TypeError) as exc:
                log.error(f"[UI] 停止旧工作区失败: {exc}")
            self._tabs_wrapper_layout.removeWidget(existing_workspace)
            delete_later = getattr(existing_workspace, "deleteLater", None)
            if callable(delete_later):
                delete_later()
        return workspace

    def create_central_quotes_service(self, *, code_supplier=None):
        from ui.workers.central_quotes_worker import CentralQuotesService

        return CentralQuotesService(
            self,
            self.data_provider,
            code_supplier=code_supplier,
        )

    def _open_command_palette(self):
        if self._command_palette is None:
            from ui.components.command_palette import CommandPaletteDialog

            self._command_palette = CommandPaletteDialog(parent=self)
            self._command_palette.set_dynamic_provider(self._command_service.build_stock_commands)
        self._command_palette.set_commands(self._command_service.build_commands())
        self._command_palette.show()
        self._command_palette.raise_()
        self._command_palette.activateWindow()

    def _set_titlebar_sync_state(self, state: str, detail: str = "", freshness: str = ""):
        self._titlebar_sync_state = str(state or "").strip() or "idle"
        if freshness:
            self._last_sync_freshness = str(freshness or "").strip()

        sync_widget = getattr(self, "_titlebar_sync_widget", None)
        if sync_widget is not None:
            sync_widget.set_state(
                self._titlebar_sync_state,
                detail=detail,
                freshness=self._last_sync_freshness,
            )
        status_bar = getattr(self, "_status_bar_widget", None)
        if status_bar is not None and hasattr(status_bar, "show_sync_feedback"):
            status_bar.show_sync_feedback(self._titlebar_sync_state)

    def _apply_table_density(self, mode: str, persist: bool = True):
        from ui.main_window_visuals import apply_table_density

        apply_table_density(self, mode, persist=persist)

    def _tooltip_text_for_event(self, obj, event) -> str:
        if obj is None:
            return ""

        object_name = getattr(obj, "objectName", lambda: "")()
        if object_name == "floatingTooltip":
            return ""

        view = getattr(obj, "parent", lambda: None)()
        if isinstance(view, QAbstractItemView) and view.__class__.__name__ != "VCPTableView":
            index_at = getattr(view, "indexAt", None)
            if callable(index_at) and hasattr(event, "pos"):
                index = index_at(event.pos())
                if index.isValid():
                    tooltip_text = index.data(Qt.ItemDataRole.ToolTipRole)
                    if tooltip_text:
                        return str(tooltip_text).strip()

        tool_tip = getattr(obj, "toolTip", None)
        if callable(tool_tip):
            return str(tool_tip() or "").strip()
        return ""

    def eventFilter(self, obj, event):
        target_objects = (
            getattr(self, "btn_sys_menu", None),
            getattr(self, "_sys_menu", None),
            getattr(self, "_density_menu", None),
            getattr(self, "_theme_menu", None),
        )
        event_type = event.type()
        if event_type in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel,
            QEvent.Type.KeyPress,
        ):
            self._last_user_interaction_at = time.perf_counter()
        if event_type == QEvent.Type.ToolTip:
            tooltip_text = self._tooltip_text_for_event(obj, event)
            if tooltip_text and hasattr(event, "globalPos"):
                show_floating_tooltip(tooltip_text, event.globalPos(), owner=obj)
                return True
            hide_floating_tooltip()

        if event_type in (
            QEvent.Type.Leave,
            QEvent.Type.Hide,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.Wheel,
            QEvent.Type.WindowDeactivate,
        ):
            hide_floating_tooltip()

        if isinstance(obj, QAbstractButton) and event_type in (
            QEvent.Type.Enter,
            QEvent.Type.HoverEnter,
            QEvent.Type.HoverMove,
            QEvent.Type.MouseMove,
        ):
            obj.setCursor(Qt.CursorShape.PointingHandCursor if obj.isEnabled() else Qt.CursorShape.ArrowCursor)

        if obj in target_objects and event_type in (
            QEvent.Type.Enter,
            QEvent.Type.HoverEnter,
            QEvent.Type.HoverMove,
            QEvent.Type.MouseMove,
        ):
            with suppress(ImportError, RuntimeError):
                QApplication.restoreOverrideCursor()
            obj.setCursor(Qt.CursorShape.PointingHandCursor)
        return super().eventFilter(obj, event)

    def _show_trade_calendar(self):
        from ui.main_window_visuals import show_trade_calendar

        show_trade_calendar(self)

    def _open_runtime_health(self):
        from ui.components.runtime_health_dialog import RuntimeHealthDialog

        dialog = getattr(self, "_runtime_health_dialog", None)
        if dialog is None:
            dialog = RuntimeHealthDialog(self, parent=self)
            self._runtime_health_dialog = dialog
            dialog.destroyed.connect(lambda _obj=None: setattr(self, "_runtime_health_dialog", None))
        else:
            dialog.refresh()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    # =====================================================================
    # 自定义标题栏：品牌 + Tab 导航 + 窗口控制，合并成一行
    # =====================================================================
    def _init_custom_titlebar(self, parent_layout):
        """构建无边框窗口的自定义标题栏。"""
        refs = setup_custom_titlebar(self, parent_layout)
        self._custom_titlebar = refs.titlebar
        self._titlebar_layout = refs.layout
        self._titlebar_tab_placeholder = refs.placeholder
        self._market_pulse_strip = refs.pulse_strip
        self._btn_minimize = refs.btn_minimize
        self._btn_maximize = refs.btn_maximize
        self._btn_close = refs.btn_close

    def _inject_tabbar_into_titlebar(self):
        """在标题栏创建独立 TabBar 并与 QTabWidget 双向同步。"""
        self._standalone_tabbar = inject_standalone_tabbar(self)

    def _sync_maximize_button_icon(self):
        from ui.theme import theme_manager

        if not hasattr(self, "_btn_maximize") or self._btn_maximize is None:
            return
        tokens = build_ui_tokens(theme_manager.current_theme)
        icon_name = "restore" if self.isMaximized() else "maximize"
        set_button_svg_icon(
            self._btn_maximize,
            icon_name,
            tokens["icon"]["muted"],
            size=tokens["icon"]["chrome_size"],
            stroke_width=tokens["icon"]["stroke_width"],
        )

    def _toggle_maximize(self):
        """切换最大化/还原"""
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._sync_maximize_button_icon()

    def changeEvent(self, event):
        """窗口状态变化时同步最大化按钮图标"""
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            self._sync_maximize_button_icon()

    def _init_right_panel(self):
        # 不使用嵌套 QSplitter——大量 QTableView 子组件在嵌套 QSplitter 中
        # 触发 Qt6 底层 access violation (Windows fatal exception)
        self.tabs_wrapper = QFrame(self)
        self.tabs_wrapper.setObjectName("tabsWrapperFrame")
        from ui.theme import theme_manager as _twm

        _tw = _twm.current_theme
        self.tabs_wrapper.setStyleSheet(f"""
            QFrame#tabsWrapperFrame {{
                background-color: {_tw["BG_GLASS"]};
                border: none;
            }}
        """)
        self._tabs_wrapper_layout = QVBoxLayout(self.tabs_wrapper)
        self._tabs_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        self._tabs_wrapper_layout.setSpacing(0)

        event_bus.sig_rt_quotes.connect(self._on_rt_quotes_pulse)
        ui_signal_hub.sig_task_progress.connect(self._on_task_progress)
        ui_signal_hub.sig_show_kline.connect(self._on_show_kline)
        ui_signal_hub.sig_show_kline_with_list.connect(self._on_show_kline_with_list)

        self._bootstrap.mount_workspace()
        self._init_gear_menu()
        self._inject_tabbar_into_titlebar()
        return

    # _filter_table 已删除 — 各 Tab 已自行实现 proxy_model.setFilterText()，0 调用方

    # _on_table_double_click 已移除(#3)，各 Tab 自行通过 EventBus 广播 K 线请求

    # _show_context_menu 已移除(#2)，各 Tab 使用 stock_context_menu 工厂

    # _launch_tdx / _launch_eastmoney 已移除(#1)
    # 统一由 BaseStockTab 基类提供，避免双份代码维护噩梦

    def _remember_last_active_tab(self, index: int):
        _remember_active_tab_settings(self._app_config, getattr(self, "_workspace", None), index)

    def iter_workspace_tables(self):
        workspace = getattr(self, "_workspace", None)
        if workspace is None:
            return []
        iter_tables = getattr(workspace, "iter_tables", None)
        return list(iter_tables() or []) if callable(iter_tables) else []

    def install_workspace_table_copy_hooks(self, *, tables=None):
        if tables is not None:
            candidates = list(tables or [])
        else:
            candidates = []
            for table in self.iter_workspace_tables():
                try:
                    if table.window() is self:
                        candidates.append(table)
                except (AttributeError, RuntimeError, TypeError):
                    continue
        install_table_copy_hooks(candidates)

    def _save_ui_state(self):
        """Persist window geometry with version tag."""
        s = self._settings
        if self.tabs is not None:
            self._remember_last_active_tab(self.tabs.currentIndex())
        s.setValue("geometry", self.saveGeometry())
        s.setValue("geometry_version", 2)  # 无边框版本标记，防止旧缓存导致崩溃
        s.sync()

    def _restore_ui_state(self):
        """Restore window geometry. Frameless window needs special handling."""
        s = self._settings

        # 无边框改造后，旧的 geometry 缓存可能不兼容，需要版本化
        geometry_version = s.value("geometry_version", 0, type=int)
        geom_data = s.value("geometry")

        if geom_data and geometry_version >= 2:
            # 版本匹配，安全恢复
            try:
                self.restoreGeometry(geom_data)
                return
            except (AttributeError, RuntimeError, TypeError) as e:
                log.warning(f"[UI] restoreGeometry 失败，使用默认布局: {e}")

        # 版本不匹配或无缓存：使用自适应默认布局
        screen = QApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            w = int(avail.width() * 0.8)
            h = int(avail.height() * 0.7)
            self.resize(w, h)

            center = avail.center()
            geo = self.frameGeometry()
            geo.moveCenter(center)
            self.move(geo.topLeft())
        else:
            self.resize(1024, 768)

    # F5预计算 / 缓存加载 / 智能启动 / RPS缓存 / RT缓存
    # 已迁移至 core/rps_precomputer.py + core/startup_orchestrator.py

    def _update_last_f5_time(self):
        import datetime

        mtime = active_rps_cache_mtime(RPS_CACHE_FILE)
        if mtime > 0:
            dt = datetime.datetime.fromtimestamp(mtime)
            freshness = f"快照 {dt.strftime('%m-%d %H:%M')}"
            self._last_sync_freshness = freshness
            if hasattr(self, "act_f5"):
                self.act_f5.setText(f"全局同步 (F5) [{dt.strftime('%m-%d')}]")
            if self._titlebar_sync_state != "working":
                self._set_titlebar_sync_state("success", "可执行全局同步", freshness)
        else:
            self._last_sync_freshness = "暂无可用快照"
            if hasattr(self, "act_f5"):
                self.act_f5.setText("全局同步 (F5) [暂无]")
            if self._titlebar_sync_state != "working":
                self._set_titlebar_sync_state("idle", "等待首次同步", self._last_sync_freshness)

    def _on_f5_done(self, count, elapsed):
        """Handle the completion signal from the F5 precompute workflow."""
        from ui.main_window_runtime import finish_f5_reload

        finish_f5_reload(self, count=count, elapsed=elapsed, event_bus=event_bus)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "_process_watchdog"):
            self._process_watchdog.pulse("showEvent")

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._first_paint_recorded:
            return
        if not self.isVisible() or event.region().isEmpty():
            return
        self._first_paint_recorded = True
        from core.observability import emit_structured_log, record_metric

        elapsed_ms = (time.perf_counter() - self._launch_started_at) * 1000.0
        self._first_paint_elapsed_ms = elapsed_ms
        record_metric("main_window_first_paint_ms", elapsed_ms, unit="ms")
        emit_structured_log(
            "main_window.first_paint",
            elapsed_ms=round(elapsed_ms, 3),
            workspace_mode=str(getattr(getattr(self, "_workspace", None), "mode", "unknown")),
        )
        self._schedule_post_paint_runtime()

    def closeEvent(self, event):
        """应用关闭：广播信号让各组件自行保存，然后清理资源"""
        from ui.main_window_runtime import shutdown_main_window

        self._post_paint_runtime_timer.stop()
        _disconnect_main_window_runtime_signals(self)

        if self._app_cursor_filter_installed:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            self._app_cursor_filter_installed = False

        shutdown_main_window(self, event_bus=event_bus, task_manager=task_manager)
        if hasattr(self, "_process_watchdog"):
            self._process_watchdog.stop()

        super().closeEvent(event)

    def _is_launch_at_login_supported(self) -> bool:
        from app.services.ui_autostart_service import is_launch_at_login_supported

        return is_launch_at_login_supported(self._project_root)

    def _is_launch_at_login_enabled(self) -> bool:
        from app.services.ui_autostart_service import AutoStartError, is_launch_at_login_enabled

        try:
            return is_launch_at_login_enabled(self._project_root)
        except (AutoStartError, OSError, RuntimeError) as exc:
            log.warning(f"[UI] launch-at-login state probe failed: {exc}")
            return False

    def _set_launch_at_login_action_checked(self, checked: bool) -> None:
        action = getattr(self, "_act_launch_at_login", None)
        if action is None:
            return
        previous = action.blockSignals(True)
        try:
            action.setChecked(bool(checked))
        finally:
            action.blockSignals(previous)

    def _toggle_launch_at_login(self, checked: bool) -> None:
        from app.services.ui_autostart_service import AutoStartError, set_launch_at_login_enabled
        from ui.components.toast_widget import show_toast

        target = bool(checked)
        try:
            set_launch_at_login_enabled(target, self._project_root)
        except (AutoStartError, OSError, RuntimeError) as exc:
            log.warning(f"[UI] launch-at-login toggle failed: {exc}")
            self._set_launch_at_login_action_checked(self._is_launch_at_login_enabled())
            show_toast(
                "\u5f00\u673a\u81ea\u542f\u52a8\u8bbe\u7f6e\u5931\u8d25\uff1a" + str(exc),
                "error",
                self,
                duration=3500,
            )
            return

        self._set_launch_at_login_action_checked(target)
        message = (
            "\u5df2\u5f00\u542f\u5f00\u673a\u81ea\u542f\u52a8"
            if target
            else "\u5df2\u5173\u95ed\u5f00\u673a\u81ea\u542f\u52a8"
        )
        show_toast(message, "success", self, duration=2500)

    def _action_refresh_f5(self):
        """F5 盘后预计算界面触发层"""
        if not _f5_runtime_dependencies_ready(self):
            self._pending_f5_request = True
            if hasattr(self, "lbl_status"):
                self.lbl_status.setText("数据服务初始化中，完成后继续全局同步...")
            self._set_titlebar_sync_state("cache", "数据服务初始化中，完成后继续同步")
            return
        from PyQt6.QtWidgets import QMessageBox

        from ui.components.message_box import show_themed_question

        reply = show_themed_question(
            self,
            "盘后一键预计算",
            "此操作将执行完整的盘后数据重建流程：\n\n"
            "① 从通达信本地日线(vipdoc)重新读取数据\n"
            "② 预计算全市场RPS排名(120日/250日)\n"
            "③ 预计算板块RPS排名\n"
            "④ 保存缓存供后续扫描与复盘使用\n\n"
            "请确保已在通达信中完成【盘后数据下载】.\n是否执行?",
            yes_text="执行",
            no_text="取消",
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        if hasattr(self, "lbl_status"):
            self.lbl_status.setText("F5 盘后预计算进行中...")
        self._set_titlebar_sync_state("working", "全局同步进行中")
        self._f5_cancelled = False
        from ui.main_window_runtime import start_f5_precompute

        start_f5_precompute(self)

    # =======================================================================
    # 右键菜单委托方法
    # =======================================================================
    # _toggle_special 已删除 — 关注池操作已统一由 watchlist_vm.toggle_stock() 处理，0 调用方

    # _export_current_tab 已删除 — 无菜单/快捷键指向它，各 Tab 自带独立导出按钮

    # =======================================================================
    # [Global Event Bus] 信号中转站
    # =======================================================================
    @pyqtSlot(object)
    def _on_rt_quotes_pulse(self, payload: object):
        """Blink the titlebar quotes heartbeat when fresh quote payloads arrive."""
        if not payload:
            return
        _update_titlebar_quote_status(getattr(self, "_titlebar_sync_widget", None), payload)

    @pyqtSlot(str, int, str)
    def _on_task_progress(self, module: str, pct: int, msg: str):
        """处理扫描进度更新"""
        if module == "scan":
            if hasattr(self, "progress_bar"):
                self.progress_bar.setValue(pct)
            if hasattr(self, "lbl_status"):
                self.lbl_status.setText(msg)

    # ================================================================
    # EventBus 信号处理（各 Tab 组件广播的信号）
    # ================================================================
    def _on_show_kline(self, code: str):
        """响应简单K线图请求（无上下文列表）"""
        self._on_show_kline_with_list(code, [], 0)

    def _on_show_kline_with_list(self, code: str, code_list: list, current_idx: int):
        """响应带列表上下文的 K 线图请求 — 委托给 KLineWindowManager (#1)"""
        from app.services.kline_open_service import build_kline_open_context

        workspace = getattr(self, "_workspace", None)
        source_tab_index = self.tabs.currentIndex() if self.tabs is not None else -1
        source_tab_key = ""
        if workspace is not None and hasattr(workspace, "tab_specs"):
            tab_specs = workspace.tab_specs()
            if 0 <= source_tab_index < len(tab_specs):
                source_tab_key = str(tab_specs[source_tab_index].get("key") or "").strip()

        open_context = build_kline_open_context(
            code=code,
            code_name_map=getattr(self.data_provider, "code2name", {}),
            code_list=code_list,
            current_idx=current_idx,
            workspace=workspace,
            source_tab_index=source_tab_index,
            source_tab_key=source_tab_key,
        )

        kline_manager.open_chart(
            main_window=self,
            code=open_context.code,
            name=open_context.name,
            data_provider=self.data_provider,
            vcp_data=None,
            code_list=None,
            current_idx=open_context.current_idx,
            open_context=open_context,
        )

    # ================================================================
    # 主题切换系统
    # ================================================================
    def _apply_theme(self, _theme_name: str = ""):
        from ui.main_window_visuals import apply_theme

        apply_theme(self, notify=self.isVisible())
