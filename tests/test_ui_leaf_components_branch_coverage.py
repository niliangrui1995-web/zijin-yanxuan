from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QDate, QItemSelectionModel, QObject, QPoint, QPointF, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QKeyEvent, QPixmap, QStandardItem, QStandardItemModel
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QDialog, QTableView, QWidget

from ui import main_window_tables, main_window_visuals
from ui.components import notification_service
from ui.components.command_palette import CommandPaletteDialog
from ui.components.runtime_health_dialog import RuntimeHealthDialog
from ui.components.shared_title_bar import DraggableTitleBar
from ui.components.toggle_switch import ToggleSwitch
from ui.workspaces.workspace_navigation_service import WorkspaceNavigationService


@pytest.fixture(autouse=True)
def _restore_qapplication_state(qt_application):
    previous_style = qt_application.styleSheet()
    previous_icon = QIcon(qt_application.windowIcon())
    previous_clipboard = QApplication.clipboard().text()
    previous_tray = getattr(qt_application, notification_service._TRAY_ATTR, None)
    yield
    current_tray = getattr(qt_application, notification_service._TRAY_ATTR, None)
    if current_tray is not None and current_tray is not previous_tray:
        current_tray.hide()
        current_tray.deleteLater()
        delattr(qt_application, notification_service._TRAY_ATTR)
    if previous_tray is not None:
        setattr(qt_application, notification_service._TRAY_ATTR, previous_tray)
    qt_application.setStyleSheet(previous_style)
    qt_application.setWindowIcon(previous_icon)
    QApplication.clipboard().setText(previous_clipboard)


class _Action:
    def __init__(self):
        self.checked = None

    def setChecked(self, checked):
        self.checked = bool(checked)


class _ThemeWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.theme_calls = 0

    def apply_theme(self):
        self.theme_calls += 1


class _MouseEvent:
    def __init__(
        self,
        *,
        button=Qt.MouseButton.LeftButton,
        buttons=Qt.MouseButton.LeftButton,
        global_pos=(0.0, 0.0),
        local_pos=(0.0, 0.0),
    ):
        self._button = button
        self._buttons = buttons
        self._global_pos = QPointF(*global_pos)
        self._local_pos = QPointF(*local_pos)
        self.accepted = False

    def button(self):
        return self._button

    def buttons(self):
        return self._buttons

    def globalPosition(self):
        return self._global_pos

    def position(self):
        return self._local_pos

    def accept(self):
        self.accepted = True


class _FakeWindow:
    def __init__(self, *, maximized=False):
        self.maximized = maximized
        self.moves = []
        self.normal_calls = 0
        self.maximized_calls = 0

    def frameGeometry(self):
        return QRect(20, 30, 400, 300)

    def isMaximized(self):
        return self.maximized

    def width(self):
        return 400

    def showNormal(self):
        self.normal_calls += 1
        self.maximized = False

    def showMaximized(self):
        self.maximized_calls += 1
        self.maximized = True

    def move(self, *args):
        if len(args) == 1:
            point = args[0]
            self.moves.append((point.x(), point.y()))
        else:
            self.moves.append((int(args[0]), int(args[1])))


class _SelectableTab:
    def __init__(self, *, code_result=False, row_result=False):
        self.code_result = code_result
        self.row_result = row_result
        self.codes = []
        self.rows = []

    def select_code_row(self, code):
        self.codes.append(code)
        return self.code_result

    def select_primary_row(self, index):
        self.rows.append(index)
        return self.row_result


class _Tabs:
    def __init__(self, widgets, current=0):
        self.widgets = list(widgets)
        self.current = current
        self.changes = []

    def currentIndex(self):
        return self.current

    def count(self):
        return len(self.widgets)

    def widget(self, index):
        return self.widgets[index]

    def setCurrentIndex(self, index):
        self.current = index
        self.changes.append(index)


