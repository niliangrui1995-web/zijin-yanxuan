import sys

from PyQt6.QtWidgets import QApplication

from infra.events import ui_signal_hub
from ui.tabs.log_tab import LogTab


def test_log_tab_skips_hidden_flush_and_recovers_from_history():
    app = QApplication.instance()
    tab = LogTab()
    try:
        tab._on_log_msg("info", "hello world\n")
        tab._flush_log_buffer()

        assert tab.log_text.toPlainText() == ""
        assert tab._refresh_from_history_pending is True

        tab.show()
        app.processEvents()

        assert "hello world" in tab.log_text.toPlainText()
        assert tab._refresh_from_history_pending is False
        assert "1" in tab.lbl_status.text()
        assert tab.btn_clear_log.minimumWidth() == 58
        assert tab.btn_clear_log.property("inToolbar") is True
        assert tab.search_box.minimumWidth() == 150
        assert tab.search_box.property("inToolbar") is True
        assert tab.level_filter.minimumWidth() == 92
        assert tab.level_filter.property("inToolbar") is True
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_log_tab_level_filter_supports_multi_select():
    tab = LogTab()
    try:
        tab._log_history = [
            ("info", "info message"),
            ("error", "error message"),
            ("warn", "warn message"),
        ]

        tab.level_filter.set_selected_values({"error", "warning"})
        filtered = tab._filtered_entries()
        assert filtered == [
            ("error", "error message"),
            ("warn", "warn message"),
        ]

        tab.level_filter.set_selected_values(set())
        assert len(tab._filtered_entries()) == 3
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_log_tab_renders_task_status_updates():
    app = QApplication.instance()
    tab = LogTab()
    try:
        tab.show()
        app.processEvents()

        ui_signal_hub.sig_task_progress.emit("scan", 35, "running")
        ui_signal_hub.sig_task_progress.emit("rt_monitor", 100, "done")
        app.processEvents()

        details = tab.task_status_panel.details_edit.toPlainText()
        assert "scan" in details
        assert "running" in details
        assert "rt_monitor" in details
        assert "done" in details
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_log_tab_shutdown_restores_redirected_streams():
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    tab = LogTab()
    try:
        assert getattr(sys.stdout, "_is_ui_log_redirect", False) is True
        assert getattr(sys.stderr, "_is_ui_log_redirect", False) is True
    finally:
        tab.shutdown()
        tab.deleteLater()

    assert sys.stdout is original_stdout
    assert sys.stderr is original_stderr
