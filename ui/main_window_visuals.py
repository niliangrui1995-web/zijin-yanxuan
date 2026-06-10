from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
)

from app.services.ui_earnings_calendar_service import (
    GlobalEarningsCalendarService,
    events_by_date,
)
from app.services.ui_event_service import domain_events
from ui.components.main_window_shell import apply_chrome_theme
from ui.components.shared_title_bar import DraggableTitleBar
from ui.components.tooltip_popup import hide_floating_tooltip
from ui.components.trade_calendar import OligarchEarningsCalendarPanel, TradeCalendarWidget
from ui.styles.global_qss import generate_global_qss, invalidate_global_qss_cache
from ui.theme import theme_manager
from ui.theme_tokens import build_ui_tokens


def apply_table_density(main_window, mode: str, persist: bool = True):
    from app.services.ui_config_service import app_config
    from ui.components import VCPTableView
    from ui.models.table_model_helpers import invalidate_table_token_cache

    if mode not in ("紧凑", "舒适"):
        mode = "舒适"
    invalidate_table_token_cache(mode)
    invalidate_global_qss_cache()

    if persist:
        app_config.table_density = mode
        app_config.sync()

    if hasattr(main_window, "_act_density_compact"):
        main_window._act_density_compact.setChecked(mode == "紧凑")
    if hasattr(main_window, "_act_density_comfort"):
        main_window._act_density_comfort.setChecked(mode == "舒适")

    app = QApplication.instance()
    if app is not None:
        for widget in app.allWidgets():
            if isinstance(widget, VCPTableView):
                widget.apply_density(mode)

        qss = generate_global_qss(density=mode)
        main_window.setStyleSheet(qss)
        app.setStyleSheet(qss)

    apply_chrome_theme(main_window)
    if hasattr(main_window, "_status_bar_widget") and main_window._status_bar_widget:
        main_window._status_bar_widget.apply_theme()