def test_command_palette_normalizes_filters_deduplicates_and_executes(qt_application):
    calls = []
    dialog = CommandPaletteDialog(
        [
            {"title": "  ", "handler": lambda: calls.append("blank")},
            {
                "title": " 全局同步 ",
                "subtitle": " 刷新缓存 ",
                "keywords": [" F5 ", "", None],
                "shortcut": " Ctrl+R ",
                "handler": lambda: calls.append("sync"),
            },
            {"title": "关注池", "keywords": ["watchlist"]},
        ]
    )

    assert [item["title"] for item in dialog._commands] == ["全局同步", "关注池"]
    assert dialog._commands[0]["keywords"] == ["f5"]
    assert dialog.list_widget.count() == 2
    assert dialog._matches_query(dialog._commands[0], "刷新") is True
    assert dialog._matches_query(dialog._commands[0], "f5") is True
    assert dialog._matches_query(dialog._commands[0], "不存在") is False
    assert dialog._matches_query(dialog._commands[0], "") is True

    provider_queries = []

    def provider(query):
        provider_queries.append(query)
        return [
            dict(dialog._commands[0]),
            {"title": "600519 贵州茅台", "subtitle": "打开 K 线", "shortcut": "Enter", "handler": None},
        ]

    dialog.set_dynamic_provider(provider)
    assert provider_queries == []
    dialog.search_box.setText("600519")
    qt_application.processEvents()

    assert provider_queries == ["600519"]
    assert dialog.list_widget.count() == 1
    item = dialog.list_widget.item(0)
    assert item.text() == "600519 贵州茅台\n打开 K 线    Enter"
    assert item.toolTip() == "600519 贵州茅台｜打开 K 线｜Enter"

    accepted = QSignalSpy(dialog.accepted)
    dialog._trigger_current()
    assert len(accepted) == 1
    assert calls == []

    dialog.set_dynamic_provider(None)
    dialog.search_box.clear()
    dialog.set_commands([{"title": "执行", "handler": lambda: calls.append("done")}])
    dialog._trigger_current()
    assert calls == ["done"]

    dialog.set_commands([])
    dialog._trigger_current()
    assert dialog.list_widget.currentItem() is None
    dialog.deleteLater()


def test_command_palette_show_event_centers_and_selects_search_text(qt_application):
    parent = QWidget()
    parent.setGeometry(120, 80, 900, 640)
    parent.show()
    dialog = CommandPaletteDialog([{"title": "命令"}], parent)
    dialog.search_box.setText("命令")

    dialog.show()
    qt_application.processEvents()

    delta = dialog.frameGeometry().center() - parent.geometry().center()
    assert abs(delta.x()) <= 1
    assert abs(delta.y()) <= 1
    assert dialog.search_box.selectedText() == "命令"
    assert dialog.search_box.hasFocus()
    dialog.close()
    parent.close()

    standalone = CommandPaletteDialog([{"title": "独立命令"}])
    standalone.search_box.setText("独立命令")
    standalone.show()
    qt_application.processEvents()
    assert standalone.search_box.selectedText() == "独立命令"
    standalone.close()


def test_runtime_health_dialog_refresh_export_and_close(monkeypatch, qt_application):
    import ui.components.runtime_health_dialog as runtime_health_dialog

    assert runtime_health_dialog._quote_health_summary(
        {"recent_batch_count": 4, "recent_cache_hit_count": 3}
    ) == "行情批次 4"
    reports = [
        {
            "background_tasks": {"count": 2},
            "timers": {"active": 3, "total": 5},
            "event_bus": {"total_receivers": 7},
            "process": {"thread_count": 11},
            "webengine": {"count": 1},
            "quotes": {
                "request_stats": {
                    "recent_batch_count": 4,
                    "recent_network_result_count": 20,
                    "recent_cache_hit_count": 3,
                    "recent_stale_result_count": 5,
                    "recent_latest_quote_time": "2026-07-22T10:24:06+08:00",
                }
            },
            "f5_cache": {"trade_date": "2026-07-14"},
            "中文": "正常",
        },
        {"background_tasks": {"count": 9}},
        {},
    ]
    collected = []
    exported = []

    def collect(main_window, **_kwargs):
        collected.append(main_window)
        return reports[min(len(collected) - 1, len(reports) - 1)]

    def export(main_window, *, project_root, report):
        exported.append((main_window, project_root, report))
        return "tmp/runtime-health.json"

    monkeypatch.setattr(runtime_health_dialog, "collect_runtime_health", collect)
    monkeypatch.setattr(runtime_health_dialog, "export_runtime_health_report", export)
    main_window = QWidget()
    main_window._project_root = "D:/project"

    dialog = RuntimeHealthDialog(main_window)
    assert "任务 2" in dialog.summary_label.text()
    assert "Timer 3/5" in dialog.summary_label.text()
    assert "行情 10:24:06" in dialog.summary_label.text()
    assert "联网 20/缓存 3/过期 5" in dialog.summary_label.text()
    assert "F5 2026-07-14" in dialog.summary_label.text()
    assert '"中文": "正常"' in dialog.report_edit.toPlainText()
    assert dialog.report_edit.isReadOnly() is True

    QTest.mouseClick(dialog.btn_refresh, Qt.MouseButton.LeftButton)
    assert dialog._last_report == reports[1]
    assert dialog.summary_label.text().startswith("任务 9 | Timer 0/0")

    QTest.mouseClick(dialog.btn_export, Qt.MouseButton.LeftButton)
    assert exported[-1] == (main_window, "D:/project", reports[1])
    assert dialog.summary_label.text().endswith("已导出 tmp/runtime-health.json")

    dialog._last_report = None
    QTest.mouseClick(dialog.btn_export, Qt.MouseButton.LeftButton)
    assert exported[-1][2] == {}
    assert "线程 -" in dialog.summary_label.text()
    assert "F5 暂无" in dialog.summary_label.text()

    accepted = QSignalSpy(dialog.accepted)
    QTest.mouseClick(dialog.btn_close, Qt.MouseButton.LeftButton)
    assert len(accepted) == 1
    dialog.deleteLater()
    main_window.deleteLater()


