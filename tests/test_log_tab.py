import sys
import threading

from PyQt6.QtWidgets import QApplication

from app.services.ui_event_service import domain_events
from infra.events import ui_signal_hub
from ui.services.log_buffer_service import LogBufferService, install_log_buffer_service
from ui.tabs import log_tab as log_tab_module
from ui.tabs.log_tab import LogTab


def test_log_buffer_clear_cannot_overtake_an_inflight_append():
    append_entered = threading.Event()
    append_release = threading.Event()
    clear_finished = threading.Event()

    class BlockingEntries:
        def __init__(self):
            self.data = []

        def append(self, entry):
            append_entered.set()
            assert append_release.wait(1)
            self.data.append(entry)

        def clear(self):
            self.data.clear()

        def __iter__(self):
            return iter(self.data)

    service = LogBufferService()
    service._entries = BlockingEntries()
    capture = threading.Thread(target=service._capture_entry, args=("info", "before clear"))
    clear = threading.Thread(target=lambda: (service.clear(), clear_finished.set()))

    capture.start()
    assert append_entered.wait(1)
    clear.start()
    assert clear_finished.wait(0.05) is False
    append_release.set()
    capture.join(1)
    clear.join(1)

    generation, sequence, history = service.snapshot_versioned()
    assert (generation, sequence, history) == (1, 1, [])


def test_log_tab_flush_timer_only_runs_while_visible():
    app = QApplication.instance()
    tab = LogTab()
    try:
        assert tab._log_flush_timer.isActive() is False

        tab.show()
        app.processEvents()
        assert tab._log_flush_timer.isActive() is True

        tab.hide()
        app.processEvents()
        assert tab._log_flush_timer.isActive() is False

        tab._on_log_msg("info", "while hidden\n")
        tab.show()
        app.processEvents()
        assert tab._log_flush_timer.isActive() is True
        assert "while hidden" in tab.log_text.toPlainText()
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_log_tab_background_preload_reuses_buffer_snapshot_synchronously():
    tab = LogTab()
    try:
        assert tab.is_background_preload_complete() is False
        assert tab.prime_background_load() is True
        assert tab.is_background_preload_complete() is True
        assert tab._log_flush_timer.isActive() is False
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_log_tab_skips_hidden_flush_and_recovers_from_history():
    app = QApplication.instance()
    tab = LogTab()
    try:
        tab._history_refresh_delay_ms = 0
        tab._history_refresh_interval_ms = 0
        tab._on_log_msg("info", "hello world\n")
        tab._flush_log_buffer()

        assert tab.log_text.toPlainText() == ""
        assert tab._refresh_from_history_pending is True

        tab.show()
        app.processEvents()
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


def test_log_tab_flushes_visible_logs_in_bounded_batches():
    app = QApplication.instance()
    tab = LogTab()
    try:
        tab._log_flush_batch_max = 2
        tab.show()
        app.processEvents()

        tab._on_log_msg("info", "first\n")
        tab._on_log_msg("info", "second\n")
        tab._on_log_msg("info", "third\n")

        tab._flush_log_buffer()

        visible_text = tab.log_text.toPlainText()
        assert "first" in visible_text
        assert "second" in visible_text
        assert "third" not in visible_text
        assert len(tab._log_buffer) == 1
        assert tab._visible_log_count == 2

        tab._flush_log_buffer()

        assert "third" in tab.log_text.toPlainText()
        assert tab._log_buffer == []
        assert tab._visible_log_count == 3
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_log_tab_status_summary_uses_rendered_count_without_refiltering(monkeypatch):
    tab = LogTab()
    try:
        tab._visible_log_count = 7
        tab._log_history = [("info", f"line {idx}") for idx in range(20)]

        def _fail_filter(*_args, **_kwargs):
            raise AssertionError("status refresh should not refilter full history")

        monkeypatch.setattr(tab, "_filtered_entries", _fail_filter)

        tab._refresh_status_summary()

        assert "7" in tab.lbl_status.text()
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_log_tab_show_rebuilds_history_in_bounded_batches():
    app = QApplication.instance()
    tab = LogTab()
    try:
        tab._history_refresh_delay_ms = 0
        tab._history_refresh_interval_ms = 0
        tab._history_refresh_batch_max = 2
        tab._log_history = [
            ("info", "first\n"),
            ("info", "second\n"),
            ("info", "third\n"),
        ]
        tab._refresh_from_history_pending = True

        tab.show()
        app.processEvents()

        visible_text = tab.log_text.toPlainText()
        assert "first" in visible_text
        assert "second" in visible_text
        assert "third" not in visible_text
        assert tab._history_rebuild_entries

        app.processEvents()

        assert "third" in tab.log_text.toPlainText()
        assert tab._history_rebuild_entries == []
        assert tab._visible_log_count == 3
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_log_tab_history_rebuild_honors_character_budget_without_dropping_entries(monkeypatch):
    app = QApplication.instance()
    tab = LogTab()
    try:
        tab.show()
        app.processEvents()
        scheduled = []
        monkeypatch.setattr(
            "ui.tabs.log_tab.QTimer.singleShot",
            lambda _delay, callback: scheduled.append(callback),
        )
        tab._history_refresh_batch_max = 10
        tab._history_refresh_char_max = 5
        tab._log_history = [
            ("info", "aaaa\n"),
            ("warn", "bbbb\n"),
            ("error", "cccc\n"),
        ]

        tab._start_history_refresh()

        assert tab.log_text.toPlainText() == "aaaa\n"
        assert len(tab._history_rebuild_entries) == 2
        while scheduled:
            scheduled.pop(0)()

        assert tab.log_text.toPlainText() == "aaaa\nbbbb\ncccc\n"
        assert tab._history_rebuild_entries == []
        assert tab._visible_log_count == 3
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_log_tab_ignores_stale_deferred_history_refresh():
    tab = LogTab()
    try:
        tab._log_history = [("info", "old\n")]
        tab._refresh_from_history_pending = True
        tab._schedule_history_refresh(delay_ms=100)
        stale_token = tab._history_refresh_token

        tab._history_refresh_token += 1
        tab._log_history = [("info", "new\n")]
        tab._start_history_refresh(stale_token)

        assert tab.log_text.toPlainText() == ""
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_log_tab_hidden_diagnostic_count_uses_cache(monkeypatch):
    tab = LogTab()
    try:
        tab._on_log_msg("warn", "[event] ui.stall.event_loop | tab=system_log\n")
        tab._on_log_msg("info", "normal\n")

        monkeypatch.setattr(
            LogTab,
            "_is_diagnostic_log",
            classmethod(lambda cls, text: (_ for _ in ()).throw(AssertionError("should use cache"))),
        )

        assert tab._hidden_diagnostic_count() == 1
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


