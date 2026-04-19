from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QToolButton, QToolTip, QVBoxLayout

from ui.components.main_window_shell import DraggableTitleBar, apply_chrome_theme
from ui.components.trade_calendar import TradeCalendarWidget
from ui.styles.global_qss import generate_global_qss
from ui.theme import theme_manager
from ui.theme_tokens import build_ui_tokens


def apply_table_density(main_window, mode: str, persist: bool = True):
    from core.app_config import app_config
    from ui.components import VCPTableView

    if mode not in ("紧凑", "舒适"):
        mode = "舒适"

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
    shell = tokens["shell"]

    dlg = QDialog(main_window)
    dlg.setObjectName("tradeCalendarDialog")
    dlg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
    dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    dlg.resize(400, 360)

    main_layout = QVBoxLayout(dlg)
    main_layout.setContentsMargins(0, 0, 0, 0)

    container = QFrame(dlg)
    container.setObjectName("dialogContainer")
    container_layout = QVBoxLayout(container)
    container_layout.setContentsMargins(1, 1, 1, 18)
    container_layout.setSpacing(0)

    title_bar = DraggableTitleBar(dlg)
    title_bar.setObjectName("dialogTitleBar")
    title_bar.setFixedHeight(shell["titlebar_height"])
    tb_layout = QHBoxLayout(title_bar)
    tb_layout.setContentsMargins(14, 0, 8, 0)
    tb_layout.setSpacing(0)

    title_lbl = QLabel("A股交易休市日历")
    title_lbl.setObjectName("dialogWindowTitle")
    tb_layout.addWidget(title_lbl)
    tb_layout.addStretch()

    btn_close = QToolButton(title_bar)
    btn_close.setObjectName("dialogCloseButton")
    btn_close.setText("✕")
    btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_close.setFixedSize(36, shell["titlebar_height"])
    btn_close.clicked.connect(dlg.reject)
    tb_layout.addWidget(btn_close)
    container_layout.addWidget(title_bar)

    body = QFrame(container)
    body.setObjectName("tradeCalendarBody")
    body_layout = QVBoxLayout(body)
    body_layout.setContentsMargins(14, 14, 14, 14)
    body_layout.setSpacing(0)

    calendar = TradeCalendarWidget(body)
    calendar.setObjectName("tradeCalendarWidget")
    body_layout.addWidget(calendar)

    content_layout = QVBoxLayout()
    content_layout.setContentsMargins(18, 16, 18, 0)
    content_layout.setSpacing(0)
    content_layout.addWidget(body)

    container_layout.addLayout(content_layout)
    main_layout.addWidget(container)
    dlg.exec()


def apply_theme(main_window):
    t = theme_manager.current_theme
    qss = generate_global_qss()
    main_window.setStyleSheet(qss)

    app = QApplication.instance()
    if app is not None:
        pal = app.style().standardPalette()
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            pal.setColor(group, QPalette.ColorRole.ToolTipBase, QColor(t['BG_ELEVATED']))
            pal.setColor(group, QPalette.ColorRole.ToolTipText, QColor(t['TEXT_PRIMARY']))
        app.setPalette(pal)
        app.setStyleSheet(qss)
        QToolTip.hideText()
        QToolTip.setPalette(pal)

    apply_chrome_theme(main_window)
    if hasattr(main_window, '_status_bar_widget') and main_window._status_bar_widget:
        main_window._status_bar_widget.apply_theme()

    for widget in (
        main_window,
        getattr(main_window, '_custom_titlebar', None),
        getattr(main_window, '_status_bar_widget', None),
        getattr(main_window, '_standalone_tabbar', None),
        getattr(main_window, '_workspace', None),
        getattr(main_window, 'tabs_wrapper', None),
        getattr(main_window, 'btn_sys_menu', None),
        getattr(getattr(main_window, '_workspace', None), 'detail_drawer', None),
    ):
        if widget:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    from ui.components.toast_widget import show_toast

    show_toast(
        f"已切换至「{theme_manager.current_theme_name}」主题",
        "success",
        main_window,
        duration=2000,
    )