def test_toggle_switch_real_click_animation_size_and_paint(qt_application):
    toggle = ToggleSwitch("盘中提醒")
    toggle.resize(toggle.sizeHint())
    toggle.show()
    toggled = QSignalSpy(toggle.toggled)

    QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert toggle.isChecked() is True
    assert len(toggled) == 1
    assert float(toggle._animation.endValue()) == 1.0
    assert toggle.minimumSizeHint() == toggle.sizeHint()
    assert toggle.sizeHint().width() > 42

    toggle._animation.stop()
    toggle._on_animation_value(0.35)
    assert toggle._position == 0.35
    checked_pixmap = toggle.grab()
    assert checked_pixmap.isNull() is False
    assert checked_pixmap.width() == toggle.width()

    toggle.setEnabled(False)
    disabled_pixmap = toggle.grab()
    assert disabled_pixmap.isNull() is False

    compact = ToggleSwitch()
    compact.resize(compact.sizeHint())
    compact.show()
    compact._animate_to_state(False)
    compact._animation.stop()
    compact._position = 0.0
    unchecked_pixmap = compact.grab()
    assert compact.sizeHint().width() == 42
    assert unchecked_pixmap.isNull() is False
    compact.close()
    toggle.close()


def test_notify_breakout_uses_tray_fallback_and_logs_failures(monkeypatch):
    sent = []
    sounds = []
    logs = []
    monkeypatch.setattr(notification_service, "_send_tray_notification", lambda title, message: sent.append((title, message)))
    monkeypatch.setattr(notification_service, "_play_alert_sound", lambda: sounds.append("played"))

    notification_service.notify_breakout("600519", "贵州茅台", "突破", sound=True)

    assert sent == [("🔔 VCP 突破信号", "贵州茅台(600519)\n突破")]
    assert sounds == ["played"]

    monkeypatch.setattr(notification_service, "_send_tray_notification", lambda *_args: (_ for _ in ()).throw(OSError("tray")))
    monkeypatch.setattr(notification_service, "_send_windows_toast", lambda title, message: sent.append(("toast", title, message)))
    notification_service.notify_breakout("000001", "平安银行", "观察", sound=False)
    assert sent[-1] == ("toast", "🔔 VCP 突破信号", "平安银行(000001)\n观察")
    assert sounds == ["played"]

    monkeypatch.setattr(notification_service, "_send_windows_toast", lambda *_args: (_ for _ in ()).throw(RuntimeError("toast")))
    monkeypatch.setattr(notification_service, "_play_alert_sound", lambda: (_ for _ in ()).throw(RuntimeError("sound")))
    monkeypatch.setattr(notification_service, "log", SimpleNamespace(debug=logs.append))
    notification_service.notify_breakout("300750", "宁德时代", "失败", sound=True)
    assert "tray/toast 均发送失败" in logs[0]
    assert "提示音播放失败" in logs[1]


