# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from PyQt6.QtCore import QItemSelectionModel, Qt

from ui.tabs.fund_holdings_tab import FundHoldingsTab


class _DummyProvider:
    pass


def _view_rows(count: int) -> list[dict]:
    return [
        {
            "代码": f"{index:06d}",
            "名称": f"测试{index}",
            "主体": "QFII",
            "资金属性": "--",
            "季度": "2026Q1",
            "变化类型": "增持",
            "_capital_attribute_value": "",
            "_is_latest_subject_quarter": True,
        }
        for index in range(count)
    ]


def _drain_committer(tab: FundHoldingsTab) -> None:
    while tab._view_committer.is_active:
        tab._view_committer._timer.stop()
        tab._view_committer.apply_next()


def _visible_codes(tab: FundHoldingsTab) -> list[str]:
    code_column = tab.model.headers.index("代码")
    return [
        str(tab.proxy_model.data(tab.proxy_model.index(row, code_column), Qt.ItemDataRole.DisplayRole) or "")
        for row in range(tab.proxy_model.rowCount())
    ]


def _visible_row(tab: FundHoldingsTab, visual_row: int) -> dict:
    proxy_index = tab.proxy_model.index(visual_row, 0)
    source_index = tab.proxy_model.mapToSource(proxy_index)
    return tab.model.get_row_data(source_index.row())


def _fund_row_identity(row: dict) -> tuple[str, ...]:
    return (
        row["代码"],
        row.get("主体代码", ""),
        row.get("主体原名") or row.get("主体", ""),
        row.get("季度", ""),
        row.get("_capital_attribute_value") or row.get("资金属性", ""),
    )


def _stub_finish(monkeypatch, completed: list[list[str]]) -> None:
    monkeypatch.setattr(
        FundHoldingsTab,
        "_finish_apply_view_payload",
        lambda self, rows: completed.append([row["代码"] for row in rows]) or False,
    )
    monkeypatch.setattr(FundHoldingsTab, "_show_empty_view_payload_if_needed", lambda self, rows: None)


