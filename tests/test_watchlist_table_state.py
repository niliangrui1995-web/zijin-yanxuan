from PyQt6.QtWidgets import QTableView

from ui.tabs.watchlist_table_state import LazyWatchlistTableStateWrapper


def test_watchlist_table_state_defers_overlay_until_a_state_is_requested(qt_application):
    table = QTableView()
    wrapper = LazyWatchlistTableStateWrapper(table, empty_title="空", loading_title="载入")

    try:
        assert wrapper._overlay is None
        assert wrapper._stack.currentWidget() is table

        wrapper.show_loading()

        assert wrapper._overlay is not None
        assert wrapper._stack.currentWidget() is wrapper._overlay

        wrapper.show_table()
        assert wrapper._stack.currentWidget() is table
    finally:
        wrapper.deleteLater()


def test_watchlist_table_state_reuses_one_overlay_for_all_states(qt_application):
    wrapper = LazyWatchlistTableStateWrapper(QTableView())

    try:
        wrapper.show_empty()
        overlay = wrapper._overlay
        wrapper.show_error("失败", "稍后重试")
        wrapper.show_info("已恢复")

        assert wrapper._overlay is overlay
        assert wrapper._stack.count() == 2
    finally:
        wrapper.deleteLater()