def test_notification_tray_is_reused_and_send_uses_expected_payload(qt_application, monkeypatch):
    if hasattr(qt_application, notification_service._TRAY_ATTR):
        old_tray = getattr(qt_application, notification_service._TRAY_ATTR)
        old_tray.hide()
        delattr(qt_application, notification_service._TRAY_ATTR)

    icon_pixmap = QPixmap(8, 8)
    icon_pixmap.fill(Qt.GlobalColor.red)
    qt_application.setWindowIcon(QIcon(icon_pixmap))
    tray = notification_service._get_or_create_tray_icon(qt_application)
    assert tray is notification_service._get_or_create_tray_icon(qt_application)
    assert tray.icon().isNull() is False

    tray.hide()
    delattr(qt_application, notification_service._TRAY_ATTR)
    tray.deleteLater()

    qt_application.setWindowIcon(QIcon())
    iconless_tray = notification_service._get_or_create_tray_icon(qt_application)
    assert iconless_tray.icon().isNull() is True
    iconless_tray.hide()
    delattr(qt_application, notification_service._TRAY_ATTR)
    iconless_tray.deleteLater()

    shown = []
    fake_tray = SimpleNamespace(showMessage=lambda *args: shown.append(args))
    monkeypatch.setattr(notification_service, "_get_or_create_tray_icon", lambda app: fake_tray)
    notification_service._send_tray_notification("标题", "内容")
    assert shown[0][0:2] == ("标题", "内容")
    assert shown[0][3] == 5000


def test_notification_tray_tolerates_parent_without_window_icon(qt_application):
    parent = QObject()
    tray = notification_service._get_or_create_tray_icon(parent)
    assert getattr(parent, notification_service._TRAY_ATTR) is tray
    assert notification_service._get_or_create_tray_icon(parent) is tray
    tray.hide()
    tray.deleteLater()
    parent.deleteLater()


def test_tray_notification_returns_when_no_qapplication(monkeypatch):
    import PyQt6.QtWidgets as qt_widgets

    monkeypatch.setattr(qt_widgets, "QApplication", SimpleNamespace(instance=lambda: None))
    notification_service._send_tray_notification("标题", "内容")


def test_windows_toast_and_alert_sound_platform_boundaries(monkeypatch):
    calls = []
    monkeypatch.setattr(notification_service, "run_process", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(notification_service, "windows_no_window_kwargs", lambda: {"creationflags": 77})

    monkeypatch.setattr(notification_service, "os", SimpleNamespace(name="posix"))
    notification_service._send_windows_toast("标题", "内容")
    notification_service._play_alert_sound()
    assert calls == []

    beeps = []
    fake_winsound = SimpleNamespace(MB_ICONASTERISK=64, MessageBeep=beeps.append)
    monkeypatch.setitem(sys.modules, "winsound", fake_winsound)
    monkeypatch.setattr(notification_service, "os", SimpleNamespace(name="nt"))
    notification_service._send_windows_toast("标题", "内容")
    notification_service._play_alert_sound()

    command, kwargs = calls[0]
    assert command[0][0:4] == ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
    assert command[0][-2:] == ["标题", "内容"]
    assert kwargs == {"capture_output": True, "timeout": 5, "creationflags": 77, "check": False}
    assert beeps == [64]


def test_draggable_title_bar_moves_normal_and_maximized_windows(monkeypatch):
    bar = DraggableTitleBar()
    bar.resize(300, 40)
    normal_window = _FakeWindow()
    monkeypatch.setattr(bar, "window", lambda: normal_window)
    press = _MouseEvent(global_pos=(120, 100))

    bar.mousePressEvent(press)
    assert press.accepted is True
    assert bar._drag_pos == QPoint(100, 70)

    move = _MouseEvent(global_pos=(170, 145), local_pos=(50, 20))
    bar.mouseMoveEvent(move)
    assert move.accepted is True
    assert normal_window.moves[-1] == (70, 75)

    maximized_window = _FakeWindow(maximized=True)
    monkeypatch.setattr(bar, "window", lambda: maximized_window)
    bar._drag_pos = QPoint(10, 10)
    max_move = _MouseEvent(global_pos=(700, 100), local_pos=(150, 20))
    bar.mouseMoveEvent(max_move)
    assert maximized_window.normal_calls == 1
    assert maximized_window.moves[-1] == (550, 80)
    assert bar._drag_pos == QPoint(680, 70)


def test_draggable_title_bar_release_double_click_and_non_left_events(qt_application, monkeypatch):
    bar = DraggableTitleBar()
    bar.resize(300, 40)
    bar.show()
    fake_window = _FakeWindow()
    monkeypatch.setattr(bar, "window", lambda: fake_window)

    double_click = _MouseEvent()
    bar.mouseDoubleClickEvent(double_click)
    assert double_click.accepted is True
    assert fake_window.maximized_calls == 1

    fake_window.maximized = True
    second_double_click = _MouseEvent()
    bar.mouseDoubleClickEvent(second_double_click)
    assert fake_window.normal_calls == 1

    bar._drag_pos = QPoint(5, 5)
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton)
    assert bar._drag_pos is None

    QTest.mousePress(bar, Qt.MouseButton.RightButton)
    QTest.mouseMove(bar, QPoint(10, 10))
    QTest.mouseRelease(bar, Qt.MouseButton.RightButton)
    QTest.mouseDClick(bar, Qt.MouseButton.RightButton)
    assert bar._drag_pos is None
    bar.close()


