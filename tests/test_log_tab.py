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
        assert tab.btn_clear_log.minimumWidth() == 54
        assert tab.btn_clear_log.maximumWidth() == 54
        assert tab.search_box.minimumWidth() == 132
        assert tab.level_filter.minimumWidth() == 96
    finally:
        tab.deleteLater()
