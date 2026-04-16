from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QToolButton, QToolTip, QVBoxLayout

from ui.components.main_window_shell import DraggableTitleBar, apply_chrome_theme
from ui.components.trade_calendar import TradeCalendarWidget
from ui.styles.global_qss import generate_global_qss
from ui.theme import theme_manager


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
    dlg = QDialog(main_window)
    dlg.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
    dlg.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    dlg.resize(400, 360)

    main_layout = QVBoxLayout(dlg)
    main_layout.setContentsMargins(0, 0, 0, 0)

    container = QFrame()
    container.setObjectName("dialogContainer")
    container.setStyleSheet(f"""
        QFrame#dialogContainer {{
            background-color: {theme_manager.get('BG_BASE')};
            border: 1px solid {theme_manager.get('BORDER_DEFAULT')};
            border-radius: 8px;
        }}
    """)
    container_layout = QVBoxLayout(container)
    container_layout.setContentsMargins(1, 1, 1, 14)
    container_layout.setSpacing(0)

    title_bar = DraggableTitleBar(dlg)
    title_bar.setObjectName("calendarTitleBar")
    title_bar.setFixedHeight(38)
    title_bar.setStyleSheet(f"""
        QWidget#calendarTitleBar {{
            background-color: {theme_manager.get('BG_TITLEBAR')};
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
            border-bottom: 1px solid {theme_manager.get('TITLEBAR_BORDER')};
        }}
    """)
    tb_layout = QHBoxLayout(title_bar)
    tb_layout.setContentsMargins(14, 0, 8, 0)

    title_lbl = QLabel("A股交易休市日历")
    title_lbl.setStyleSheet(
        f"color: {theme_manager.get('TEXT_PRIMARY')}; font-weight: bold; background: transparent;"
    )
    tb_layout.addWidget(title_lbl)
    tb_layout.addStretch()

    btn_close = QToolButton()
    btn_close.setText("✕")
    btn_close.setFixedSize(32, 28)
    btn_close.clicked.connect(dlg.reject)
    btn_close.setStyleSheet(f"""
        QToolButton {{
            background: transparent;
            border: none;
            color: {theme_manager.get('TEXT_MUTED')};
        }}
        QToolButton:hover {{
            background-color: #E81123;
            color: white;
            border-radius: 4px;
        }}
    """)
    tb_layout.addWidget(btn_close)
    container_layout.addWidget(title_bar)

    content_layout = QVBoxLayout()
    content_layout.setContentsMargins(14, 14, 14, 0)
    content_layout.addWidget(TradeCalendarWidget())

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