def test_apply_table_density_updates_config_tables_styles_and_chrome(monkeypatch, qt_application):
    import app.services.ui_config_service as ui_config_service
    import ui.components as components
    import ui.models.table_model_helpers as table_model_helpers

    density_calls = []
    invalidations = []
    chrome_calls = []
    config = SimpleNamespace(table_density=None, sync=lambda: density_calls.append("sync"))

    class FakeTable(QWidget):
        def __init__(self):
            super().__init__()
            self.modes = []

        def apply_density(self, mode):
            self.modes.append(mode)

    monkeypatch.setattr(ui_config_service, "app_config", config)
    monkeypatch.setattr(components, "VCPTableView", FakeTable)
    monkeypatch.setattr(table_model_helpers, "invalidate_table_token_cache", invalidations.append)
    monkeypatch.setattr(main_window_visuals, "invalidate_global_qss_cache", lambda: invalidations.append("qss"))
    monkeypatch.setattr(main_window_visuals, "generate_global_qss", lambda *, density=None: f"QSS:{density}")
    monkeypatch.setattr(main_window_visuals, "apply_chrome_theme", chrome_calls.append)

    main_window = QWidget()
    main_window._act_density_compact = _Action()
    main_window._act_density_comfort = _Action()
    main_window._status_bar_widget = _ThemeWidget(main_window)
    table = FakeTable()
    table.show()

    main_window_visuals.apply_table_density(main_window, "未知", persist=True)
    assert config.table_density == "舒适"
    assert density_calls == ["sync"]
    assert invalidations == ["舒适", "qss"]
    assert main_window._act_density_compact.checked is False
    assert main_window._act_density_comfort.checked is True
    assert table.modes == ["舒适"]
    assert main_window.styleSheet() == ""
    assert qt_application.styleSheet() == "QSS:舒适"
    assert chrome_calls == [main_window]
    assert main_window._status_bar_widget.theme_calls == 1

    main_window_visuals.apply_table_density(main_window, "紧凑", persist=False)
    assert density_calls == ["sync"]
    assert table.modes[-1] == "紧凑"
    assert main_window._act_density_compact.checked is True
    table.close()
    main_window.close()


def test_apply_theme_repolishes_widgets_and_only_notifies_visible_window(monkeypatch, qt_application):
    import ui.components.toast_widget as toast_widget
    import ui.models.table_model_helpers as table_model_helpers

    calls = []
    monkeypatch.setattr(table_model_helpers, "invalidate_table_token_cache", lambda: calls.append("table"))
    monkeypatch.setattr(main_window_visuals, "invalidate_global_qss_cache", lambda: calls.append("qss"))
    monkeypatch.setattr(main_window_visuals, "generate_global_qss", lambda: "GLOBAL-QSS")
    monkeypatch.setattr(main_window_visuals, "hide_floating_tooltip", lambda: calls.append("hide-tooltip"))
    monkeypatch.setattr(main_window_visuals, "apply_chrome_theme", lambda window: calls.append(("chrome", window)))
    monkeypatch.setattr(main_window_visuals, "theme_manager", SimpleNamespace(current_theme_name="月白"))
    monkeypatch.setattr(toast_widget, "show_toast", lambda *args, **kwargs: calls.append(("toast", args, kwargs)))

    main_window = QWidget()
    main_window._custom_titlebar = QWidget(main_window)
    main_window._status_bar_widget = _ThemeWidget(main_window)
    main_window._standalone_tabbar = QWidget(main_window)
    main_window._workspace = QWidget(main_window)
    main_window._workspace.detail_drawer = QWidget(main_window._workspace)
    main_window.tabs_wrapper = QWidget(main_window)
    main_window.btn_sys_menu = QWidget(main_window)
    main_window.show()
    qt_application.processEvents()

    main_window_visuals.apply_theme(main_window, notify=True)
    assert main_window.styleSheet() == ""
    assert qt_application.styleSheet() == "GLOBAL-QSS"
    assert main_window._status_bar_widget.theme_calls == 1
    toast_calls = [item for item in calls if isinstance(item, tuple) and item[0] == "toast"]
    assert len(toast_calls) == 1
    assert "月白" in toast_calls[0][1][0]
    assert "hide-tooltip" in calls

    main_window.hide()
    main_window_visuals.apply_theme(main_window, notify=True)
    assert len([item for item in calls if isinstance(item, tuple) and item[0] == "toast"]) == 1
    main_window_visuals.apply_theme(main_window, notify=False)
    assert main_window._status_bar_widget.theme_calls == 3
    main_window.close()


