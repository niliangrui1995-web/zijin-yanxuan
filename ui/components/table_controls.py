"""Table-oriented widgets shared by the UI package."""

import logging
import math
import time
from contextlib import suppress

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QPersistentModelIndex,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPolygonF,
    QRegion,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.components.tooltip_popup import hide_floating_tooltip, show_floating_tooltip
from ui.models.table_model_helpers import FLASH_DURATION_SECONDS
from ui.theme_tokens import build_ui_tokens, get_state_tone

from .motion import install_button_feedback, install_menu_fade
from .table_view_helpers import bounded_model_row, find_header_column

log = logging.getLogger(__name__)

_PAINT_REASON_PRIORITY = {
    "other": 0,
    "flash_expiry": 1,
    "model_data_changed": 2,
    "quote_data_changed": 3,
    "preload_reveal": 4,
    "model_layout_changed": 5,
    "model_reset": 6,
}
_STRUCTURAL_PAINT_REASONS = frozenset(
    {
        "model_layout_changed",
        "model_reset",
        "preload_reveal",
    }
)


def _paint_region_metrics(region: QRegion, viewport_rect) -> tuple[float, int, bool]:
    """Return bounding span plus whether the region actually covers the viewport."""
    viewport_area = max(1, viewport_rect.width() * viewport_rect.height())
    bounds = region.boundingRect()
    bounding_area = max(0, bounds.width() * bounds.height())
    viewport_region = QRegion(viewport_rect)
    covers_viewport = bool(
        not viewport_rect.isEmpty() and viewport_region.subtracted(region).isEmpty()
    )
    return min(1.0, bounding_area / viewport_area), int(region.rectCount()), covers_viewport


def _model_has_rows(model) -> bool:
    try:
        return int(model.rowCount()) > 0
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _selected_table_rows(table) -> tuple[list[int], list[str]]:
    selection_model = table.selectionModel()
    if selection_model is None:
        return [], []
    try:
        rows = [index.row() for index in selection_model.selectedRows()]
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return [], []
    codes = [code for row in rows if (code := table._row_identity(row))]
    return rows, codes


def _model_sort_state(model) -> tuple[int, Qt.SortOrder]:
    if not hasattr(model, "sortColumn"):
        return -1, Qt.SortOrder.AscendingOrder
    try:
        return int(model.sortColumn()), model.sortOrder()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return -1, Qt.SortOrder.AscendingOrder


def _stop_state_animation(wrapper) -> None:
    animation = wrapper._state_animation
    if animation is None:
        return
    try:
        effect = animation.targetObject()
        animation.stop()
        effect_owner = effect.parent() if effect is not None else None
        if isinstance(effect_owner, QWidget) and effect_owner.graphicsEffect() is effect:
            effect_owner.setGraphicsEffect(None)
    except (AttributeError, RuntimeError):
        pass
    wrapper._state_animation = None


def _is_quote_metadata_tooltip(tooltip_text: str) -> bool:
    return "报价时间：" in tooltip_text or "新鲜度：" in tooltip_text


def _is_elided_table_cell(table, index) -> bool:
    display_text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
    if not display_text:
        return False
    fm = QFontMetrics(table._display_font_for_index(index))
    visible_rect = table.visualRect(index)
    available_width = table.columnWidth(index.column())
    if visible_rect.isValid() and visible_rect.width() > 0:
        available_width = min(available_width, visible_rect.width())
    available_width = max(0, available_width - 14)
    if available_width <= 0:
        return False
    pill_color = index.data(Qt.ItemDataRole.UserRole + 2)
    required_width = fm.horizontalAdvance(display_text) + (12 if pill_color else 0)
    return required_width > available_width


