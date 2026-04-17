from PyQt6.QtWidgets import QApplication

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
        assert "日志 1条" in tab.lbl_status.text()
        assert tab.btn_clear_log.minimumWidth() == 50
        assert tab.btn_clear_log.maximumWidth() == 50
        assert tab.btn_clear_log.property("inToolbar") is True
        assert tab.search_box.minimumWidth() == 120
        assert tab.search_box.property("inToolbar") is True
        assert tab.level_filter.minimumWidth() == 90
        assert tab.level_filter.property("inToolbar") is True
    finally:
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
        tab.deleteLater()