def test_visual_helpers_tolerate_missing_application_and_optional_chrome(monkeypatch):
    import app.services.ui_config_service as ui_config_service
    import ui.models.table_model_helpers as table_model_helpers

    calls = []
    monkeypatch.setattr(main_window_visuals, "QApplication", SimpleNamespace(instance=lambda: None))
    monkeypatch.setattr(ui_config_service, "app_config", SimpleNamespace(table_density=None, sync=lambda: calls.append("sync")))
    monkeypatch.setattr(table_model_helpers, "invalidate_table_token_cache", lambda *args: calls.append(("table", args)))
    monkeypatch.setattr(main_window_visuals, "invalidate_global_qss_cache", lambda: calls.append("qss"))
    qss_calls = []
    monkeypatch.setattr(
        main_window_visuals,
        "generate_global_qss",
        lambda **kwargs: qss_calls.append(kwargs) or f"QSS:{kwargs}",
    )
    monkeypatch.setattr(main_window_visuals, "apply_chrome_theme", lambda window: calls.append(("chrome", window)))

    main_window = QWidget()
    main_window_visuals.apply_table_density(main_window, "舒适", persist=False)
    assert main_window.styleSheet() == ""

    main_window_visuals.apply_theme(main_window, notify=False)
    assert main_window.styleSheet() == ""
    assert qss_calls == []
    assert ("chrome", main_window) in calls
    assert "qss" in calls
    main_window.deleteLater()


def test_show_trade_calendar_wires_live_signals_and_cleans_up(monkeypatch, qt_application):
    class FakeDomainEvents(QObject):
        sig_earnings_updated = pyqtSignal()

    class FakeThemeManager(QObject):
        sig_theme_changed = pyqtSignal(str)

        def __init__(self):
            super().__init__()
            self.current_theme = "dark"

    class FakeService:
        def __init__(self):
            self.load_calls = 0

        def load_events(self):
            self.load_calls += 1
            return [{"symbol": "NVDA"}]

    class FakeCalendar(QWidget):
        clicked = pyqtSignal(QDate)
        instances = []

        def __init__(self, parent=None, *, earnings_events=None):
            super().__init__(parent)
            self.earnings_events = earnings_events
            self.set_events_calls = []
            self.dispose_calls = 0
            self.__class__.instances.append(self)

        def set_earnings_events(self, events):
            self.set_events_calls.append(events)

        def _dispose(self):
            self.dispose_calls += 1

    class FakePanel(QWidget):
        eventsChanged = pyqtSignal(object)
        instances = []

        def __init__(self, parent=None, *, events=None, service=None):
            super().__init__(parent)
            self.events = events
            self.service = service
            self.selected_dates = []
            self.refresh_calls = 0
            self.reload_calls = 0
            self.dispose_calls = 0
            self.__class__.instances.append(self)

        def set_selected_date(self, value):
            self.selected_dates.append(value)

        def refresh_from_service(self):
            self.refresh_calls += 1

        def reload_from_service_cache(self):
            self.reload_calls += 1

        def _dispose(self):
            self.dispose_calls += 1

    fake_events = FakeDomainEvents()
    fake_theme = FakeThemeManager()

    class FakeDialog(QDialog):
        def exec(self):
            calendar = FakeCalendar.instances[-1]
            panel = FakePanel.instances[-1]
            panel.eventsChanged.emit({"2026-07-14": ["event"]})
            calendar.clicked.emit(QDate(2026, 7, 14))
            fake_events.sig_earnings_updated.emit()
            fake_theme.current_theme = "light"
            fake_theme.sig_theme_changed.emit("light")
            calendar._dispose = None
            self.finished.emit(0)
            return 0

    token_calls = []

    def tokens(theme):
        token_calls.append(theme)
        return {
            "is_dark": theme == "dark",
            "shell": {"window_shadow_blur": 32, "window_shadow_offset_y": 8, "window_shadow_alpha": 0.25, "titlebar_height": 44},
        }

    service = FakeService()
    monkeypatch.setattr(main_window_visuals, "GlobalEarningsCalendarService", lambda: service)
    monkeypatch.setattr(main_window_visuals, "events_by_date", lambda events: {"mapped": events})
    monkeypatch.setattr(main_window_visuals, "TradeCalendarWidget", FakeCalendar)
    monkeypatch.setattr(main_window_visuals, "OligarchEarningsCalendarPanel", FakePanel)
    monkeypatch.setattr(main_window_visuals, "QDialog", FakeDialog)
    monkeypatch.setattr(main_window_visuals, "domain_events", fake_events)
    monkeypatch.setattr(main_window_visuals, "theme_manager", fake_theme)
    monkeypatch.setattr(main_window_visuals, "build_ui_tokens", tokens)
    monkeypatch.setattr(main_window_visuals, "QTimer", SimpleNamespace(singleShot=lambda _delay, callback: callback()))

    main_window = QWidget()
    main_window_visuals.show_trade_calendar(main_window)
    calendar = FakeCalendar.instances[-1]
    panel = FakePanel.instances[-1]

    assert service.load_calls == 1
    assert calendar.earnings_events == {"mapped": [{"symbol": "NVDA"}]}
    assert calendar.set_events_calls == [{"2026-07-14": ["event"]}]
    assert panel.selected_dates == ["2026-07-14"]
    assert panel.refresh_calls == 1
    assert panel.reload_calls == 1
    assert panel.dispose_calls >= 1
    assert calendar.dispose_calls == 0
    assert token_calls == ["dark", "dark", "light"]

    reload_count = panel.reload_calls
    fake_events.sig_earnings_updated.emit()
    fake_theme.sig_theme_changed.emit("light")
    assert panel.reload_calls == reload_count
    main_window.deleteLater()