def test_large_fund_holdings_payload_is_committed_in_bounded_gui_batches(monkeypatch):
    completed = []
    _stub_finish(monkeypatch, completed)
    tab = FundHoldingsTab(_DummyProvider(), autoload=False)
    rows = _view_rows(137)
    batch_sizes = []
    original_append = tab.model.append_rows

    def _recording_append(chunk):
        batch_sizes.append(len(chunk))
        return original_append(chunk)

    monkeypatch.setattr(tab.model, "append_rows", _recording_append)
    try:
        tab._apply_view_rows_and_finish(rows, defer_finish=False)

        assert tab.model.rowCount() == tab.VIEW_ROW_CHUNK_SIZE
        assert tab._view_committer.pending_count == len(rows) - tab.VIEW_ROW_CHUNK_SIZE
        assert completed == []

        _drain_committer(tab)

        assert batch_sizes
        assert max(batch_sizes) <= tab.VIEW_ROW_CHUNK_SIZE
        assert [row["代码"] for row in tab.model.row_data] == [row["代码"] for row in rows]
        assert [row[tab.model.headers[0]] for row in tab.model.row_data] == list(range(1, len(rows) + 1))
        assert completed == [[row["代码"] for row in rows]]
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_chunked_fund_holdings_commit_pauses_and_resumes_without_losing_pending_rows(monkeypatch):
    """A foreground Watchlist hold must not discard an in-flight hidden commit."""
    completed = []
    _stub_finish(monkeypatch, completed)
    tab = FundHoldingsTab(_DummyProvider(), autoload=False)
    rows = _view_rows(96)
    try:
        tab._apply_view_rows_and_finish(rows, defer_finish=False)
        committer = tab._view_committer
        pending_before_pause = committer.pending_count
        row_count_before_pause = tab.model.rowCount()

        assert committer.pause() is True
        assert committer.is_paused is True
        committer.apply_next()

        assert committer.pending_count == pending_before_pause
        assert tab.model.rowCount() == row_count_before_pause == tab.VIEW_ROW_CHUNK_SIZE

        assert committer.resume() is True
        _drain_committer(tab)

        assert committer.is_paused is False
        assert [row["代码"] for row in tab.model.row_data] == [row["代码"] for row in rows]
        assert completed == [[row["代码"] for row in rows]]
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_background_fund_preload_defers_completed_payload_until_watchlist_hold_releases(
    monkeypatch,
    qt_application,
):
    """A worker callback must not start model commits while Watchlist owns the foreground."""
    completed = []
    _stub_finish(monkeypatch, completed)
    tab = FundHoldingsTab(_DummyProvider(), autoload=False)
    payload = {"view_rows": _view_rows(48), "loaded_quarter_scope": "latest"}
    try:
        tab._background_preload_requested = True
        tab._initial_load_started = True
        assert tab.pause_background_preload() is True

        tab._apply_view_payload(payload)

        assert tab.model.rowCount() == 0
        assert tab._background_preload_pending_payload is payload
        assert tab.is_background_preload_complete() is False

        assert tab.resume_background_preload() is True
        for _ in range(3):
            qt_application.processEvents()

        assert tab.model.rowCount() > 0
        _drain_committer(tab)
        assert [row["代码"] for row in tab.model.row_data] == [row["代码"] for row in payload["view_rows"]]
        assert completed == [[row["代码"] for row in payload["view_rows"]]]
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_background_fund_preload_drops_stale_payload_held_across_a_new_generation(
    monkeypatch,
    qt_application,
):
    """A held worker result must not overwrite a newer fund-holdings request."""
    completed = []
    _stub_finish(monkeypatch, completed)
    tab = FundHoldingsTab(_DummyProvider(), autoload=False)
    payload = {"view_rows": _view_rows(48), "loaded_quarter_scope": "latest"}
    try:
        tab._background_preload_requested = True
        tab._initial_load_started = True
        assert tab.pause_background_preload() is True
        tab._apply_view_payload(payload)
        tab._view_load_generation += 1

        assert tab.resume_background_preload() is True
        for _ in range(3):
            qt_application.processEvents()

        assert tab.model.rowCount() == 0
        assert completed == []
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_background_fund_preload_defers_a_rows_callback_already_queued_before_hold_release(
    monkeypatch,
    qt_application,
):
    """The second-stage singleShot callback must not append rows during the hold."""
    completed = []
    _stub_finish(monkeypatch, completed)
    tab = FundHoldingsTab(_DummyProvider(), autoload=False)
    rows = _view_rows(48)
    try:
        tab._background_preload_requested = True
        tab._initial_load_started = True
        assert tab.pause_background_preload() is True

        tab._apply_view_rows_and_finish(rows, defer_finish=True, generation=0)

        assert tab.model.rowCount() == 0
        assert tab._background_preload_pending_rows == (rows, True, 0)

        assert tab.resume_background_preload() is True
        for _ in range(3):
            qt_application.processEvents()

        assert tab.model.rowCount() > 0
        _drain_committer(tab)
        assert completed == [[row["代码"] for row in rows]]
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_chunked_fund_holdings_commit_updates_all_duplicate_quote_rows_after_first_batch(monkeypatch):
    completed = []
    _stub_finish(monkeypatch, completed)
    tab = FundHoldingsTab(_DummyProvider(), autoload=False)
    rows = _view_rows(tab.VIEW_ROW_CHUNK_SIZE + 2)
    first_row = 0
    appended_row = tab.VIEW_ROW_CHUNK_SIZE
    rows[first_row]["代码"] = "600000"
    rows[appended_row]["代码"] = "600000"
    try:
        tab._apply_view_rows_and_finish(rows, defer_finish=False)
        _drain_committer(tab)

        changed_rows = tab.model.update_quotes(
            {"600000": {"close": 10.5, "last_close": 10.0, "total_shares": 1_000_000_000}}
        )

        assert changed_rows == 2
        for row_index in (first_row, appended_row):
            row = tab.model.get_row_data(row_index)
            assert row["市价"] == "10.50"
            assert row["涨幅%"] == pytest.approx(5.0)
            assert row["市值"] == "105亿"
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_chunked_fund_holdings_commit_hydrates_snapshot_that_precedes_later_duplicate_rows(monkeypatch):
    from core.global_store import global_store

    completed = []
    _stub_finish(monkeypatch, completed)
    monkeypatch.setattr(
        global_store,
        "get_latest_quotes",
        lambda: {"600000": {"close": 10.5, "last_close": 10.0, "total_shares": 1_000_000_000}},
    )
    tab = FundHoldingsTab(_DummyProvider(), autoload=False)
    rows = _view_rows(tab.VIEW_ROW_CHUNK_SIZE + 2)
    first_row = 0
    appended_row = tab.VIEW_ROW_CHUNK_SIZE
    rows[first_row]["代码"] = "600000"
    rows[appended_row]["代码"] = "600000"
    try:
        tab._apply_view_rows_and_finish(rows, defer_finish=False)
        _drain_committer(tab)

        for row_index in (first_row, appended_row):
            row = tab.model.get_row_data(row_index)
            assert row["市价"] == "10.50"
            assert row["涨幅%"] == pytest.approx(5.0)
            assert row["市值"] == "105亿"
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_chunked_fund_holdings_commit_preserves_sort_and_selected_code(monkeypatch, qt_application):
    completed = []
    _stub_finish(monkeypatch, completed)
    tab = FundHoldingsTab(_DummyProvider(), autoload=False)
    rows = _view_rows(96)
    target_code = "000083"
    try:
        tab.model.update_data(rows, hydrate_latest_quotes=False)
        code_column = tab.model.headers.index("代码")
        tab.table.sortByColumn(code_column, Qt.SortOrder.DescendingOrder)
        target_row = _visible_codes(tab).index(target_code)
        tab.table.selectRow(target_row)
        tab.table.setCurrentIndex(tab.proxy_model.index(target_row, code_column))

        updated_rows = [dict(row, 名称=f"{row['名称']}-新") for row in rows]
        tab._apply_view_rows_and_finish(updated_rows, defer_finish=False)
        _drain_committer(tab)
        qt_application.processEvents()

        selected_rows = tab.table.selectionModel().selectedRows()
        assert _visible_codes(tab) == sorted([row["代码"] for row in rows], reverse=True)
        assert [_visible_codes(tab)[index.row()] for index in selected_rows] == [target_code]
        assert _visible_codes(tab)[tab.table.currentIndex().row()] == target_code
        assert completed == [[row["代码"] for row in updated_rows]]
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_chunked_commit_restores_duplicate_code_rows_by_full_holding_identity(monkeypatch, qt_application):
    completed = []
    _stub_finish(monkeypatch, completed)
    tab = FundHoldingsTab(_DummyProvider(), autoload=False)
    rows = _view_rows(96)
    duplicate_specs = (
        (12, "主体甲", "2025Q4", "self_owned", "自营资金"),
        (44, "主体甲", "2025Q4", "client", "客户资金"),
        (55, "主体甲", "2026Q1", "self_owned", "自营资金"),
        (83, "主体乙", "2026Q1", "client", "客户资金"),
    )
    for source_row, subject, quarter, capital_value, capital_text in duplicate_specs:
        rows[source_row].update(
            {
                "代码": "600000",
                "主体": subject,
                "主体原名": subject,
                "主体代码": "qfii",
                "季度": quarter,
                "资金属性": capital_text,
                "_capital_attribute_value": capital_value,
            }
        )

    target_identities = {
        _fund_row_identity(rows[12]),
        _fund_row_identity(rows[83]),
    }
    current_identity = _fund_row_identity(rows[12])
    try:
        tab.model.update_data(rows, hydrate_latest_quotes=False)
        current_column = tab.model.headers.index("主体")
        quarter_column = tab.model.headers.index("季度")
        tab.proxy_model.set_filter_state(filter_text="600000", latest_only=False)
        tab.table.sortByColumn(quarter_column, Qt.SortOrder.DescendingOrder)

        selection_model = tab.table.selectionModel()
        for visual_row in range(tab.proxy_model.rowCount()):
            identity = _fund_row_identity(_visible_row(tab, visual_row))
            if identity in target_identities:
                selection_model.select(
                    tab.proxy_model.index(visual_row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )
                if identity == current_identity:
                    selection_model.setCurrentIndex(
                        tab.proxy_model.index(visual_row, current_column),
                        QItemSelectionModel.SelectionFlag.NoUpdate,
                    )

        updated_rows = [dict(row, 名称=f"{row['名称']}-新") for row in rows]
        tab._apply_view_rows_and_finish(updated_rows, defer_finish=False)
        _drain_committer(tab)
        qt_application.processEvents()

        selected_identities = {
            _fund_row_identity(_visible_row(tab, index.row())) for index in tab.table.selectionModel().selectedRows()
        }
        current_row = _visible_row(tab, tab.table.currentIndex().row())
        visible_quarters = [_visible_row(tab, row)["季度"] for row in range(tab.proxy_model.rowCount())]

        assert tab.proxy_model.rowCount() == len(duplicate_specs)
        assert visible_quarters == sorted(visible_quarters, reverse=True)
        assert selected_identities == target_identities
        assert _fund_row_identity(current_row) == current_identity
        assert tab.table.currentIndex().column() == current_column
        assert completed == [[row["代码"] for row in updated_rows]]
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_chunked_commit_uses_occurrence_for_legacy_duplicate_rows_with_missing_fields(monkeypatch):
    completed = []
    _stub_finish(monkeypatch, completed)
    tab = FundHoldingsTab(_DummyProvider(), autoload=False)
    rows = _view_rows(80)
    identity_fields = ("主体代码", "主体原名", "主体", "季度", "_capital_attribute_value", "资金属性")
    for source_row, name in ((10, "旧数据甲"), (60, "旧数据乙")):
        rows[source_row].update({"代码": "600000", "名称": name})
        for field in identity_fields:
            rows[source_row].pop(field, None)

    try:
        tab.model.update_data(rows, hydrate_latest_quotes=False)
        name_column = tab.model.headers.index("名称")
        tab.proxy_model.set_filter_state(filter_text="600000", latest_only=False)
        target_visual_row = next(
            visual_row
            for visual_row in range(tab.proxy_model.rowCount())
            if tab.proxy_model.mapToSource(tab.proxy_model.index(visual_row, 0)).row() == 60
        )
        selection_model = tab.table.selectionModel()
        selection_model.select(
            tab.proxy_model.index(target_visual_row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
        selection_model.setCurrentIndex(
            tab.proxy_model.index(target_visual_row, name_column),
            QItemSelectionModel.SelectionFlag.NoUpdate,
        )

        updated_rows = [dict(row, 名称=f"{row['名称']}-新") for row in rows]
        tab._apply_view_rows_and_finish(updated_rows, defer_finish=False)
        _drain_committer(tab)

        selected_row = tab.table.selectionModel().selectedRows()[0].row()
        assert _visible_row(tab, selected_row)["名称"] == "旧数据乙-新"
        assert _visible_row(tab, tab.table.currentIndex().row())["名称"] == "旧数据乙-新"
        assert tab.table.currentIndex().column() == name_column
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_chunked_commit_clears_stale_selection_when_holding_identity_disappears(monkeypatch):
    completed = []
    _stub_finish(monkeypatch, completed)
    tab = FundHoldingsTab(_DummyProvider(), autoload=False)
    old_rows = _view_rows(tab.VIEW_ROW_CHUNK_SIZE)
    old_rows[18].update({"主体": "旧主体", "主体原名": "旧主体", "主体代码": "old"})
    new_rows = _view_rows(80)
    new_rows[18].update({"主体": "新主体", "主体原名": "新主体", "主体代码": "new"})

    try:
        tab.model.update_data(old_rows, hydrate_latest_quotes=False)
        current_column = tab.model.headers.index("主体")
        target_visual_row = next(
            visual_row
            for visual_row in range(tab.proxy_model.rowCount())
            if tab.proxy_model.mapToSource(tab.proxy_model.index(visual_row, 0)).row() == 18
        )
        selection_model = tab.table.selectionModel()
        selection_model.select(
            tab.proxy_model.index(target_visual_row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
        selection_model.setCurrentIndex(
            tab.proxy_model.index(target_visual_row, current_column),
            QItemSelectionModel.SelectionFlag.NoUpdate,
        )

        tab._apply_view_rows_and_finish(new_rows, defer_finish=False)
        assert tab.model.rowCount() == tab.VIEW_ROW_CHUNK_SIZE
        assert tab.table.selectionModel().selectedRows()

        _drain_committer(tab)

        assert tab.table.selectionModel().selectedRows() == []
        assert not tab.table.currentIndex().isValid()
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_chunked_commit_keeps_user_selection_changed_between_batches(monkeypatch):
    completed = []
    _stub_finish(monkeypatch, completed)
    tab = FundHoldingsTab(_DummyProvider(), autoload=False)
    rows = _view_rows(96)
    old_code = "000070"
    user_code = "000010"

    try:
        tab.model.update_data(rows, hydrate_latest_quotes=False)
        code_column = tab.model.headers.index("代码")
        old_visual_row = _visible_codes(tab).index(old_code)
        tab.table.selectRow(old_visual_row)
        tab.table.selectionModel().setCurrentIndex(
            tab.proxy_model.index(old_visual_row, code_column),
            QItemSelectionModel.SelectionFlag.NoUpdate,
        )

        updated_rows = [dict(row, 名称=f"{row['名称']}-新") for row in rows]
        tab._apply_view_rows_and_finish(updated_rows, defer_finish=False)
        tab._view_committer._timer.stop()
        tab._view_committer.apply_next()

        user_visual_row = _visible_codes(tab).index(user_code)
        selection_model = tab.table.selectionModel()
        selection_model.clearSelection()
        selection_model.select(
            tab.proxy_model.index(user_visual_row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )
        selection_model.setCurrentIndex(
            tab.proxy_model.index(user_visual_row, code_column),
            QItemSelectionModel.SelectionFlag.NoUpdate,
        )

        _drain_committer(tab)

        selected_codes = [_visible_codes(tab)[index.row()] for index in selection_model.selectedRows()]
        assert selected_codes == [user_code]
        assert _visible_codes(tab)[tab.table.currentIndex().row()] == user_code
        assert tab.table.currentIndex().column() == code_column
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_shutdown_cancels_pending_fund_holdings_gui_batches(monkeypatch):
    completed = []
    _stub_finish(monkeypatch, completed)
    tab = FundHoldingsTab(_DummyProvider(), autoload=False)
    rows = _view_rows(80)

    tab._apply_view_rows_and_finish(rows, defer_finish=False)
    initial_count = tab.model.rowCount()
    tab.shutdown()
    tab._view_committer.apply_next()

    assert tab._view_committer._timer.parent() is tab._view_committer
    assert not tab._view_committer.is_active
    assert tab._view_committer.pending_count == 0
    assert tab.model.rowCount() == initial_count == tab.VIEW_ROW_CHUNK_SIZE
    assert completed == []
    tab.deleteLater()
