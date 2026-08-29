# -*- coding: utf-8 -*-
"""Table model helpers dedicated to progressive fund-holdings commits."""

from __future__ import annotations

from PyQt6.QtCore import QItemSelectionModel, QObject, Qt, QTimer

from app.services.ui_diagnostics_service import ui_stall_span
from ui.models.table_models import StockTableModel


class FundHoldingsTableModel(StockTableModel):
    """Stock model that can expose already-built rows in bounded GUI batches."""


def build_fund_holdings_view_metadata(payload: dict) -> dict:
    return {
        "_latest_quarter_map": dict(payload.get("latest_quarter_map") or {}),
        "_latest_sync_map": dict(payload.get("latest_sync_map") or {}),
        "_concept_sector_cache": dict(payload.get("concept_sector_cache") or {}),
        "_loaded_quarter_scope": str(payload.get("loaded_quarter_scope") or "").strip(),
        "_loaded_quarter_keys": {
            str(quarter_key or "").strip()
            for quarter_key in (payload.get("loaded_quarter_keys") or [])
            if str(quarter_key or "").strip()
        },
    }


def apply_fund_holdings_view_rows(
    tab,
    view_rows: list[dict],
    *,
    defer_finish: bool,
    generation: int | None = None,
    view_metadata: dict | None = None,
) -> None:
    if getattr(tab, "_runtime_cleanup_done", False):
        return
    view_committer = getattr(tab, "_view_committer", None)
    if view_committer is not None and view_committer.should_chunk(view_rows):
        view_committer.start(view_rows, generation=generation, view_metadata=view_metadata)
        return

    if view_committer is not None:
        view_committer.cancel()
    with ui_stall_span(
        "FundHoldingsTab._apply_view_rows_and_finish",
        tab="fund_holdings",
        signal="deferred" if defer_finish else "sync",
    ):
        tab.model.update_data(view_rows, hydrate_latest_quotes=False)
    _apply_view_metadata(tab, view_metadata)
    _schedule_view_finish(
        tab,
        view_rows,
        defer_finish=False,
        generation=generation,
    )


def _schedule_view_finish(
    tab,
    view_rows: list[dict],
    *,
    defer_finish: bool,
    generation: int | None = None,
) -> None:
    def finish(rows=view_rows):
        if getattr(tab, "_runtime_cleanup_done", False):
            return
        if generation is not None and generation != int(getattr(tab, "_view_load_generation", 0)):
            return
        skip_empty_state = _finish_view_payload(tab, rows)
        if not skip_empty_state:
            _show_empty_view_payload_if_needed(tab, rows)

    if defer_finish:
        QTimer.singleShot(0, finish)
    else:
        finish()


def _finish_view_payload(tab, rows: list[dict]) -> bool:
    handler = getattr(tab, "_finish_apply_view_payload", None)
    if callable(handler):
        return bool(handler(rows))
    from ui.tabs.fund_holdings_tab import FundHoldingsTab

    return bool(FundHoldingsTab._finish_apply_view_payload(tab, rows))


def _show_empty_view_payload_if_needed(tab, rows: list[dict]) -> None:
    handler = getattr(tab, "_show_empty_view_payload_if_needed", None)
    if callable(handler):
        handler(rows)
        return
    from ui.tabs.fund_holdings_tab import FundHoldingsTab

    FundHoldingsTab._show_empty_view_payload_if_needed(tab, rows)


_VIEW_METADATA_FIELDS = (
    "_latest_quarter_map",
    "_latest_sync_map",
    "_concept_sector_cache",
    "_loaded_quarter_scope",
    "_loaded_quarter_keys",
)


def _apply_view_metadata(tab, metadata: dict | None) -> None:
    for field in _VIEW_METADATA_FIELDS:
        if metadata is not None and field in metadata:
            setattr(tab, field, metadata[field])


def _view_code_column(tab) -> int:
    try:
        return tab.model.headers.index("代码")
    except AttributeError, ValueError:
        return -1


def _code_for_row(model, row: int, code_column: int) -> str:
    if model is None or row < 0 or code_column < 0:
        return ""
    return str(model.data(model.index(row, code_column), Qt.ItemDataRole.DisplayRole) or "").strip()