def _table_with_values():
    table = QTableView()
    model = QStandardItemModel(2, 2, table)
    model.setItem(0, 0, QStandardItem("A0"))
    model.setItem(0, 1, QStandardItem("A1"))
    model.setItem(1, 0, QStandardItem("B0"))
    model.setItem(1, 1, QStandardItem("B1"))
    table.setModel(model)
    return table, model


def test_table_copy_hook_handles_return_single_cell_and_multiple_rows(monkeypatch, qt_application):
    toasts = []
    monkeypatch.setattr(main_window_tables, "show_toast", lambda *args, **kwargs: toasts.append((args, kwargs)))
    table, model = _table_with_values()
    main_window_tables.install_table_copy_hooks([table])
    table.show()
    table.setFocus()
    table.setCurrentIndex(model.index(0, 1))

    double_clicks = QSignalSpy(table.doubleClicked)
    return_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    table.keyPressEvent(return_event)
    assert len(double_clicks) == 1
    assert return_event.isAccepted() is True

    selection = table.selectionModel()
    selection.select(model.index(0, 0), QItemSelectionModel.SelectionFlag.Select)
    selection.select(model.index(0, 1), QItemSelectionModel.SelectionFlag.Select)
    copy_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    table.keyPressEvent(copy_event)
    assert QApplication.clipboard().text() == "A1"
    assert toasts[-1][0][0].startswith("已复制")

    selection.clearSelection()
    selection.select(model.index(0, 0), QItemSelectionModel.SelectionFlag.Select)
    selection.select(model.index(1, 1), QItemSelectionModel.SelectionFlag.Select)
    table.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier))
    assert QApplication.clipboard().text() == "A0\nB1"

    first_handler = table.keyPressEvent
    main_window_tables.install_table_copy_hooks([table])
    assert table.keyPressEvent is first_handler
    assert table.selectionBehavior() == QTableView.SelectionBehavior.SelectRows
    assert table.selectionMode() == QTableView.SelectionMode.ExtendedSelection
    table.close()


def test_table_copy_hook_does_not_reapply_selection_state_to_installed_table():
    calls = []
    table = SimpleNamespace(
        _copy_hook_installed=True,
        setSelectionBehavior=lambda value: calls.append(("behavior", value)),
        setSelectionMode=lambda value: calls.append(("mode", value)),
    )

    main_window_tables.install_table_copy_hooks([table])

    assert calls == []


def test_table_copy_handler_empty_selection_and_fallback(qt_application):
    table, _model = _table_with_values()
    originals = []
    handler = main_window_tables._build_copy_handler(table, originals.append)
    QApplication.clipboard().setText("sentinel")

    copy_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    handler(copy_event)
    assert copy_event.isAccepted() is True
    assert QApplication.clipboard().text() == "sentinel"

    other_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier)
    handler(other_event)
    assert originals == [other_event]
    table.deleteLater()