def test_log_tab_hides_ui_stall_diagnostics_by_default():
    tab = LogTab()
    try:
        tab._log_history = [
            ("info", "[基金持仓] 刷新完成"),
            ("warn", "[事件] ui.stall.event_loop | method=FundHoldingsTab._reload_from_db"),
            ("warning", "[事件] ui.stall.method | method=MainWindowQT.create_workspace"),
            ("debug", "[指标] ui_event_loop_stall_ms | 120ms"),
            ("error", "业务错误"),
        ]

        assert tab._filtered_entries() == [
            ("info", "[基金持仓] 刷新完成"),
            ("error", "业务错误"),
        ]

        tab._refresh_status_summary()
        assert "隐藏诊断 3条" in tab.lbl_status.text()

        tab.search_box.setText("ui.stall")
        assert tab._filtered_entries() == [
            ("warn", "[事件] ui.stall.event_loop | method=FundHoldingsTab._reload_from_db"),
            ("warning", "[事件] ui.stall.method | method=MainWindowQT.create_workspace"),
        ]
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_log_tab_keeps_hidden_diagnostics_out_of_visible_flush_queue():
    app = QApplication.instance()
    tab = LogTab()
    try:
        tab.show()
        app.processEvents()

        tab._on_log_msg("warn", "[event] ui.stall.event_loop | tab=system_log\n")

        assert tab._log_buffer == []
        assert len(tab._log_history) == 1
        assert tab._log_status_refresh_pending is True

        tab._flush_log_buffer()

        assert tab.log_text.toPlainText() == ""
        assert tab._hidden_diagnostic_count() == 1
        assert tab._log_status_refresh_pending is False

        tab.search_box.setText("ui.stall")
        tab._on_log_msg("warn", "[event] ui.stall.event_loop | tab=system_log\n")

        assert len(tab._log_buffer) == 1
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
        ui_signal_hub.sig_task_progress.emit("quotes", 100, "done")
        app.processEvents()

        details = tab.task_status_panel.details_edit.toPlainText()
        assert "scan" in details
        assert "running" in details
        assert "quotes" in details
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


def test_log_tab_queued_clear_is_ignored_after_shutdown():
    tab = LogTab()
    tab._on_log_msg("info", "keep before shutdown\n")
    before = list(tab._log_history)

    tab.shutdown()
    log_tab_module._apply_shared_log_clear(tab, tab._log_generation + 1, 0)

    assert tab._log_history == before
    tab.deleteLater()


def test_log_tab_hydrates_entries_captured_before_widget_creation():
    app = QApplication.instance()
    service = install_log_buffer_service()
    try:
        domain_events.sig_system_log.emit("warn", "startup before log tab\n")
        app.processEvents()

        tab = LogTab()
        try:
            assert ("warn", "startup before log tab\n") in tab._log_history
        finally:
            tab.shutdown()
            tab.deleteLater()
    finally:
        service.shutdown()


def test_log_tab_rejects_pre_clear_entries_delivered_after_clear():
    tab = LogTab()
    try:
        service = tab._log_service
        old_generation = service.generation
        old_sequence = tab._last_log_sequence + 1

        tab._clear_logs()
        tab._on_versioned_log_msg(old_generation, old_sequence, "warn", "stale before clear\n")

        assert tab._log_history == []
        assert "stale before clear" not in tab.log_text.toPlainText()
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_log_clear_propagates_to_all_log_tabs_without_dropping_new_generation():
    app = QApplication.instance()
    first = LogTab()
    second = LogTab()
    try:
        first._on_log_msg("info", "old local entry\n")
        second._on_log_msg("info", "old local entry\n")

        first._clear_logs()
        domain_events.sig_system_log.emit("info", "new generation entry\n")
        app.processEvents()
        app.processEvents()

        assert all("old local entry" not in text for _level, text in second._log_history)
        assert ("info", "new generation entry\n") in second._log_history
    finally:
        first.shutdown()
        second.shutdown()
        first.deleteLater()
        second.deleteLater()


def test_log_service_marshals_worker_thread_entries_to_log_tab():
    app = QApplication.instance()
    tab = LogTab()
    try:
        worker = threading.Thread(
            target=lambda: domain_events.sig_system_log.emit("warn", "worker thread entry\n"),
            daemon=True,
        )
        worker.start()
        worker.join(timeout=1)
        app.processEvents()

        assert worker.is_alive() is False
        assert ("warn", "worker thread entry\n") in tab._log_history
    finally:
        tab.shutdown()
        tab.deleteLater()