def _normalized_row_value(row: dict, *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _row_identity(row: dict | None, *, fallback_code: str = "") -> tuple[str, ...]:
    row = row if isinstance(row, dict) else {}
    return (
        _normalized_row_value(row, "代码") or fallback_code,
        _normalized_row_value(row, "主体代码"),
        _normalized_row_value(row, "主体原名", "主体"),
        _normalized_row_value(row, "季度"),
        _normalized_row_value(row, "_capital_attribute_value", "资金属性"),
    )


def _model_row_dict(model, row: int) -> dict | None:
    getter = getattr(model, "get_row_data", None)
    if callable(getter):
        value = getter(row)
        return value if isinstance(value, dict) else None
    rows = getattr(model, "row_data", None)
    if rows is not None and 0 <= row < len(rows):
        value = rows[row]
        return value if isinstance(value, dict) else None
    return None


def _source_model_and_row(model, view_row: int):
    source_model_getter = getattr(model, "sourceModel", None)
    map_to_source = getattr(model, "mapToSource", None)
    if callable(source_model_getter) and callable(map_to_source):
        source_model = source_model_getter()
        source_index = map_to_source(model.index(view_row, 0))
        if source_model is not None and source_index.isValid():
            return source_model, source_index.row()
    return model, view_row


def _source_row_tokens(model, code_column: int) -> dict[int, tuple[tuple[str, ...], int]]:
    source_model_getter = getattr(model, "sourceModel", None)
    source_model = source_model_getter() if callable(source_model_getter) else model
    source_model = source_model or model
    occurrences: dict[tuple[str, ...], int] = {}
    tokens = {}
    for source_row in range(source_model.rowCount()):
        fallback_code = _code_for_row(source_model, source_row, code_column)
        identity = _row_identity(_model_row_dict(source_model, source_row), fallback_code=fallback_code)
        occurrence = occurrences.get(identity, 0)
        occurrences[identity] = occurrence + 1
        tokens[source_row] = (identity, occurrence)
    return tokens


def _view_row_tokens(model, code_column: int) -> dict[int, tuple[tuple[str, ...], int]]:
    source_tokens = _source_row_tokens(model, code_column)
    result = {}
    for view_row in range(model.rowCount()):
        _, source_row = _source_model_and_row(model, view_row)
        token = source_tokens.get(source_row)
        if token is not None:
            result[view_row] = token
    return result


def _selected_view_tokens(table, row_tokens: dict[int, tuple[tuple[str, ...], int]]) -> list[tuple]:
    selection_model = table.selectionModel()
    if selection_model is None:
        return []
    return [row_tokens[index.row()] for index in selection_model.selectedRows() if index.row() in row_tokens]


def _current_view_selection(table, row_tokens: dict[int, tuple[tuple[str, ...], int]]) -> tuple[tuple | None, int]:
    current_index = table.currentIndex()
    if not current_index.isValid():
        return None, 0
    return row_tokens.get(current_index.row()), current_index.column()


def _capture_view_selection(tab) -> dict | None:
    table = getattr(tab, "table", None)
    if table is None:
        return None
    model = table.model()
    code_column = _view_code_column(tab)
    if model is None:
        return None
    if code_column < 0:
        return None

    row_tokens = _view_row_tokens(model, code_column)
    selected_tokens = _selected_view_tokens(table, row_tokens)
    current_token, current_column = _current_view_selection(table, row_tokens)
    if not selected_tokens and current_token is None:
        return None
    return {
        "selected_tokens": selected_tokens,
        "current_token": current_token,
        "current_column": current_column,
    }


def _restore_selected_rows(selection_model, model, rows: list[int]) -> None:
    selection_model.clearSelection()
    if not rows:
        return
    for row in rows:
        selection_model.select(
            model.index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )


def _view_selection_context(tab):
    table = getattr(tab, "table", None)
    if table is None:
        return None
    model = table.model()
    if model is None:
        return None
    selection_model = table.selectionModel()
    if selection_model is None:
        return None
    code_column = _view_code_column(tab)
    if code_column < 0:
        return None
    return table, model, selection_model, code_column


def _restore_current_view_index(table, model, rows_by_token: dict[tuple, int], snapshot: dict) -> None:
    current_row = rows_by_token.get(snapshot.get("current_token"))
    selection_model = table.selectionModel()
    if selection_model is None:
        return
    if current_row is None:
        selection_model.clearCurrentIndex()
        return
    current_column = min(max(0, int(snapshot.get("current_column", 0) or 0)), model.columnCount() - 1)
    selection_model.setCurrentIndex(
        model.index(current_row, current_column),
        QItemSelectionModel.SelectionFlag.NoUpdate,
    )


def _restore_view_selection(tab, snapshot: dict | None) -> None:
    if not snapshot or getattr(tab, "_runtime_cleanup_done", False):
        return
    context = _view_selection_context(tab)
    if context is None:
        return
    table, model, selection_model, code_column = context

    rows_by_token = {token: row for row, token in _view_row_tokens(model, code_column).items()}
    selected_rows = [rows_by_token[token] for token in snapshot.get("selected_tokens", []) if token in rows_by_token]
    _restore_selected_rows(selection_model, model, selected_rows)
    _restore_current_view_index(table, model, rows_by_token, snapshot)


class FundHoldingsViewCommitter(QObject):
    """Commit a prepared fund-holdings payload in bounded GUI-thread slices."""

    def __init__(self, tab, *, chunk_size: int):
        super().__init__(tab)
        self._tab = tab
        self._chunk_size = max(1, int(chunk_size))
        self._pending_rows: list[dict] = []
        self._finish_rows: list[dict] | None = None
        self._generation: int | None = None
        self._previous_rows: list[dict] | None = None
        self._view_metadata: dict | None = None
        self._selection: dict | None = None
        self._selection_baseline: dict | None = None
        self._paused = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.apply_next)

    @property
    def pending_count(self) -> int:
        return len(self._pending_rows)

    @property
    def is_active(self) -> bool:
        return self._finish_rows is not None

    @property
    def is_paused(self) -> bool:
        return bool(self._paused)

    def should_chunk(self, rows) -> bool:
        return len(rows) > self._chunk_size and callable(getattr(self._tab.model, "append_rows", None))

    def start(
        self,
        rows,
        *,
        generation: int | None = None,
        view_metadata: dict | None = None,
    ) -> None:
        previous_rows = self._previous_rows
        previous_selection = self._selection
        if previous_rows is None:
            previous_rows = list(self._tab.model.row_data)
            previous_selection = _capture_view_selection(self._tab)
        self.cancel()
        view_rows = list(rows)
        self._generation = generation
        self._previous_rows = previous_rows
        self._view_metadata = view_metadata
        self._selection = previous_selection
        self._finish_rows = view_rows
        self._pending_rows = view_rows[self._chunk_size :]
        with ui_stall_span(
            "FundHoldingsTab._apply_view_rows_and_finish",
            tab="fund_holdings",
            signal=f"chunk:1/{len(view_rows)}",
        ):
            self._tab.model.update_data(
                view_rows[: self._chunk_size],
                hydrate_latest_quotes=True,
                record_flash=False,
            )
        self._selection_baseline = _capture_view_selection(self._tab)
        self._timer.start()

    def apply_next(self) -> None:
        if getattr(self._tab, "_runtime_cleanup_done", False):
            self.cancel()
            return
        if self._generation is not None and self._generation != int(getattr(self._tab, "_view_load_generation", 0)):
            self.cancel(restore_previous=True)
            return
        if self._paused:
            return

        chunk = self._pending_rows[: self._chunk_size]
        del self._pending_rows[: self._chunk_size]
        if chunk:
            with ui_stall_span(
                "FundHoldingsTab._apply_view_row_chunk",
                tab="fund_holdings",
                signal=f"rows:{len(chunk)}",
            ):
                self._tab.model.append_rows(chunk)

        if self._pending_rows:
            self._timer.start()
        else:
            self._complete()

    def pause(self) -> bool:
        """Freeze a hidden batch commit without dropping its pending rows."""
        if not self.is_active:
            return False
        self._paused = True
        self._timer.stop()
        return True

    def resume(self) -> bool:
        """Continue a previously paused batch from the exact next row."""
        if not self._paused:
            return False
        self._paused = False
        if self.is_active and not getattr(self._tab, "_runtime_cleanup_done", False):
            self._timer.start()
        return True

    def _complete(self) -> None:
        view_rows = self._finish_rows
        view_metadata = self._view_metadata
        selection = self._selection
        selection_changed = _capture_view_selection(self._tab) != self._selection_baseline
        self._finish_rows = None
        self._generation = None
        self._previous_rows = None
        self._view_metadata = None
        self._selection = None
        self._selection_baseline = None
        if view_rows is None or getattr(self._tab, "_runtime_cleanup_done", False):
            return

        _apply_view_metadata(self._tab, view_metadata)
        skip_empty_state = self._tab._finish_apply_view_payload(view_rows)
        if not skip_empty_state:
            self._tab._show_empty_view_payload_if_needed(view_rows)
        if not selection_changed:
            _restore_view_selection(self._tab, selection)

    def cancel(self, *, restore_previous: bool = False) -> None:
        previous_rows = self._previous_rows
        previous_selection = self._selection
        self._timer.stop()
        self._paused = False
        self._pending_rows = []
        self._finish_rows = None
        self._generation = None
        self._previous_rows = None
        self._view_metadata = None
        self._selection = None
        self._selection_baseline = None
        if restore_previous and previous_rows is not None and not getattr(self._tab, "_runtime_cleanup_done", False):
            self._tab.model.update_data(previous_rows, hydrate_latest_quotes=True, record_flash=False)
            _restore_view_selection(self._tab, previous_selection)


__all__ = [
    "FundHoldingsTableModel",
    "FundHoldingsViewCommitter",
    "apply_fund_holdings_view_rows",
    "build_fund_holdings_view_metadata",
]