class VCPTableView(QTableView):
    """
    紫金研选统一表格组件 (VCPTableView)
    """

    SHELL_NAV_REPAINT_GUARD_ARM_MS = 750
    SHELL_NAV_REPAINT_GUARD_ACTIVE_MS = 750
    # Watchlist treats this as an observability threshold because Qt can deliver
    # more than two redundant full paints inside one bounded native burst.
    # LHB retains its existing post-budget fail-open behavior.
    SHELL_NAV_REPAINT_GUARD_MAX_SUPPRESSIONS = 2
    NATIVE_WINDOW_PROVENANCE_MAX_AGE_MS = 10_000
    # The guard below is intentionally much narrower than provenance retention:
    # it applies only to the immediate WindowDeactivate -> UpdateRequest burst
    # reproduced while opening an independent K-line window.
    NATIVE_WINDOW_DEACTIVATE_GUARD_MAX_AGE_MS = 250
    _NATIVE_WINDOW_EVENT_SIGNALS = {
        QEvent.Type.WindowActivate: "window_activate",
        QEvent.Type.WindowDeactivate: "window_deactivate",
        QEvent.Type.ActivationChange: "activation_change",
        QEvent.Type.Expose: "window_expose",
        QEvent.Type.UpdateRequest: "window_update_request",
        QEvent.Type.UpdateLater: "window_update_later",
        QEvent.Type.LayoutRequest: "window_layout_request",
        QEvent.Type.Resize: "window_resize",
        QEvent.Type.Show: "window_show",
        QEvent.Type.Hide: "window_hide",
        QEvent.Type.WindowStateChange: "window_state_change",
        QEvent.Type.StyleChange: "window_style_change",
        QEvent.Type.PaletteChange: "window_palette_change",
    }
    _NATIVE_WINDOW_STRUCTURAL_SIGNALS = frozenset(
        {
            "window_activate",
            "window_expose",
            "window_layout_request",
            "window_resize",
            "window_show",
            "window_hide",
            "window_state_change",
            "window_style_change",
            "window_palette_change",
        }
    )

    def __init__(self, parent=None, default_row_height: int = None):
        super().__init__(parent)
        self._base_row_height = None
        self._refresh_state_snapshot = None
        self._pending_refresh_state_restore = None
        self._pending_scrollbar_restore = None
        self._restoring_refresh_state = False
        self._bound_refresh_model = None
        self._flash_repaint_until = 0.0
        self._coalesced_flash_repaint = False
        self._targeted_flash_repaint = False
        self._paint_metric_scope = ""
        self._pending_paint_metric: dict[str, object] | None = None
        self._flash_repaint_scheduled_at = 0.0
        self._flash_dirty_indexes: set[QPersistentModelIndex] = set()
        self._shell_nav_repaint_guard: dict[str, object] | None = None
        self._shell_nav_guard_selection_model = None
        self._native_window_event_source = None
        self._native_window_last_event: dict[str, object] | None = None
        self._native_window_paint_event: dict[str, object] | None = None
        self._native_window_inactive = False
        self._native_window_requires_full_paint = False
        self._closing = False
        self._flash_repaint_timer = QTimer(self)
        self._flash_repaint_timer.setInterval(60)
        self._flash_repaint_timer.timeout.connect(self._tick_flash_repaint)
        self._ambient_repaint_timer = QTimer(self)
        self._ambient_repaint_timer.setInterval(120)
        self._ambient_repaint_timer.timeout.connect(self.viewport().update)
        self._refresh_state_restore_timer = QTimer(self)
        self._refresh_state_restore_timer.setSingleShot(True)
        self._refresh_state_restore_timer.timeout.connect(self._restore_pending_refresh_state)
        self._scrollbar_restore_timer = QTimer(self)
        self._scrollbar_restore_timer.setSingleShot(True)
        self._scrollbar_restore_timer.timeout.connect(self._restore_pending_scrollbars)
        self._init_common_styles(default_row_height)
        from ui.theme import theme_manager

        self._theme_manager = theme_manager
        self._theme_manager.sig_theme_changed.connect(self._on_theme_changed)

    def _init_common_styles(self, default_row_height: int):
        header = self.horizontalHeader()
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSortingEnabled(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setCornerButtonEnabled(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._sorted_column = -1
        header.setHighlightSections(False)
        header.setSectionsClickable(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.sortIndicatorChanged.connect(self._on_sort_indicator_changed)
        for guarded_header in (header, self.verticalHeader()):
            with suppress(TypeError, RuntimeError):
                guarded_header.sectionResized.connect(self._on_shell_nav_header_geometry_changed)
                guarded_header.sectionMoved.connect(self._on_shell_nav_header_geometry_changed)
        for scrollbar in (self.horizontalScrollBar(), self.verticalScrollBar()):
            with suppress(TypeError, RuntimeError):
                scrollbar.valueChanged.connect(self._on_shell_nav_scroll_changed)
        self._apply_screen_width_limit()

        if default_row_height is None:
            default_row_height = self.verticalHeader().defaultSectionSize()
        self._base_row_height = default_row_height
        self.apply_density()

    def _screen_width_limit(self) -> int:
        screen = self.screen()
        if screen is None:
            window = self.window()
            handle = window.windowHandle() if window is not None else None
            screen = handle.screen() if handle is not None else None
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return 1920
        return max(320, screen.availableGeometry().width())

    def _apply_screen_width_limit(self):
        max_w = self._screen_width_limit()
        changed = False
        header = self.horizontalHeader()
        if header.maximumSectionSize() != max_w:
            header.setMaximumSectionSize(max_w)
            changed = True
        if self.maximumWidth() != max_w:
            self.setMaximumWidth(max_w)
            changed = True
        if changed:
            self.updateGeometry()

    def _on_sort_indicator_changed(self, column: int, _order):
        if not (self._restoring_refresh_state and self._paint_metric_scope == "lhb"):
            self._invalidate_shell_nav_repaint_guard("sort_changed")
        self._sorted_column = column
        self.viewport().update()

    def _on_shell_nav_header_geometry_changed(self, *_args) -> None:
        if self._restoring_refresh_state and self._paint_metric_scope == "lhb":
            return
        self._invalidate_shell_nav_repaint_guard("header_geometry")

    def _on_shell_nav_scroll_changed(self, *_args) -> None:
        if self._restoring_refresh_state and self._paint_metric_scope == "lhb":
            return
        self._invalidate_shell_nav_repaint_guard("scroll_changed")

    def sorted_column(self) -> int:
        return self._sorted_column

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(min(hint.width(), self._screen_width_limit()), hint.height())

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), self._screen_width_limit()), hint.height())

    def apply_density(self, mode: str | None = None):
        self._invalidate_shell_nav_repaint_guard("density_changed")
        tokens = build_ui_tokens(density=mode)
        base_height = self._base_row_height or self.verticalHeader().defaultSectionSize()
        comfort_height = max(base_height, tokens["table"]["row_height_base"])
        if tokens["density"] == "紧凑":
            row_height = max(20, comfort_height - tokens["table"]["row_height_delta"])
        else:
            row_height = comfort_height
        self.verticalHeader().setDefaultSectionSize(row_height)
        self.horizontalHeader().setMinimumHeight(tokens["table"]["header_min_height"])

    def _on_theme_changed(self, _theme_name: str):
        if self._closing:
            return
        self._invalidate_shell_nav_repaint_guard("theme_changed")
        self._clear_model_presentation_cache()
        self._apply_screen_width_limit()
        self.style().unpolish(self)
        self.style().polish(self)
        self.sync_viewport_base_background()
        self.viewport().update()

    def set_viewport_base_background_enabled(self, enabled: bool) -> None:
        """Opt in to a palette-backed viewport background for this table only."""
        enabled = bool(enabled)
        self.setProperty("vcpViewportBaseBackground", enabled)
        viewport = self.viewport()
        if viewport is None:
            return
        if not enabled:
            viewport.setAutoFillBackground(False)
            return
        self.sync_viewport_base_background()

    def sync_viewport_base_background(self) -> None:
        if not bool(self.property("vcpViewportBaseBackground")):
            return
        viewport = self.viewport()
        if viewport is None:
            return
        viewport.setBackgroundRole(QPalette.ColorRole.Base)
        viewport.setAutoFillBackground(True)

    def setModel(self, model):
        self._invalidate_shell_nav_repaint_guard("model_changed")
        self._disconnect_shell_nav_guard_selection_model()
        self._disconnect_refresh_model()
        super().setModel(model)
        self._connect_refresh_model(model)
        self._connect_shell_nav_guard_selection_model()

    def _clear_model_presentation_cache(self) -> None:
        model = self.model()
        seen: set[int] = set()
        while model is not None and id(model) not in seen:
            seen.add(id(model))
            clear_cache = getattr(model, "clear_presentation_cache", None)
            if callable(clear_cache):
                clear_cache()
            source_model = getattr(model, "sourceModel", None)
            model = source_model() if callable(source_model) else None

    def _connect_shell_nav_guard_selection_model(self) -> None:
        selection_model = self.selectionModel()
        if selection_model is None:
            return
        self._shell_nav_guard_selection_model = selection_model
        for signal_name in ("selectionChanged", "currentChanged"):
            signal = getattr(selection_model, signal_name, None)
            if signal is None:
                continue
            with suppress(TypeError, RuntimeError):
                signal.connect(self._on_shell_nav_guard_selection_changed)

    def _disconnect_shell_nav_guard_selection_model(self) -> None:
        selection_model = self._shell_nav_guard_selection_model
        if selection_model is None:
            return
        for signal_name in ("selectionChanged", "currentChanged"):
            signal = getattr(selection_model, signal_name, None)
            if signal is None:
                continue
            with suppress(TypeError, RuntimeError):
                signal.disconnect(self._on_shell_nav_guard_selection_changed)
        self._shell_nav_guard_selection_model = None

    def _on_shell_nav_guard_selection_changed(self, *_args) -> None:
        if self._restoring_refresh_state and self._paint_metric_scope == "lhb":
            return
        self._invalidate_shell_nav_repaint_guard("selection_changed")

    def _connect_refresh_model(self, model) -> None:
        if model is None:
            return
        self._bound_refresh_model = model
        for signal_name, slot in (
            ("modelAboutToBeReset", self._capture_refresh_state),
            ("layoutAboutToBeChanged", self._capture_refresh_state),
            ("modelReset", self._on_model_reset),
            ("layoutChanged", self._on_model_layout_changed),
            ("dataChanged", self._on_model_data_changed),
        ):
            signal = getattr(model, signal_name, None)
            if signal is None:
                continue
            with suppress(TypeError, RuntimeError):
                signal.connect(slot)

    def _disconnect_refresh_model(self) -> None:
        model = self._bound_refresh_model
        if model is None:
            return
        for signal_name, slot in (
            ("modelAboutToBeReset", self._capture_refresh_state),
            ("layoutAboutToBeChanged", self._capture_refresh_state),
            ("modelReset", self._on_model_reset),
            ("layoutChanged", self._on_model_layout_changed),
            ("dataChanged", self._on_model_data_changed),
        ):
            signal = getattr(model, signal_name, None)
            if signal is None:
                continue
            with suppress(TypeError, RuntimeError):
                signal.disconnect(slot)
        self._bound_refresh_model = None

    def _row_identity(self, row: int) -> str:
        model = self.model()
        code_column = find_header_column(model, "代码")
        if model is None or code_column < 0 or row < 0:
            return ""
        try:
            index = model.index(row, code_column)
            return str(model.data(index, Qt.ItemDataRole.DisplayRole) or "").strip()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return ""

    def _find_row_by_identity(self, identity: str) -> int:
        identity_text = str(identity or "").strip()
        if not identity_text:
            return -1
        model = self.model()
        if model is None:
            return -1
        try:
            row_count = int(model.rowCount())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return -1
        for row in range(row_count):
            if self._row_identity(row) == identity_text:
                return row
        return -1

    def _capture_refresh_state(self, *_args) -> None:
        if self._restoring_refresh_state:
            return
        model = self.model()
        if model is None or not _model_has_rows(model):
            self._refresh_state_snapshot = None
            return

        current = self.currentIndex()
        selected_rows, selected_codes = _selected_table_rows(self)
        header = self.horizontalHeader()
        proxy_sort_column, proxy_sort_order = _model_sort_state(model)

        self._refresh_state_snapshot = {
            "v_scroll": self.verticalScrollBar().value(),
            "h_scroll": self.horizontalScrollBar().value(),
            "current_row": current.row() if current.isValid() else -1,
            "current_col": current.column() if current.isValid() else 0,
            "current_code": self._row_identity(current.row()) if current.isValid() else "",
            "selected_rows": selected_rows,
            "selected_codes": selected_codes,
            "header_state": header.saveState(),
            "sort_column": header.sortIndicatorSection(),
            "sort_order": header.sortIndicatorOrder(),
            "proxy_sort_column": proxy_sort_column,
            "proxy_sort_order": proxy_sort_order,
        }

    def _schedule_refresh_state_restore(self, *_args) -> None:
        if self._restoring_refresh_state:
            return
        snapshot = self._refresh_state_snapshot
        if not snapshot:
            return
        self._pending_refresh_state_restore = dict(snapshot)
        self._refresh_state_restore_timer.start(0)

    def _model_row_count(self) -> int:
        model = self.model()
        try:
            return max(0, int(model.rowCount())) if model is not None else 0
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return 0

    def _on_model_reset(self, *_args) -> None:
        self._invalidate_shell_nav_repaint_guard("model_reset")
        self._mark_pending_paint_metric("model_reset", model_rows=self._model_row_count())
        self._schedule_refresh_state_restore(*_args)

    def _on_model_layout_changed(self, *_args) -> None:
        self._invalidate_shell_nav_repaint_guard("model_layout_changed")
        self._mark_pending_paint_metric("model_layout_changed", model_rows=self._model_row_count())
        self._schedule_refresh_state_restore(*_args)

    def _restore_pending_refresh_state(self) -> None:
        snapshot = self._pending_refresh_state_restore
        self._pending_refresh_state_restore = None
        if snapshot:
            self._restore_refresh_state(snapshot)

    def _restore_refresh_state(self, snapshot: dict) -> None:
        if self.model() is None:
            return

        self._restoring_refresh_state = True
        try:
            v_scroll = int(snapshot.get("v_scroll", 0) or 0)
            h_scroll = int(snapshot.get("h_scroll", 0) or 0)
            header = self.horizontalHeader()
            header_state = snapshot.get("header_state")
            if header_state is not None:
                with suppress(AttributeError, RuntimeError, TypeError, ValueError):
                    if header.saveState() != header_state:
                        header.restoreState(header_state)

            sort_column = int(snapshot.get("proxy_sort_column", snapshot.get("sort_column", -1)) or -1)
            sort_order = snapshot.get("proxy_sort_order", snapshot.get("sort_order", Qt.SortOrder.AscendingOrder))
            if sort_column >= 0:
                with suppress(AttributeError, RuntimeError, TypeError, ValueError):
                    current_sort_column, current_sort_order = _model_sort_state(self.model())
                    if current_sort_column != sort_column or current_sort_order != sort_order:
                        self.sortByColumn(sort_column, sort_order)

            selection_model = self.selectionModel()
            if selection_model is not None:
                with suppress(AttributeError, RuntimeError):
                    selection_model.clearSelection()

            restored_rows = []
            for code in snapshot.get("selected_codes", []) or []:
                row = self._find_row_by_identity(code)
                if row >= 0 and row not in restored_rows:
                    restored_rows.append(row)
            if not restored_rows:
                restored_rows = [
                    bounded_model_row(self.model(), row)
                    for row in (snapshot.get("selected_rows", []) or [])
                    if bounded_model_row(self.model(), row) >= 0
                ]

            current_row = self._find_row_by_identity(snapshot.get("current_code", ""))
            if current_row < 0:
                current_row = bounded_model_row(self.model(), int(snapshot.get("current_row", -1) or -1))
            current_col = max(0, int(snapshot.get("current_col", 0) or 0))

            if selection_model is not None:
                for row in restored_rows:
                    index = self.model().index(row, 0)
                    selection_model.select(
                        index,
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                    )

            if current_row >= 0:
                current_index = self.model().index(
                    current_row, min(current_col, max(0, self.model().columnCount() - 1))
                )
                self.setCurrentIndex(current_index)

            self._restore_scrollbars(v_scroll, h_scroll)
            self._pending_scrollbar_restore = (v_scroll, h_scroll)
            self._scrollbar_restore_timer.start(0)
        finally:
            self._restoring_refresh_state = False
            self._refresh_state_snapshot = None

    def _restore_pending_scrollbars(self) -> None:
        pending = self._pending_scrollbar_restore
        self._pending_scrollbar_restore = None
        if not pending:
            return
        v_scroll, h_scroll = pending
        self._restore_scrollbars(v_scroll, h_scroll)

    def _restore_scrollbars(self, v_scroll: int, h_scroll: int) -> None:
        if self.model() is None:
            return
        self.verticalScrollBar().setValue(v_scroll)
        self.horizontalScrollBar().setValue(h_scroll)

    def _stop_deferred_restores(self) -> None:
        self._closing = True
        for timer in (
            getattr(self, "_refresh_state_restore_timer", None),
            getattr(self, "_scrollbar_restore_timer", None),
            getattr(self, "_flash_repaint_timer", None),
            getattr(self, "_ambient_repaint_timer", None),
        ):
            if timer is not None:
                timer.stop()
        self._pending_refresh_state_restore = None
        self._pending_scrollbar_restore = None
        self._pending_paint_metric = None
        self._flash_repaint_scheduled_at = 0.0
        self._flash_dirty_indexes.clear()
        self._clear_shell_nav_repaint_guard()
        self._remove_native_window_event_filter()
        self._disconnect_shell_nav_guard_selection_model()
        self._disconnect_refresh_model()
        hide_floating_tooltip()
        with suppress(AttributeError, TypeError, RuntimeError):
            self._theme_manager.sig_theme_changed.disconnect(self._on_theme_changed)

    def closeEvent(self, event):
        self._stop_deferred_restores()
        super().closeEvent(event)

    def deleteLater(self):
        self._stop_deferred_restores()
        super().deleteLater()

    def _on_model_data_changed(self, *_args) -> None:
        if self._closing:
            return
        top_left = _args[0] if len(_args) >= 1 else None
        bottom_right = _args[1] if len(_args) >= 2 else None
        self._remember_shell_nav_repaint_dirty_region(top_left, bottom_right)
        roles = _args[2] if len(_args) >= 3 else None
        flash_role = int(Qt.ItemDataRole.UserRole) + 1
        includes_flash_role = True
        if roles:
            role_values = {int(getattr(role, "value", role)) for role in roles}
            includes_flash_role = flash_role in role_values
        reason = "model_data_changed"
        metadata = {}
        if len(_args) >= 2:
            quote_changed = self._data_change_includes_quote_columns(_args[0], _args[1])
            reason = "quote_data_changed" if quote_changed else "model_data_changed"
            changed_rows = abs(_args[1].row() - _args[0].row()) + 1
            changed_columns = abs(_args[1].column() - _args[0].column()) + 1
            changed_indexes = changed_rows * changed_columns
            update_threshold = 0
            threshold_getter = getattr(self, "updateThreshold", None)
            if callable(threshold_getter):
                with suppress(RuntimeError, TypeError, ValueError):
                    update_threshold = max(0, int(threshold_getter()))
            metadata = {
                "changed_rows": changed_rows,
                "changed_columns": changed_columns,
                "changed_indexes": changed_indexes,
                "update_threshold": update_threshold,
                "threshold_exceeded": str(changed_indexes > update_threshold).lower(),
                "includes_flash_role": str(includes_flash_role).lower(),
            }
            self._mark_pending_paint_metric(reason, **metadata)
        if not includes_flash_role:
            return
        if not self._model_has_active_flash_records():
            return
        if self._coalesced_flash_repaint and not self.isVisible():
            self._pending_paint_metric = None
            self._flash_repaint_scheduled_at = 0.0
            self._flash_dirty_indexes.clear()
            return
        if len(_args) >= 2:
            if self._targeted_flash_repaint:
                metadata["dirty_cells"] = self._remember_flash_dirty_indexes(_args[0], _args[1])
            self._mark_pending_paint_metric(reason, **metadata)
        self.schedule_flash_repaint_until(time.time() + FLASH_DURATION_SECONDS)

    def _model_has_active_flash_records(self) -> bool:
        model = self.model()
        visited: set[int] = set()
        while model is not None and id(model) not in visited:
            visited.add(id(model))
            flash_records = getattr(model, "_flash_records", None)
            if isinstance(flash_records, dict) and any(bool(cells) for cells in flash_records.values()):
                return True
            source_model = getattr(model, "sourceModel", None)
            model = source_model() if callable(source_model) else None
        return False

    def set_coalesced_flash_repaint_enabled(self, enabled: bool) -> None:
        self._coalesced_flash_repaint = bool(enabled)
        self._flash_repaint_timer.stop()
        self._flash_repaint_timer.setSingleShot(self._coalesced_flash_repaint)
        interval_ms = max(1, int(FLASH_DURATION_SECONDS * 1000)) if self._coalesced_flash_repaint else 60
        self._flash_repaint_timer.setInterval(interval_ms)

    def set_targeted_flash_repaint_enabled(self, enabled: bool, *, metric_scope: str = "") -> None:
        self._targeted_flash_repaint = bool(enabled)
        self._paint_metric_scope = str(metric_scope or "").strip()
        self._pending_paint_metric = None
        self._flash_repaint_scheduled_at = 0.0
        self._flash_dirty_indexes.clear()
        if self._paint_metric_scope != "watchlist":
            self._remove_native_window_event_filter()
        elif self.isVisible():
            self._native_window_requires_full_paint = True
            self._native_window_inactive = False
            self._native_window_paint_event = None
            self._native_window_last_event = None
            self._install_native_window_event_filter()

    def _install_native_window_event_filter(self) -> None:
        source = self.window()
        if source is self._native_window_event_source:
            return
        self._remove_native_window_event_filter()
        if source is None or source is self:
            return
        try:
            source.installEventFilter(self)
            self._native_window_event_source = source
        except (AttributeError, RuntimeError, TypeError):
            self._native_window_event_source = None

    def _remove_native_window_event_filter(self) -> None:
        source = self._native_window_event_source
        self._native_window_event_source = None
        if source is None:
            return
        with suppress(AttributeError, RuntimeError, TypeError):
            source.removeEventFilter(self)

    def _native_window_is_active(self) -> bool | None:
        source = self._native_window_event_source
        if source is None:
            return None
        try:
            return bool(source.isActiveWindow())
        except (AttributeError, RuntimeError, TypeError):
            return None

    def _record_native_window_event(self, event) -> None:
        event_type = event.type()
        signal = self._NATIVE_WINDOW_EVENT_SIGNALS.get(event_type, "")
        if event_type == QEvent.Type.ActivationChange:
            active = self._native_window_is_active()
            if active is not None:
                signal = "window_activate" if active else "window_deactivate"
        if not signal:
            return
        now = time.monotonic()
        record = {
            "signal": signal,
            "recorded_at": now,
            "spontaneous": bool(event.spontaneous()),
        }
        self._native_window_last_event = record
        if signal == "window_deactivate":
            self._native_window_inactive = True
            # Do not discard a preceding Show/Resize/Expose requirement here.
            # A deactivation may arrive while the first visible frame is still
            # pending; only a completed full paint clears that requirement.
            self._native_window_paint_event = record
            return
        if signal == "window_activate":
            self._native_window_inactive = False
            self._native_window_requires_full_paint = True
            self._native_window_paint_event = record
            return
        if signal in self._NATIVE_WINDOW_STRUCTURAL_SIGNALS:
            self._native_window_requires_full_paint = True
            self._native_window_paint_event = record

    def _native_window_paint_provenance(self) -> dict[str, str]:
        now = time.monotonic()
        paint_event = self._native_window_paint_event or {}
        last_event = self._native_window_last_event or {}
        paint_recorded_at = float(paint_event.get("recorded_at", 0.0) or 0.0)
        last_recorded_at = float(last_event.get("recorded_at", 0.0) or 0.0)
        paint_age_ms = max(0.0, (now - paint_recorded_at) * 1000.0) if paint_recorded_at else -1.0
        last_age_ms = max(0.0, (now - last_recorded_at) * 1000.0) if last_recorded_at else -1.0
        signal = str(paint_event.get("signal", "") or "")
        if paint_age_ms > self.NATIVE_WINDOW_PROVENANCE_MAX_AGE_MS:
            signal = ""
        return {
            "signal": signal,
            "signal_age_ms": f"{paint_age_ms:.3f}" if paint_age_ms >= 0.0 else "",
            "last_event": str(last_event.get("signal", "") or ""),
            "last_event_age_ms": f"{last_age_ms:.3f}" if last_age_ms >= 0.0 else "",
            "window_inactive": str(bool(self._native_window_inactive)).lower(),
            "requires_full_paint": str(bool(self._native_window_requires_full_paint)).lower(),
        }

    def _maybe_defer_inactive_window_full_paint(self, event) -> bool:
        """Skip only the known inactive-window full-paint burst; otherwise fail open."""
        if self._closing or self._paint_metric_scope != "watchlist":
            return False
        if self._pending_paint_metric is not None:
            # Quote/model and flash paints carry a business reason and must
            # remain visible even while an independent window owns focus.
            return False
        try:
            if event.type() != QEvent.Type.Paint:
                return False
            if not bool(event.spontaneous()):
                return False
            viewport = self.viewport()
            if viewport is None or not viewport.isVisible() or not viewport.updatesEnabled():
                return False
            dirty_ratio, dirty_rects, full_viewport = _paint_region_metrics(
                event.region(), viewport.rect()
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False
        if not full_viewport:
            return False

        provenance = self._native_window_paint_provenance()
        try:
            signal_age_ms = float(provenance["signal_age_ms"])
            last_event_age_ms = float(provenance["last_event_age_ms"])
        except (KeyError, TypeError, ValueError):
            return False
        if (
            provenance["signal"] != "window_deactivate"
            or provenance["last_event"] != "window_update_request"
            or provenance["window_inactive"] != "true"
            or provenance["requires_full_paint"] != "false"
            or self._native_window_is_active() is not False
            or not 0.0 <= signal_age_ms <= self.NATIVE_WINDOW_DEACTIVATE_GUARD_MAX_AGE_MS
            or not 0.0 <= last_event_age_ms <= self.NATIVE_WINDOW_DEACTIVATE_GUARD_MAX_AGE_MS
        ):
            return False

        try:
            from core.observability import record_metric

            record_metric(
                "watchlist_inactive_window_full_paint_guard",
                1,
                unit="count",
                tags={
                    "decision": "defer_untracked_full",
                    "tab": self._paint_metric_scope,
                    "reason": "other",
                    "signal": provenance["signal"],
                    "signal_age_ms": provenance["signal_age_ms"],
                    "last_event": provenance["last_event"],
                    "last_event_age_ms": provenance["last_event_age_ms"],
                    "window_inactive": provenance["window_inactive"],
                    "requires_full_paint": provenance["requires_full_paint"],
                    "dirty_bounding_area_ratio": f"{dirty_ratio:.4f}",
                    "dirty_region_rects": str(dirty_rects),
                    "paint_event_spontaneous": str(bool(event.spontaneous())).lower(),
                },
            )
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            log.debug("skip inactive-window repaint guard metric: %s", exc)
        return True

    def prepare_shell_nav_repaint_guard(self) -> None:
        """Arm a short guard for redundant shell-nav full paints on data tables."""
        if self._closing or self._paint_metric_scope not in {"watchlist", "lhb"}:
            return
        self._arm_redundant_full_paint_guard(
            workspace_load_reason="shell_nav",
            metric_name=f"{self._paint_metric_scope}_shell_nav_repaint_guard",
            retain_after_budget=False,
            preserve_visible_frame=False,
        )

    def prepare_workspace_preload_repaint_guard(self, *, load_reason: str) -> None:
        """Protect an already rendered AI table from a nearby preload mount repaint burst."""
        load_reason_text = str(load_reason or "").strip()
        if (
            self._closing
            or self._paint_metric_scope != "ai_industry_chain"
            or load_reason_text not in {"background_prewarm", "restore_last_tab"}
        ):
            return
        self._arm_redundant_full_paint_guard(
            workspace_load_reason=load_reason_text,
            metric_name="ai_industry_chain_preload_repaint_guard",
            retain_after_budget=True,
            preserve_visible_frame=True,
        )

    def _arm_redundant_full_paint_guard(
        self,
        *,
        workspace_load_reason: str,
        metric_name: str,
        retain_after_budget: bool,
        preserve_visible_frame: bool,
    ) -> None:
        now = time.monotonic()
        guard = {
            "armed_until": now + self.SHELL_NAV_REPAINT_GUARD_ARM_MS / 1000.0,
            "active_until": 0.0,
            "active_started_at": 0.0,
            "first_full_seen": False,
            "viewport_size": None,
            "content_epoch": 0,
            "rendered_content_epoch": 0,
            "structural_epoch": 0,
            "rendered_structural_epoch": 0,
            "visible_dirty_region": QRegion(),
            "partial_update_pending": False,
            "suppressed": 0,
            "workspace_load_reason": str(workspace_load_reason or ""),
            "metric_name": str(metric_name or ""),
            "retain_after_budget": bool(retain_after_budget),
            "rearm_after_required_full": bool(preserve_visible_frame),
        }
        self._shell_nav_repaint_guard = guard
        viewport = self.viewport()
        if not (
            preserve_visible_frame
            and self.isVisible()
            and viewport is not None
            and viewport.isVisible()
        ):
            return
        self._activate_shell_nav_repaint_guard()
        viewport_rect = viewport.rect()
        guard["first_full_seen"] = True
        guard["viewport_size"] = (viewport_rect.width(), viewport_rect.height())
        guard["rendered_content_epoch"] = int(guard.get("content_epoch", 0) or 0)
        guard["rendered_structural_epoch"] = int(guard.get("structural_epoch", 0) or 0)
        self._record_shell_nav_repaint_guard(guard, "visible_frame_preserved")

    def _activate_shell_nav_repaint_guard(self) -> None:
        guard = self._shell_nav_repaint_guard
        if guard is None:
            return
        now = time.monotonic()
        if now > float(guard.get("armed_until", 0.0) or 0.0):
            self._clear_shell_nav_repaint_guard()
            return
        guard["active_started_at"] = now
        guard["active_until"] = now + self.SHELL_NAV_REPAINT_GUARD_ACTIVE_MS / 1000.0

    def _clear_shell_nav_repaint_guard(self) -> None:
        self._shell_nav_repaint_guard = None

    def _active_shell_nav_repaint_guard(self) -> dict[str, object] | None:
        guard = self._shell_nav_repaint_guard
        if guard is None:
            return None
        now = time.monotonic()
        active_until = float(guard.get("active_until", 0.0) or 0.0)
        armed_until = float(guard.get("armed_until", 0.0) or 0.0)
        if (active_until <= 0.0 and now > armed_until) or (active_until > 0.0 and now > active_until):
            self._clear_shell_nav_repaint_guard()
            return None
        viewport = self.viewport()
        if active_until <= 0.0 or viewport is None or not self.isVisible() or not viewport.isVisible():
            return None
        return guard

    def _record_shell_nav_repaint_guard(
        self,
        guard: dict[str, object],
        decision: str,
        *,
        region: QRegion | None = None,
        fallback_reason: str = "",
    ) -> None:
        scope = self._paint_metric_scope
        if not scope:
            return
        viewport = self.viewport()
        if viewport is None:
            return
        viewport_rect = viewport.rect()
        dirty_region = region if region is not None else QRegion()
        ratio, rects, _full = _paint_region_metrics(dirty_region, viewport_rect)
        started_at = float(guard.get("active_started_at", 0.0) or 0.0)
        try:
            from core.observability import record_metric

            tags = {
                "decision": decision,
                "workspace_load_reason": str(guard.get("workspace_load_reason", "shell_nav") or "shell_nav"),
                "age_ms": f"{max(0.0, (time.monotonic() - started_at) * 1000.0):.3f}",
                "remaining": str(
                    max(0, self.SHELL_NAV_REPAINT_GUARD_MAX_SUPPRESSIONS - int(guard.get("suppressed", 0) or 0))
                ),
                "suppressed": str(int(guard.get("suppressed", 0) or 0)),
                "retain_after_budget": str(bool(guard.get("retain_after_budget", False))).lower(),
                "dirty_bounding_area_ratio": f"{ratio:.4f}",
                "dirty_region_rects": str(rects),
            }
            if fallback_reason:
                tags["fallback_reason"] = fallback_reason
            metric_name = str(guard.get("metric_name", "") or f"{scope}_shell_nav_repaint_guard")
            record_metric(metric_name, 1, unit="count", tags=tags)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            log.debug("skip shell-nav repaint guard metric: %s", exc)

    def _invalidate_shell_nav_repaint_guard(self, reason: str) -> None:
        guard = self._active_shell_nav_repaint_guard()
        if guard is None:
            return
        guard["structural_epoch"] = int(guard.get("structural_epoch", 0) or 0) + 1
        if reason in {"model_layout_changed", "model_reset"} and (
            self._paint_metric_scope == "lhb"
            or bool(guard.get("rearm_after_required_full", False))
        ):
            # A required structural frame is allowed, then the short guard remains
            # available for the redundant native full paints that can follow it.
            if bool(guard.get("first_full_seen", False)):
                guard["first_full_seen"] = False
                guard["viewport_size"] = None
                guard["visible_dirty_region"] = QRegion()
                guard["partial_update_pending"] = False
                self._record_shell_nav_repaint_guard(guard, "rearm_after_structure", fallback_reason=reason)
            return
        if bool(guard.get("first_full_seen", False)):
            self._record_shell_nav_repaint_guard(guard, "allow_full_fallback", fallback_reason=reason)
            self._clear_shell_nav_repaint_guard()

    def _remember_shell_nav_repaint_dirty_region(self, top_left, bottom_right) -> None:
        guard = self._active_shell_nav_repaint_guard()
        if guard is None:
            return
        guard["content_epoch"] = int(guard.get("content_epoch", 0) or 0) + 1
        viewport = self.viewport()
        if (
            viewport is None
            or not getattr(top_left, "isValid", lambda: False)()
            or not getattr(bottom_right, "isValid", lambda: False)()
        ):
            guard["visible_dirty_region"] = QRegion(viewport.rect()) if viewport is not None else QRegion()
            return
        try:
            region = self.visualRegionForSelection(QItemSelection(top_left, bottom_right)).intersected(
                QRegion(viewport.rect())
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            guard["visible_dirty_region"] = QRegion(viewport.rect())
            return
        existing = guard.get("visible_dirty_region")
        guard["visible_dirty_region"] = (existing if isinstance(existing, QRegion) else QRegion()).united(region)

    def _acknowledge_shell_nav_partial_paint(self, event) -> None:
        guard = self._active_shell_nav_repaint_guard()
        if guard is None or not bool(guard.get("first_full_seen", False)):
            return
        dirty_region = guard.get("visible_dirty_region")
        if not isinstance(dirty_region, QRegion) or dirty_region.isEmpty():
            return
        try:
            remaining = dirty_region.subtracted(event.region())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return
        if remaining.isEmpty():
            guard["visible_dirty_region"] = QRegion()
            guard["partial_update_pending"] = False
            guard["rendered_content_epoch"] = int(guard.get("content_epoch", 0) or 0)

    def _maybe_defer_shell_nav_full_paint(self, event) -> bool:
        """Fail open unless this is a bounded, redundant post-reveal full paint."""
        guard = self._active_shell_nav_repaint_guard()
        if guard is None:
            return False
        try:
            viewport = self.viewport()
            if viewport is None:
                self._clear_shell_nav_repaint_guard()
                return False
            viewport_rect = viewport.rect()
            viewport_size = (viewport_rect.width(), viewport_rect.height())
            _ratio, _rects, full_viewport = _paint_region_metrics(event.region(), viewport_rect)
            if not full_viewport:
                self._acknowledge_shell_nav_partial_paint(event)
                return False
            if not bool(guard.get("first_full_seen", False)):
                guard["first_full_seen"] = True
                guard["viewport_size"] = viewport_size
                guard["rendered_content_epoch"] = int(guard.get("content_epoch", 0) or 0)
                guard["rendered_structural_epoch"] = int(guard.get("structural_epoch", 0) or 0)
                guard["visible_dirty_region"] = QRegion()
                guard["partial_update_pending"] = False
                self._record_shell_nav_repaint_guard(guard, "first_full_allowed")
                return False
            if guard.get("viewport_size") != viewport_size:
                self._record_shell_nav_repaint_guard(guard, "allow_full_fallback", fallback_reason="viewport_geometry")
                self._clear_shell_nav_repaint_guard()
                return False
            pending_metric = self._pending_paint_metric or {}
            if str(pending_metric.get("structural_reason", "") or ""):
                self._record_shell_nav_repaint_guard(guard, "allow_full_fallback", fallback_reason="structural_metric")
                self._clear_shell_nav_repaint_guard()
                return False
            if int(guard.get("structural_epoch", 0) or 0) != int(
                guard.get("rendered_structural_epoch", 0) or 0
            ):
                self._record_shell_nav_repaint_guard(guard, "allow_full_fallback", fallback_reason="structural_change")
                self._clear_shell_nav_repaint_guard()
                return False
            if str(pending_metric.get("reason", "") or "") == "flash_expiry":
                # Flash expiry has its own requested dirty region.  Let its paint
                # event through rather than leaving a stale highlight on screen.
                self._record_shell_nav_repaint_guard(guard, "allow_full_fallback", fallback_reason="flash_expiry")
                self._clear_shell_nav_repaint_guard()
                return False
            if (
                self._paint_metric_scope != "watchlist"
                and not bool(guard.get("retain_after_budget", False))
                and int(guard.get("suppressed", 0) or 0) >= self.SHELL_NAV_REPAINT_GUARD_MAX_SUPPRESSIONS
            ):
                self._clear_shell_nav_repaint_guard()
                return False
            dirty_region = guard.get("visible_dirty_region")
            dirty_region = dirty_region if isinstance(dirty_region, QRegion) else QRegion()
            content_changed = int(guard.get("content_epoch", 0) or 0) != int(
                guard.get("rendered_content_epoch", 0) or 0
            )
            if content_changed:
                if bool(guard.get("partial_update_pending", False)):
                    self._record_shell_nav_repaint_guard(
                        guard,
                        "allow_full_fallback",
                        fallback_reason="targeted_region_expanded",
                    )
                    self._clear_shell_nav_repaint_guard()
                    return False
                _dirty_ratio, _dirty_rects, dirty_is_full = _paint_region_metrics(dirty_region, viewport_rect)
                if dirty_region.isEmpty() or dirty_is_full or not viewport.updatesEnabled():
                    fallback_reason = "dirty_region" if dirty_region.isEmpty() or dirty_is_full else "updates_disabled"
                    if (
                        dirty_is_full
                        and bool(guard.get("rearm_after_required_full", False))
                    ):
                        guard["rendered_content_epoch"] = int(guard.get("content_epoch", 0) or 0)
                        guard["visible_dirty_region"] = QRegion()
                        guard["partial_update_pending"] = False
                        self._record_shell_nav_repaint_guard(
                            guard,
                            "allow_content_full",
                            fallback_reason=fallback_reason,
                        )
                        return False
                    self._record_shell_nav_repaint_guard(guard, "allow_full_fallback", fallback_reason=fallback_reason)
                    self._clear_shell_nav_repaint_guard()
                    return False
                viewport.update(dirty_region)
                guard["partial_update_pending"] = True
                decision = "partial_fallback"
                guard["suppressed"] = int(guard.get("suppressed", 0) or 0) + 1
                self._record_shell_nav_repaint_guard(guard, decision, region=dirty_region)
                return True

            suppressed = int(guard.get("suppressed", 0) or 0) + 1
            guard["suppressed"] = suppressed
            decision = (
                "suppress_redundant_full_after_budget"
                if (
                    self._paint_metric_scope == "watchlist"
                    or bool(guard.get("retain_after_budget", False))
                )
                and suppressed > self.SHELL_NAV_REPAINT_GUARD_MAX_SUPPRESSIONS
                else "suppress_redundant_full"
            )
            self._record_shell_nav_repaint_guard(guard, decision)
            return True
        except Exception as exc:  # noqa: BLE001 - Paint handling must fail open on an unexpected Qt wrapper error.
            self._clear_shell_nav_repaint_guard()
            log.debug("allow shell-nav paint after guard error: %s", exc)
            return False

    def prepare_background_preload_reveal(self) -> None:
        """Reset off-screen paint provenance before the first visible frame."""
        self._pending_paint_metric = None
        self._mark_pending_paint_metric("preload_reveal", model_rows=self._model_row_count())

    def _data_change_includes_quote_columns(self, top_left, bottom_right) -> bool:
        model = self.model()
        if model is None or not top_left.isValid() or not bottom_right.isValid():
            return False
        quote_headers = {"现价", "市价", "涨幅%", "涨幅", "市值", "买点"}
        col_start, col_end = sorted((top_left.column(), bottom_right.column()))
        return any(
            str(model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or "")
            in quote_headers
            for column in range(col_start, col_end + 1)
        )

    def _remember_flash_dirty_indexes(self, top_left, bottom_right) -> int:
        model = self.model()
        if model is None or not top_left.isValid() or not bottom_right.isValid():
            return len(self._flash_dirty_indexes)

        row_start, row_end = sorted((top_left.row(), bottom_right.row()))
        col_start, col_end = sorted((top_left.column(), bottom_right.column()))
        flash_role = int(Qt.ItemDataRole.UserRole) + 1
        for row in range(row_start, row_end + 1):
            for column in range(col_start, col_end + 1):
                index = model.index(row, column)
                if not index.isValid() or not index.data(flash_role):
                    continue
                self._flash_dirty_indexes.add(QPersistentModelIndex(index))
        return len(self._flash_dirty_indexes)

    def _flash_repaint_region(self) -> tuple[QRegion, int, int]:
        viewport = self.viewport()
        if viewport is None:
            return QRegion(), len(self._flash_dirty_indexes), 0
        viewport_rect = viewport.rect()
        region = QRegion()
        visible_dirty_cells = 0
        for persistent_index in self._flash_dirty_indexes:
            if not persistent_index.isValid():
                continue
            rect = self.visualRect(QModelIndex(persistent_index)).intersected(viewport_rect)
            if rect.isEmpty():
                continue
            visible_dirty_cells += 1
            region = region.united(QRegion(rect))
        return region, len(self._flash_dirty_indexes), visible_dirty_cells

    def _mark_pending_paint_metric(self, reason: str, **metadata) -> None:
        if not self._paint_metric_scope:
            return
        reason_text = str(reason or "other")
        now = time.perf_counter()
        previous = self._pending_paint_metric
        previous_reasons = tuple((previous or {}).get("pending_reasons", ()))
        if not previous_reasons and previous is not None:
            previous_reasons = (str(previous.get("reason", "other")),)
        pending_reasons = tuple(dict.fromkeys((*previous_reasons, reason_text)))
        primary_reason = max(
            pending_reasons,
            key=lambda item: _PAINT_REASON_PRIORITY.get(item, 0),
        )
        merged = dict(previous or {})
        merged.update(metadata)
        merged.update(
            {
                "reason": primary_reason,
                "pending_reasons": pending_reasons,
                "scheduled_at": (previous or {}).get("scheduled_at", now),
                "last_scheduled_at": now,
            }
        )
        structural_reason = next(
            (item for item in pending_reasons if item == primary_reason and item in _STRUCTURAL_PAINT_REASONS),
            "",
        )
        if structural_reason:
            merged["structural_reason"] = structural_reason
        else:
            merged.pop("structural_reason", None)
        if "requested_full_viewport" in metadata:
            merged["targeted_request_reason"] = reason_text
        self._pending_paint_metric = merged

    def schedule_flash_repaint_until(self, active_until: float) -> None:
        if not self.isVisible():
            self._clear_flash_repaint_state()
            return
        self._flash_repaint_until = max(self._flash_repaint_until, float(active_until))
        if self._targeted_flash_repaint:
            self._flash_repaint_scheduled_at = time.perf_counter()
        if self._coalesced_flash_repaint:
            remaining_ms = max(1, int((self._flash_repaint_until - time.time()) * 1000))
            self._flash_repaint_timer.start(remaining_ms)
        elif not self._flash_repaint_timer.isActive():
            self._flash_repaint_timer.start()

    def _tick_flash_repaint(self) -> None:
        if self._closing:
            self._flash_repaint_timer.stop()
            return
        if not self.isVisible():
            self._clear_flash_repaint_state()
            return
        viewport = self.viewport()
        if viewport is None:
            self._flash_repaint_timer.stop()
            return
        if self._coalesced_flash_repaint:
            self._flash_repaint_timer.stop()
            deadline = self._flash_repaint_until
            self._flash_repaint_until = 0.0
            if not self._targeted_flash_repaint:
                if self._paint_metric_scope:
                    self._mark_pending_paint_metric("flash_expiry")
                viewport.update()
                self._flash_dirty_indexes.clear()
                return

            callback_started_at = time.perf_counter()
            callback_wall_time = time.time()
            region, dirty_cells, visible_dirty_cells = self._flash_repaint_region()
            scheduled_at = self._flash_repaint_scheduled_at
            self._flash_repaint_scheduled_at = 0.0
            viewport_rect = viewport.rect()
            requested_ratio, requested_rects, requested_full = _paint_region_metrics(region, viewport_rect)
            if self._paint_metric_scope:
                from core.observability import record_metric

                tags = {
                    "dirty_bounding_area_ratio": f"{requested_ratio:.4f}",
                    "dirty_region_rects": str(requested_rects),
                    "requested_full_viewport": str(requested_full).lower(),
                    "dirty_cells": str(dirty_cells),
                    "visible_dirty_cells": str(visible_dirty_cells),
                    "tab": self._paint_metric_scope,
                }
                callback_elapsed_ms = (time.perf_counter() - callback_started_at) * 1000.0
                if scheduled_at > 0.0:
                    record_metric(
                        f"{self._paint_metric_scope}_flash_repaint_schedule_to_callback_ms",
                        (callback_started_at - scheduled_at) * 1000.0,
                        unit="ms",
                        tags=tags,
                    )
                if deadline > 0.0:
                    record_metric(
                        f"{self._paint_metric_scope}_flash_repaint_timer_offset_ms",
                        (callback_wall_time - deadline) * 1000.0,
                        unit="ms",
                        tags=tags,
                    )
                record_metric(
                    f"{self._paint_metric_scope}_flash_repaint_callback_ms",
                    callback_elapsed_ms,
                    unit="ms",
                    tags=tags,
                )
            if not region.isEmpty():
                self._mark_pending_paint_metric(
                    "flash_expiry",
                    dirty_cells=dirty_cells,
                    visible_dirty_cells=visible_dirty_cells,
                    dirty_region_rects=requested_rects,
                    requested_dirty_bounding_area_ratio=f"{requested_ratio:.4f}",
                    requested_dirty_region_rects=requested_rects,
                    requested_full_viewport=requested_full,
                )
                viewport.update(region)
            else:
                self._pending_paint_metric = None
            self._flash_dirty_indexes.clear()
            return
        if time.time() >= self._flash_repaint_until:
            self._flash_repaint_timer.stop()
            self._flash_repaint_until = 0.0
            viewport.update()
            return
        viewport.update()

    def paintEvent(self, event) -> None:
        scope = self._paint_metric_scope
        if not scope:
            super().paintEvent(event)
            return
        viewport = self.viewport()
        if viewport is None:
            super().paintEvent(event)
            return
        metric = self._pending_paint_metric
        reason = str((metric or {}).get("reason", "other"))
        viewport_rect = viewport.rect()
        delivered_ratio, delivered_rects, delivered_full = _paint_region_metrics(
            event.region(), viewport_rect
        )
        tags = {
            "dirty_bounding_area_ratio": f"{delivered_ratio:.4f}",
            "delivered_dirty_bounding_area_ratio": f"{delivered_ratio:.4f}",
            "delivered_full_viewport": str(delivered_full).lower(),
            "dirty_region_rects": str(delivered_rects),
            "paint_event_spontaneous": str(bool(event.spontaneous())).lower(),
            "reason": reason,
            "tab": scope,
        }
        native_window_provenance = self._native_window_paint_provenance()
        if metric is None and delivered_full:
            tags.update(
                {
                    "native_window_signal": native_window_provenance["signal"],
                    "native_window_signal_age_ms": native_window_provenance["signal_age_ms"],
                    "native_window_last_event": native_window_provenance["last_event"],
                    "native_window_last_event_age_ms": native_window_provenance["last_event_age_ms"],
                    "native_window_inactive": native_window_provenance["window_inactive"],
                    "native_window_requires_full_paint": native_window_provenance["requires_full_paint"],
                }
            )
        for key in (
            "changed_rows",
            "changed_columns",
            "changed_indexes",
            "update_threshold",
            "threshold_exceeded",
            "includes_flash_role",
            "model_rows",
            "dirty_cells",
            "visible_dirty_cells",
            "requested_dirty_bounding_area_ratio",
            "requested_dirty_region_rects",
            "requested_full_viewport",
            "targeted_request_reason",
            "structural_reason",
        ):
            if metric is not None and key in metric:
                value = metric[key]
                tags[key] = str(value).lower() if isinstance(value, bool) else str(value)
        if metric is not None:
            pending_reasons = tuple(metric.get("pending_reasons", ()))
            if pending_reasons:
                tags["pending_reasons"] = ",".join(str(item) for item in pending_reasons)

        requested_full_value = (metric or {}).get("requested_full_viewport")
        requested_full = requested_full_value if isinstance(requested_full_value, bool) else None
        structural_reason = str((metric or {}).get("structural_reason", ""))
        if structural_reason:
            tags["delivery_kind"] = "structural_full_viewport" if delivered_full else "structural_partial_region"
            if requested_full is False:
                tags["targeted_request_coalesced_with_structural"] = "true"
        elif requested_full is False:
            tags["delivery_kind"] = "full_after_targeted_request" if delivered_full else "requested_region"
            tags["region_expanded"] = str(delivered_full).lower()
        elif delivered_full:
            tags["delivery_kind"] = "full_viewport"
        else:
            tags["delivery_kind"] = "partial_region"

        paint_transition_metadata = {}
        parent = self.parentWidget()
        while parent is not None:
            if hasattr(parent, "_workspace_load_reason"):
                tags["workspace_load_reason"] = str(getattr(parent, "_workspace_load_reason", "") or "")
                tags["background_preload_ready"] = str(
                    bool(getattr(parent, "_workspace_background_preload_ready", False))
                ).lower()
                tags["preload_staged"] = str(bool(getattr(parent, "_workspace_preload_staged", False))).lower()
            if hasattr(parent, "_background_prewarm_active_key"):
                tags["background_prewarm_active_key_at_paint"] = str(
                    getattr(parent, "_background_prewarm_active_key", "") or ""
                )
            transition_context = getattr(parent, "_workspace_tab_transition_context", None)
            if (
                isinstance(transition_context, dict)
                and str(transition_context.get("target_tab") or "").strip() == scope
            ):
                paint_transition_metadata = {
                    "transition_id": str(transition_context.get("transition_id") or ""),
                    "source_tab": str(transition_context.get("source_tab") or ""),
                    "target_tab": str(transition_context.get("target_tab") or ""),
                    "transition_reason": str(transition_context.get("reason") or ""),
                    "preload_state": str(transition_context.get("preload_state") or ""),
                    "mounted_before": str(bool(transition_context.get("mounted_before"))).lower(),
                    "transition_phase": "paint",
                }
                tags.update(
                    {
                        key: value
                        for key, value in paint_transition_metadata.items()
                        if key != "transition_id"
                    }
                )
            parent = parent.parentWidget()

        started_at = time.perf_counter()
        from infra.diagnostics.ui_stall_probe import ui_stall_span

        stall_signal = reason
        if reason == "other" and delivered_full:
            stall_signal = native_window_provenance["signal"] or reason
        stall_transition_metadata = {
            **paint_transition_metadata,
            "reason": str((paint_transition_metadata or {}).get("transition_reason") or ""),
        }
        with ui_stall_span(
            "VCPTableView.paintEvent",
            tab=scope,
            signal=stall_signal,
            dirty_bounding_area_ratio=tags["dirty_bounding_area_ratio"],
            delivered_full_viewport=tags["delivered_full_viewport"],
            **stall_transition_metadata,
        ):
            super().paintEvent(event)
        if delivered_full:
            # A real full viewport paint has restored the Base background and
            # current table frame, so a later inactive-window burst can be
            # considered for the narrow guard again.
            self._native_window_requires_full_paint = False
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0

        if metric is not None or elapsed_ms >= 25.0:
            from core.observability import record_metric

            record_metric(f"{scope}_table_paint_ms", elapsed_ms, unit="ms", tags=tags)
            scheduled_value = (metric or {}).get("scheduled_at", 0.0)
            scheduled_at = float(scheduled_value) if isinstance(scheduled_value, (int, float)) else 0.0
            if scheduled_at > 0.0:
                record_metric(
                    f"{scope}_table_paint_delay_ms",
                    (started_at - scheduled_at) * 1000.0,
                    unit="ms",
                    tags=tags,
                )
        if metric is self._pending_paint_metric:
            self._pending_paint_metric = None

    def showEvent(self, event):
        if self._closing:
            return
        if self._paint_metric_scope == "watchlist":
            # A just-shown table must render one full frame before any native
            # inactive-window suppression is considered.
            self._native_window_requires_full_paint = True
            self._native_window_inactive = False
            self._native_window_paint_event = None
            self._native_window_last_event = None
            self._install_native_window_event_filter()
        self._apply_screen_width_limit()
        self._sync_ambient_repaint_timer()
        self._activate_shell_nav_repaint_guard()
        super().showEvent(event)

    def hideEvent(self, event):
        if self._paint_metric_scope == "watchlist":
            self._native_window_requires_full_paint = True
            self._native_window_inactive = False
            self._native_window_paint_event = None
            self._native_window_last_event = None
            self._remove_native_window_event_filter()
        self._ambient_repaint_timer.stop()
        self._clear_flash_repaint_state()
        self._clear_shell_nav_repaint_guard()
        super().hideEvent(event)

    def _clear_flash_repaint_state(self) -> None:
        self._flash_repaint_timer.stop()
        self._flash_repaint_until = 0.0
        self._flash_repaint_scheduled_at = 0.0
        self._pending_paint_metric = None
        self._flash_dirty_indexes.clear()

    def set_ambient_repaint_enabled(self, enabled: bool) -> None:
        self.setProperty("ambientPulse", bool(enabled))
        self._sync_ambient_repaint_timer()

    def _sync_ambient_repaint_timer(self) -> None:
        if self._closing:
            self._ambient_repaint_timer.stop()
            return
        if self.property("ambientPulse") and self.isVisible():
            if not self._ambient_repaint_timer.isActive():
                self._ambient_repaint_timer.start()
        else:
            self._ambient_repaint_timer.stop()

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API naming
        if watched is self._native_window_event_source:
            try:
                self._record_native_window_event(event)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
        return super().eventFilter(watched, event)

    def _display_font_for_index(self, index):
        font = index.data(Qt.ItemDataRole.FontRole)
        if isinstance(font, QFont):
            return font
        return self.font()

    def _should_show_tooltip_for_index(self, index) -> bool:
        if not index.isValid():
            return False

        tooltip_text = index.data(Qt.ItemDataRole.ToolTipRole)
        if not tooltip_text:
            return False
        tooltip_text = str(tooltip_text)
        if _is_quote_metadata_tooltip(tooltip_text):
            return True
        return _is_elided_table_cell(self, index)

    def viewportEvent(self, event):
        event_type = event.type()
        if event_type == QEvent.Type.Paint:
            if self._maybe_defer_shell_nav_full_paint(event):
                return True
            if self._maybe_defer_inactive_window_full_paint(event):
                return True
        elif event_type in {
            QEvent.Type.Resize,
            QEvent.Type.StyleChange,
            QEvent.Type.FontChange,
            QEvent.Type.PaletteChange,
        }:
            self._invalidate_shell_nav_repaint_guard("viewport_change")
            if event_type in {
                QEvent.Type.StyleChange,
                QEvent.Type.FontChange,
                QEvent.Type.PaletteChange,
            }:
                self._clear_model_presentation_cache()
        elif event_type in {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.Wheel,
            QEvent.Type.KeyPress,
        }:
            self._invalidate_shell_nav_repaint_guard("viewport_input")
        try:
            if self._closing:
                if event.type() == QEvent.Type.ToolTip:
                    hide_floating_tooltip()
                    event.ignore()
                    return True
                return False
            if event.type() == QEvent.Type.ToolTip:
                index = self.indexAt(event.pos())
                if index.isValid():
                    tooltip_text = index.data(Qt.ItemDataRole.ToolTipRole)
                    if tooltip_text and self._should_show_tooltip_for_index(index):
                        show_floating_tooltip(
                            str(tooltip_text),
                            event.globalPos(),
                            owner=self.viewport(),
                            rich_text=False,
                        )
                        return True
                hide_floating_tooltip()
                event.ignore()
                return True
            return super().viewportEvent(event)
        except Exception as exc:  # noqa: BLE001 - Qt event handlers must not leak into sys.excepthook.
            with suppress(Exception):
                hide_floating_tooltip()
                event.ignore()
            log.debug("suppressed VCPTableView viewport event failure during Qt event handling: %s", exc)
            return True


class PulsingDot(QWidget):
    """呼吸灯指示器组件"""

    def __init__(self, color="#10B981", parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.dot_color = QColor(color)
        self._radius = 3.5
        self._opacity = 1.0
        self._animation_requested = True

        self.anim = QPropertyAnimation(self, b"opacity", self)
        self.anim.setDuration(1500)
        self.anim.setStartValue(0.2)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.anim.setLoopCount(-1)

        self._start_timer = QTimer(self)
        self._start_timer.setSingleShot(True)
        self._start_timer.timeout.connect(self.anim.start)

    def _sync_animation(self) -> None:
        if self._animation_requested and self.isVisible():
            if self.anim.state() == self.anim.State.Stopped and not self._start_timer.isActive():
                self._start_timer.start(100)
            return
        self._stop_animation()

    def set_running(self, running: bool) -> None:
        self._animation_requested = bool(running)
        self._sync_animation()

    def _stop_animation(self) -> None:
        self._start_timer.stop()
        self.anim.stop()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_animation()

    def hideEvent(self, event) -> None:
        self._stop_animation()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._animation_requested = False
        self._stop_animation()
        super().closeEvent(event)

    def deleteLater(self):
        self._animation_requested = False
        self._stop_animation()
        super().deleteLater()

    @pyqtProperty(float)
    def opacity(self):
        return self._opacity

    @opacity.setter
    def opacity(self, val):
        self._opacity = val
        self.update()

    def set_color(self, color):
        self.dot_color = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        glow_color = QColor(self.dot_color)
        glow_color.setAlphaF(self._opacity * 0.3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow_color))
        painter.drawEllipse(self.rect().center(), int(self._radius + 3), int(self._radius + 3))

        core_color = QColor(self.dot_color)
        core_color.setAlphaF(self._opacity * 0.9)
        painter.setBrush(QBrush(core_color))
        painter.drawEllipse(self.rect().center(), int(self._radius), int(self._radius))

        painter.end()


class StatusGlyph(QWidget):
    """Color plus geometry status indicator for the shell status bar."""

    def __init__(self, tone: str = "offline", parent=None):
        super().__init__(parent)
        self._tone = tone if tone in {"online", "busy", "offline"} else "offline"
        self._color_override: QColor | None = None
        size = build_ui_tokens()["icon"]["status_size"]
        self.setFixedSize(size + 2, size + 2)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_tone(self, tone: str) -> None:
        normalized = tone if tone in {"online", "busy", "offline"} else "offline"
        if normalized == self._tone:
            return
        self._tone = normalized
        self._color_override = None
        self.update()

    def set_color(self, color) -> None:
        self._color_override = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:
        tokens = build_ui_tokens()
        glyph = tokens["status_glyph"].get(self._tone, tokens["status_glyph"]["offline"])
        color = QColor(self._color_override or glyph.get("color", "#EF4444"))
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        fill = QColor(color)
        fill.setAlpha(46)
        stroke = QColor(color)
        stroke.setAlpha(220)
        painter.setPen(QPen(stroke, 1.3))
        painter.setBrush(QBrush(fill))

        shape = glyph.get("shape", "circle")
        if shape == "hexagon":
            center = rect.center()
            radius = min(rect.width(), rect.height()) / 2
            polygon = QPolygonF(
                [
                    QPointF(
                        center.x() + math.cos(math.radians(60 * idx - 30)) * radius,
                        center.y() + math.sin(math.radians(60 * idx - 30)) * radius,
                    )
                    for idx in range(6)
                ]
            )
            painter.drawPolygon(polygon)
        elif shape == "triangle":
            polygon = QPolygonF(
                [
                    QPointF(rect.center().x(), rect.top()),
                    QPointF(rect.right(), rect.bottom()),
                    QPointF(rect.left(), rect.bottom()),
                ]
            )
            painter.drawPolygon(polygon)
        else:
            painter.drawEllipse(rect)

        symbol = glyph.get("symbol", "")
        painter.setPen(QPen(stroke, 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if symbol == "check":
            path = QPainterPath(QPointF(rect.left() + rect.width() * 0.28, rect.center().y()))
            path.lineTo(rect.left() + rect.width() * 0.44, rect.bottom() - rect.height() * 0.30)
            path.lineTo(rect.right() - rect.width() * 0.24, rect.top() + rect.height() * 0.30)
            painter.drawPath(path)
        elif symbol == "hourglass":
            left = rect.left() + rect.width() * 0.32
            right = rect.right() - rect.width() * 0.32
            top = rect.top() + rect.height() * 0.26
            mid = rect.center().y()
            bottom = rect.bottom() - rect.height() * 0.26
            painter.drawLine(QPointF(left, top), QPointF(right, top))
            painter.drawLine(QPointF(left, bottom), QPointF(right, bottom))
            painter.drawLine(QPointF(left, top), QPointF(right, mid))
            painter.drawLine(QPointF(right, top), QPointF(left, mid))
            painter.drawLine(QPointF(left, mid), QPointF(right, bottom))
            painter.drawLine(QPointF(right, mid), QPointF(left, bottom))
        else:
            x = rect.center().x()
            painter.drawLine(QPointF(x, rect.top() + rect.height() * 0.28), QPointF(x, rect.bottom() - rect.height() * 0.36))
            dot_center = QPointF(x, rect.bottom() - rect.height() * 0.20)
            painter.setBrush(QBrush(stroke))
            painter.drawEllipse(QRectF(dot_center.x() - 0.65, dot_center.y() - 0.65, 1.3, 1.3))

        painter.end()


class SkeletonShimmer(QWidget):
    """Matte skeleton rows with a soft left-to-right shimmer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._running_requested = False
        self.setFixedHeight(72)
        self.setMinimumWidth(240)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._timer = QTimer(self)
        self._timer.setInterval(45)
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.045) % 1.0
        self.update()

    def set_running(self, running: bool) -> None:
        self._running_requested = bool(running)
        self._sync_timer()

    def _sync_timer(self) -> None:
        if self._running_requested and self.isVisible():
            if not self._timer.isActive():
                self._timer.start()
            return
        self._timer.stop()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        self._running_requested = False
        self.set_running(False)
        super().closeEvent(event)

    def deleteLater(self):
        self._running_requested = False
        self.set_running(False)
        super().deleteLater()

    def paintEvent(self, event) -> None:
        tokens = build_ui_tokens()
        skel = tokens["skeleton"]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        row_h = int(skel["row_height"])
        gap = int(skel["row_gap"])
        radius = int(skel["radius"])
        widths = (0.82, 0.64, 0.74)
        y = 2
        for factor in widths:
            rect = QRectF(0, y, max(40.0, self.width() * factor), row_h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(skel["base"])))
            painter.drawRoundedRect(rect, radius, radius)

            span = max(80.0, self.width() * 0.55)
            center = (self.width() + span * 2.0) * self._phase - span
            gradient = QLinearGradient(center - span, 0, center + span, 0)
            edge = QColor(skel["base"])
            edge.setAlpha(0)
            shine = QColor(skel["shine"])
            gradient.setColorAt(0.0, edge)
            gradient.setColorAt(0.50, shine)
            gradient.setColorAt(1.0, edge)
            painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(rect, radius, radius)
            y += row_h + gap
        painter.end()


class BullGlyph(QWidget):
    """A tiny line mascot used only for warm empty states."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(76, 42)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event) -> None:
        tokens = build_ui_tokens()
        color = QColor(tokens["theme"].get("BRAND_PRIMARY", "#B93A32"))
        soft = QColor(color)
        soft.setAlpha(28)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(soft))
        painter.drawEllipse(QRectF(12, 12, 52, 24))

        pen = QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        painter.drawArc(QRectF(14, 14, 48, 22), 0, 180 * 16)
        painter.drawLine(QPointF(22, 23), QPointF(10, 12))
        painter.drawLine(QPointF(54, 23), QPointF(66, 12))
        painter.drawLine(QPointF(29, 31), QPointF(27, 37))
        painter.drawLine(QPointF(47, 31), QPointF(49, 37))
        painter.drawPoint(QPointF(35, 24))
        painter.drawPoint(QPointF(43, 24))
        painter.end()


def format_multi_select_summary(
    prefix: str,
    selected_labels,
    *,
    all_text: str = "全部",
    inline_limit: int = 2,
    separator: str = " / ",
    count_suffix: str = "项",
) -> tuple[str, str]:
    labels = [str(label or "").strip() for label in (selected_labels or []) if str(label or "").strip()]
    prefix_text = str(prefix or "").strip()
    if not labels:
        text = f"{prefix_text}：{all_text}" if prefix_text else all_text
        return text, all_text

    tooltip = "、".join(labels)
    body = separator.join(labels) if len(labels) <= max(1, int(inline_limit or 1)) else f"{len(labels)}{count_suffix}"

    text = f"{prefix_text}：{body}" if prefix_text else body
    return text, tooltip


class MultiSelectFilterButton(QToolButton):
    selectionChanged = pyqtSignal()

    def __init__(self, all_label: str = "全部", parent=None):
        super().__init__(parent)
        self._all_label = str(all_label or "全部")
        self._menu = QMenu(self)
        self._all_action: QAction | None = None
        self._actions: dict[str, QAction] = {}
        self._labels: dict[str, str] = {}
        self._updating = False

        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setMenu(self._menu)
        install_menu_fade(self._menu)
        self.setText(self._all_label)
        self.setToolTip(self._all_label)
        self.set_options([])

    def option_values(self) -> list[str]:
        return list(self._actions.keys())

    def option_labels(self) -> list[str]:
        return [self._labels[value] for value in self.option_values()]

    def selected_values(self) -> set[str]:
        if not self._actions:
            return set()
        if self._all_action and self._all_action.isChecked():
            return set()
        return {value for value, action in self._actions.items() if action.isChecked()}

    def selected_labels(self) -> list[str]:
        selected = self.selected_values()
        return [self._labels[value] for value in self.option_values() if value in selected]

    def apply_summary(self, prefix: str, *, all_text: str = "全部") -> None:
        text, tooltip = format_multi_select_summary(prefix, self.selected_labels(), all_text=all_text)
        self.setText(text)
        self.setToolTip(tooltip)

    def has_value(self, value: str) -> bool:
        return str(value or "").strip() in self._actions

    def set_options(self, options, *, preserve_selection: bool = True):
        current_selection = self.selected_values() if preserve_selection else set()
        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        for option in options or []:
            if isinstance(option, (tuple, list)) and len(option) >= 2:
                value = str(option[0] or "").strip()
                label = str(option[1] or "").strip()
            else:
                value = str(option or "").strip()
                label = value
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append((value, label or value))

        self._updating = True
        try:
            self._menu.clear()
            self._actions.clear()
            self._labels.clear()

            self._all_action = QAction(self._all_label, self)
            self._all_action.setCheckable(True)
            self._all_action.toggled.connect(self._on_all_toggled)
            self._menu.addAction(self._all_action)

            if normalized:
                self._menu.addSeparator()

            for value, label in normalized:
                action = QAction(label, self)
                action.setCheckable(True)
                action.setData(value)
                action.toggled.connect(self._on_option_toggled)
                self._actions[value] = action
                self._labels[value] = label
                self._menu.addAction(action)
        finally:
            self._updating = False

        restored_selection = {value for value in current_selection if value in self._actions}
        self.set_selected_values(restored_selection, emit=False)

    def set_selected_values(self, values, *, emit: bool = True):
        selected = {str(value or "").strip() for value in (values or []) if str(value or "").strip() in self._actions}

        self._updating = True
        try:
            if self._all_action is not None:
                self._all_action.setChecked(not selected)
            for value, action in self._actions.items():
                action.setChecked(value in selected)
        finally:
            self._updating = False

        if emit:
            self.selectionChanged.emit()

    def _on_all_toggled(self, checked: bool):
        if self._updating:
            return
        if checked:
            self.set_selected_values(set(), emit=True)
            return
        if not any(action.isChecked() for action in self._actions.values()):
            self.set_selected_values(set(), emit=True)

    def _on_option_toggled(self, _checked: bool):
        if self._updating:
            return
        self.set_selected_values(
            {value for value, action in self._actions.items() if action.isChecked()},
            emit=True,
        )


class TableStateOverlay(QWidget):
    """统一空/加载状态覆盖层"""

    _DEFAULT_SUBTITLES = {
        "empty": "今天风平浪静，标的正在悄悄积蓄力量。",
        "loading": "正在同步最新数据，请稍候。",
        "offline": "当前处于离线模式，仅展示本地缓存。",
        "cached": "本次未拿到新结果，当前展示的是最近一次成功数据。",
        "error": "本次加载失败，请检查网络或稍后重试。",
        "success": "最新数据已同步完成。",
        "info": "当前状态已更新。",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode, self._disposed = "empty", False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._card = QFrame(self)
        self._card.setObjectName("tableStateCard")
        self._card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._card.setMinimumWidth(320)
        self._card.setMaximumWidth(500)

        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(22, 18, 22, 18)
        card_layout.setSpacing(10)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._dot = PulsingDot(parent=self)
        self._dot.setVisible(False)

        self._bull = BullGlyph(parent=self)
        self._bull.setVisible(False)

        self._skeleton = SkeletonShimmer(parent=self)
        self._skeleton.setVisible(False)

        self._title = QLabel("")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setWordWrap(True)
        self._title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self._subtitle = QLabel("")
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle.setWordWrap(True)
        self._subtitle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._subtitle.setMinimumWidth(260)
        self._subtitle.setMaximumWidth(420)

        self._meta = QLabel("")
        self._meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._meta.setWordWrap(True)
        self._meta.setVisible(False)
        self._meta.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._meta.setMinimumWidth(260)
        self._meta.setMaximumWidth(420)

        self._action = QPushButton("", self._card)
        self._action.setVisible(False)
        self._action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action.setProperty("class", "secondary")
        self._action_callback = None
        self._action.clicked.connect(self._handle_action)
        install_button_feedback(self._action)

        card_layout.addWidget(self._bull, 0, Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self._skeleton, 0, Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self._title)
        card_layout.addWidget(self._subtitle)
        card_layout.addWidget(self._meta)
        card_layout.addWidget(self._action, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._card, 0, Qt.AlignmentFlag.AlignCenter)

        self._bind_theme_manager()
        self._sync_card_width()
        self._apply_style()

    def _bind_theme_manager(self) -> None:
        from ui.theme import theme_manager

        self._theme_manager = theme_manager
        self._theme_manager.sig_theme_changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _name: str) -> None:
        if not self._disposed:
            self._apply_style()

    def _handle_action(self):
        if callable(self._action_callback):
            self._action_callback()

    def _sync_card_width(self):
        tokens = build_ui_tokens()
        available = max(240, self.width() - (tokens["space"]["2xl"] * 2))
        self._card.setMinimumWidth(min(320, available))
        self._card.setMaximumWidth(min(500, available))
        text_width = max(220, min(420, available - (tokens["space"]["lg"] * 2)))
        for label in (self._subtitle, self._meta):
            label.setMinimumWidth(min(260, text_width))
            label.setMaximumWidth(text_width)

    def resizeEvent(self, event):  # noqa: N802 - Qt API naming
        super().resizeEvent(event)
        self._sync_card_width()

    def _apply_style(self):
        tokens = build_ui_tokens()
        t = tokens["theme"]
        tone = get_state_tone(self._mode)
        card_border = tone["border"] if self._mode != "empty" else tokens["border"]["default"]
        card_bg = tone["bg"] if self._mode != "empty" else tokens["surface"]["elevated"]
        self._card.setStyleSheet(
            f"""
            QFrame#tableStateCard {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: {tokens["radius"]["lg"]}px;
            }}
            """
        )
        self._title.setStyleSheet(
            f"color: {tone['fg'] if self._mode != 'empty' else t['TEXT_PRIMARY']};"
            f" font-size: {tokens['font']['size_lg']}px; font-weight: {tokens['font']['weight_bold']};"
        )
        self._subtitle.setStyleSheet(f"color: {t['TEXT_SECONDARY']}; font-size: {tokens['font']['size_sm']}px;")
        self._meta.setStyleSheet(
            f"color: {t['TEXT_MUTED']}; font-size: {tokens['font']['size_sm']}px;"
            f" font-family: {tokens['font']['mono_family']};"
        )
        dot_color = (
            tone["fg"]
            if self._mode in ("loading", "success", "warning", "error", "info", "cached")
            else t.get("COLOR_INFO", "#3B82F6")
        )
        self._dot.set_color(dot_color)

    def set_state(
        self,
        mode: str,
        title: str,
        subtitle: str = "",
        *,
        meta: str = "",
        action_text: str = "",
        action_callback=None,
    ):
        self._mode = mode
        self._title.setText(title)
        self._subtitle.setText(subtitle or self._DEFAULT_SUBTITLES.get(mode, ""))
        self._meta.setText(meta)
        self._meta.setVisible(bool(meta))
        self._action.setText(action_text)
        self._action.setVisible(bool(action_text))
        self._action_callback = action_callback
        is_loading = mode == "loading"
        self._dot.setVisible(False)
        self._bull.setVisible(mode == "empty")
        self._skeleton.setVisible(is_loading)
        self._skeleton.set_running(is_loading)
        self._apply_style()

    def closeEvent(self, event):  # noqa: N802 - Qt API naming
        self._dispose()
        super().closeEvent(event)

    def deleteLater(self):
        self._dispose()
        super().deleteLater()

    def _dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._skeleton.set_running(False)
        self._dot.set_running(False)
        with suppress(AttributeError, RuntimeError, TypeError):
            self._theme_manager.sig_theme_changed.disconnect(self._on_theme_changed)


class TableStateWrapper(QWidget):
    """表格 + 状态覆盖层容器"""

    def __init__(self, table: QTableView, empty_title: str = "暂无数据", loading_title: str = "加载中..."):
        super().__init__(table.parent())
        self._table = table
        self._empty_title = empty_title
        self._loading_title = loading_title
        self._state_animation = None

        self._overlay = TableStateOverlay(self)

        stack = QStackedLayout(self)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.addWidget(self._table)
        stack.addWidget(self._overlay)
        self._stack = stack

        self.show_table()

    def _width_limit(self) -> int:
        limit = self._table.maximumWidth()
        return limit if limit and limit < 16777215 else self._table.sizeHint().width()

    def sizeHint(self) -> QSize:
        table_hint = self._table.sizeHint()
        overlay_hint = self._overlay.sizeHint()
        width = min(max(table_hint.width(), overlay_hint.width()), self._width_limit())
        height = max(table_hint.height(), overlay_hint.height())
        return QSize(width, height)

    def minimumSizeHint(self) -> QSize:
        table_hint = self._table.minimumSizeHint()
        overlay_hint = self._overlay.minimumSizeHint()
        width = min(max(table_hint.width(), overlay_hint.width()), self._width_limit())
        height = max(table_hint.height(), overlay_hint.height())
        return QSize(width, height)

    @property
    def table(self):
        return self._table

    def show_table(self):
        self._set_current_widget(self._table)

    def show_empty(self, title: str | None = None, subtitle: str = ""):
        self._overlay.set_state("empty", title or self._empty_title, subtitle)
        self._set_current_widget(self._overlay)

    def show_loading(self, title: str | None = None, subtitle: str = ""):
        self._overlay.set_state("loading", title or self._loading_title, subtitle)
        self._set_current_widget(self._overlay)

    def show_offline(self, title: str = "离线模式", subtitle: str = ""):
        self._overlay.set_state("offline", title, subtitle)
        self._set_current_widget(self._overlay)

    def show_error(
        self,
        title: str = "加载失败",
        subtitle: str = "",
        *,
        meta: str = "",
        action_text: str = "",
        action_callback=None,
    ):
        self._overlay.set_state(
            "error",
            title,
            subtitle,
            meta=meta,
            action_text=action_text,
            action_callback=action_callback,
        )
        self._set_current_widget(self._overlay)

    def show_cached(
        self,
        title: str = "显示缓存数据",
        subtitle: str = "",
        *,
        meta: str = "",
        action_text: str = "",
        action_callback=None,
    ):
        self._overlay.set_state(
            "cached",
            title,
            subtitle,
            meta=meta,
            action_text=action_text,
            action_callback=action_callback,
        )
        self._set_current_widget(self._overlay)

    def show_success(self, title: str = "更新完成", subtitle: str = "", *, meta: str = ""):
        self._overlay.set_state("success", title, subtitle, meta=meta)
        self._set_current_widget(self._overlay)

    def show_info(self, title: str = "状态更新", subtitle: str = "", *, meta: str = ""):
        self._overlay.set_state("info", title, subtitle, meta=meta)
        self._set_current_widget(self._overlay)

    def _set_current_widget(self, widget: QWidget) -> None:
        if self._stack.currentWidget() is widget:
            return
        self._stack.setCurrentWidget(widget)
        self._fade_in_widget(widget)

    def _fade_in_widget(self, widget: QWidget) -> None:
        _stop_state_animation(self)

        # QGraphicsOpacityEffect forces item views to render their whole viewport
        # into an off-screen surface on every animation frame.  Keep state-overlay
        # motion, but reveal data tables directly so a cache delivery cannot turn
        # into several full-table repaints on the GUI thread.
        if widget is self._table:
            if widget.graphicsEffect() is not None:
                widget.setGraphicsEffect(None)
            return

        if not self.isVisible() or widget.width() <= 0 or widget.height() <= 0:
            return

        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)

        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(build_ui_tokens()["motion"]["fast"])
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _cleanup():
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
            self._state_animation = None

        animation.finished.connect(_cleanup)
        self._state_animation = animation
        animation.start()