def test_table_copy_handler_handles_missing_selection_model():
    originals = []
    fake_table = SimpleNamespace(selectionModel=lambda: None)
    handler = main_window_tables._build_copy_handler(fake_table, originals.append)

    return_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    handler(return_event)
    assert originals == [return_event]

    copy_event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    handler(copy_event)
    assert copy_event.isAccepted() is True
    assert originals == [return_event]


def test_workspace_navigation_group_indices_and_keys():
    specs = [
        {"key": "scan", "group": "选股", "group_order": 20},
        {"key": "watchlist", "group": "自选", "group_order": 5},
        {"key": "candidate", "group": "选股", "group_order": 10},
        {"key": "blank", "group": ""},
    ]
    workspace = SimpleNamespace(_tab_specs=specs)
    service = WorkspaceNavigationService(workspace)

    assert service.tab_indices_by_group() == {"选股": [2, 0], "自选": [1], "": [3]}
    assert service._key_for_index(1) == "watchlist"
    assert service._key_for_index(-1) == ""
    assert service._key_for_index(99) == ""


def test_workspace_navigation_falls_back_to_public_specs():
    workspace = SimpleNamespace(_tab_specs=None, tab_specs=lambda: [{"key": "one", "group": "A"}])
    assert WorkspaceNavigationService(workspace)._tab_specs() == [{"key": "one", "group": "A"}]
    workspace.tab_specs = None
    assert WorkspaceNavigationService(workspace)._tab_specs() == []


def test_workspace_navigation_selects_current_preferred_lazy_and_remaining_tabs():
    current = _SelectableTab(code_result=False)
    preferred_placeholder = object()
    preferred = _SelectableTab(code_result=True)
    remaining = _SelectableTab(code_result=True)
    tabs = _Tabs([current, preferred_placeholder, None, remaining], current=0)
    loaded = []

    def get_tab(key):
        loaded.append(key)
        return preferred if key == "preferred" else None

    workspace = SimpleNamespace(
        _tab_specs=[
            {"key": "current"},
            {"key": "preferred"},
            {"key": "none"},
            {"key": "remaining"},
        ],
        tabs=tabs,
        get_tab=get_tab,
    )
    service = WorkspaceNavigationService(workspace)

    assert service.select_code_row(" 600519 ", preferred_tab_index=1) is True
    assert current.codes == ["600519"]
    assert preferred.codes == ["600519"]
    assert loaded == ["preferred"]
    assert tabs.changes == [1]

    preferred.code_result = False
    tabs.current = 0
    assert service.select_code_row("000001", preferred_tab_index=99) is True
    assert remaining.codes == ["000001"]
    assert tabs.changes[-1] == 3


def test_workspace_navigation_selects_staged_tab_without_preferred_lazy_load():
    current = _SelectableTab(code_result=False)
    placeholder = object()
    staged = _SelectableTab(code_result=True)
    tabs = _Tabs([current, placeholder], current=0)
    lazy_loads = []
    workspace = SimpleNamespace(
        _tab_specs=[{"key": "current"}, {"key": "staged"}],
        tabs=tabs,
        get_loaded_tab=lambda key: staged if key == "staged" else None,
        get_tab=lambda key: lazy_loads.append(key),
    )

    service = WorkspaceNavigationService(workspace)

    assert service.select_code_row("600519") is True
    assert current.codes == ["600519"]
    assert staged.codes == ["600519"]
    assert tabs.changes == [1]
    assert lazy_loads == []


def test_workspace_navigation_rejects_empty_missing_and_unselectable_tabs():
    service = WorkspaceNavigationService(SimpleNamespace(tabs=_Tabs([])))
    assert service.select_code_row("") is False
    assert service.select_code_row(None) is False

    service = WorkspaceNavigationService(SimpleNamespace())
    assert service.select_code_row("600519") is False

    tabs = _Tabs([None, object()], current=-1)
    service = WorkspaceNavigationService(SimpleNamespace(tabs=tabs, _tab_specs=[]))
    assert service.select_code_row("600519", preferred_tab_index="1") is False
    assert tabs.changes == []


def test_workspace_navigation_current_preferred_tab_needs_no_lazy_load_or_switch():
    current = _SelectableTab(code_result=True)
    tabs = _Tabs([current], current=0)
    service = WorkspaceNavigationService(SimpleNamespace(tabs=tabs, _tab_specs=[{"key": "current"}]))

    assert service.select_code_row("600519", preferred_tab_index=0) is True
    assert current.codes == ["600519"]
    assert tabs.changes == []