def show_trade_calendar(main_window):
    tokens = build_ui_tokens(theme_manager.current_theme)
    is_dark = tokens["is_dark"]
    shell = tokens["shell"]
    service = GlobalEarningsCalendarService()
    earnings_events = service.load_events()

    dlg = QDialog(main_window)
    dlg.setObjectName("tradeCalendarDialog")
    dlg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
    dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    dlg.resize(900, 560)

    main_layout = QVBoxLayout(dlg)
    main_layout.setContentsMargins(14, 14, 14, 14)
    main_layout.setSpacing(0)

    container = QFrame(dlg)
    container.setObjectName("dialogContainer")
    shadow = QGraphicsDropShadowEffect(container)
    shadow.setBlurRadius(shell["window_shadow_blur"] if is_dark else 28)
    shadow.setOffset(0, shell["window_shadow_offset_y"] if is_dark else 10)
    shadow.setColor(QColor(0, 0, 0, int(255 * shell["window_shadow_alpha"]) if is_dark else 52))
    container.setGraphicsEffect(shadow)
    container_layout = QVBoxLayout(container)
    container_layout.setContentsMargins(1, 1, 1, 14)
    container_layout.setSpacing(0)

    title_bar = DraggableTitleBar(dlg)
    title_bar.setObjectName("dialogTitleBar")
    title_bar.setFixedHeight(shell["titlebar_height"])
    tb_layout = QHBoxLayout(title_bar)
    tb_layout.setContentsMargins(18, 0, 10, 0)
    tb_layout.setSpacing(0)

    title_lbl = QLabel("A\u80a1\u4ea4\u6613\u65e5\u5386 \u00b7 \u5be1\u5934\u8d22\u62a5")
    title_lbl.setObjectName("dialogWindowTitle")
    tb_layout.addWidget(title_lbl)
    tb_layout.addStretch()

    btn_close = QToolButton(title_bar)
    btn_close.setObjectName("dialogCloseButton")
    btn_close.setText("\u2715")
    btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_close.setFixedSize(34, 34)
    btn_close.clicked.connect(dlg.reject)
    tb_layout.addWidget(btn_close)
    container_layout.addWidget(title_bar)

    body = QFrame(container)
    body.setObjectName("tradeCalendarBody")
    body_layout = QHBoxLayout(body)
    body_layout.setContentsMargins(18, 18, 18, 18)
    body_layout.setSpacing(12)

    calendar = TradeCalendarWidget(body, earnings_events=events_by_date(earnings_events))
    calendar.setObjectName("tradeCalendarWidget")
    body_layout.addWidget(calendar, 2)

    earnings_panel = OligarchEarningsCalendarPanel(body, events=earnings_events, service=service)
    body_layout.addWidget(earnings_panel, 1)
    earnings_panel.eventsChanged.connect(calendar.set_earnings_events)
    calendar.clicked.connect(lambda date: earnings_panel.set_selected_date(date.toString("yyyy-MM-dd")))
    domain_events.sig_earnings_updated.connect(earnings_panel.reload_from_service_cache)

    def _apply_dialog_live_theme(_theme_name: str | None = None) -> None:
        live_tokens = build_ui_tokens(theme_manager.current_theme)
        live_dark = live_tokens["is_dark"]
        shell_tokens = live_tokens["shell"]
        shadow.setBlurRadius(shell_tokens["window_shadow_blur"] if live_dark else 28)
        shadow.setOffset(0, shell_tokens["window_shadow_offset_y"] if live_dark else 10)
        shadow.setColor(QColor(0, 0, 0, int(255 * shell_tokens["window_shadow_alpha"]) if live_dark else 52))
        for widget in (dlg, container, title_bar, body, calendar, earnings_panel):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    def _cleanup_dialog(_result) -> None:
        for widget in (earnings_panel, calendar):
            dispose = getattr(widget, "_dispose", None)
            if dispose is not None:
                try:
                    dispose()
                except (RuntimeError, TypeError):
                    pass
        try:
            domain_events.sig_earnings_updated.disconnect(earnings_panel.reload_from_service_cache)
        except (RuntimeError, TypeError):
            pass
        try:
            theme_manager.sig_theme_changed.disconnect(_apply_dialog_live_theme)
        except (RuntimeError, TypeError):
            pass

    theme_manager.sig_theme_changed.connect(_apply_dialog_live_theme)
    dlg.finished.connect(_cleanup_dialog)
    _apply_dialog_live_theme()
    QTimer.singleShot(0, earnings_panel.refresh_from_service)

    content_layout = QVBoxLayout()
    content_layout.setContentsMargins(18, 10, 18, 4)
    content_layout.setSpacing(0)
    content_layout.addWidget(body)

    container_layout.addLayout(content_layout)
    main_layout.addWidget(container)
    try:
        dlg.exec()
    finally:
        _cleanup_dialog(None)
        dlg.deleteLater()


def apply_theme(main_window, *, notify: bool = True):
    from ui.models.table_model_helpers import invalidate_table_token_cache

    invalidate_table_token_cache()
    invalidate_global_qss_cache()
    qss = generate_global_qss()
    main_window.setStyleSheet(qss)

    app = QApplication.instance()
    if app is not None:
        app.setStyleSheet(qss)
        hide_floating_tooltip()

    apply_chrome_theme(main_window)
    if hasattr(main_window, "_status_bar_widget") and main_window._status_bar_widget:
        main_window._status_bar_widget.apply_theme()

    for widget in (
        main_window,
        getattr(main_window, "_custom_titlebar", None),
        getattr(main_window, "_status_bar_widget", None),
        getattr(main_window, "_standalone_tabbar", None),
        getattr(main_window, "_workspace", None),
        getattr(main_window, "tabs_wrapper", None),
        getattr(main_window, "btn_sys_menu", None),
        getattr(getattr(main_window, "_workspace", None), "detail_drawer", None),
    ):
        if widget:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    if notify and main_window.isVisible():
        from ui.components.toast_widget import show_toast

        show_toast(
            f"已切换至「{theme_manager.current_theme_name}」主题",
            "success",
            main_window,
            duration=2000,
        )
