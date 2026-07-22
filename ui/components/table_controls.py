"""Table-oriented widgets shared by the UI package."""

import logging
import math
import time
from contextlib import suppress

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QItemSelectionModel,
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
    QPen,
    QPolygonF,
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
        self.horizontalHeader().setMaximumSectionSize(max_w)
        self.setMaximumWidth(max_w)
        self.updateGeometry()

    def _on_sort_indicator_changed(self, column: int, _order):
        self._sorted_column = column
        self.viewport().update()

    def sorted_column(self) -> int:
        return self._sorted_column

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(min(hint.width(), self._screen_width_limit()), hint.height())

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), self._screen_width_limit()), hint.height())

    def apply_density(self, mode: str | None = None):
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
        self._apply_screen_width_limit()
        self.style().unpolish(self)
        self.style().polish(self)
        self.viewport().update()

    def setModel(self, model):
        self._disconnect_refresh_model()
        super().setModel(model)
        self._connect_refresh_model(model)

    def _connect_refresh_model(self, model) -> None:
        if model is None:
            return
        self._bound_refresh_model = model
        for signal_name, slot in (
            ("modelAboutToBeReset", self._capture_refresh_state),
            ("layoutAboutToBeChanged", self._capture_refresh_state),
            ("modelReset", self._schedule_refresh_state_restore),
            ("layoutChanged", self._schedule_refresh_state_restore),
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
            ("modelReset", self._schedule_refresh_state_restore),
            ("layoutChanged", self._schedule_refresh_state_restore),
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
                    header.restoreState(header_state)

            sort_column = int(snapshot.get("proxy_sort_column", snapshot.get("sort_column", -1)) or -1)
            sort_order = snapshot.get("proxy_sort_order", snapshot.get("sort_order", Qt.SortOrder.AscendingOrder))
            if sort_column >= 0:
                with suppress(AttributeError, RuntimeError, TypeError, ValueError):
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
        roles = _args[2] if len(_args) >= 3 else None
        flash_role = int(Qt.ItemDataRole.UserRole) + 1
        if roles:
            role_values = {int(getattr(role, "value", role)) for role in roles}
            if flash_role not in role_values:
                return
        if not self._model_has_active_flash_records():
            return
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

    def schedule_flash_repaint_until(self, active_until: float) -> None:
        if self._coalesced_flash_repaint and not self.isVisible():
            return
        self._flash_repaint_until = max(self._flash_repaint_until, float(active_until))
        if self._coalesced_flash_repaint:
            remaining_ms = max(1, int((self._flash_repaint_until - time.time()) * 1000))
            self._flash_repaint_timer.start(remaining_ms)
        elif not self._flash_repaint_timer.isActive():
            self._flash_repaint_timer.start()

    def _tick_flash_repaint(self) -> None:
        if self._closing:
            self._flash_repaint_timer.stop()
            return
        if self._coalesced_flash_repaint:
            self._flash_repaint_timer.stop()
            self._flash_repaint_until = 0.0
            self.viewport().update()
            return
        if time.time() >= self._flash_repaint_until:
            self._flash_repaint_timer.stop()
            self._flash_repaint_until = 0.0
            self.viewport().update()
            return
        self.viewport().update()

    def showEvent(self, event):
        if self._closing:
            return
        self._apply_screen_width_limit()
        self._sync_ambient_repaint_timer()
        super().showEvent(event)

    def hideEvent(self, event):
        self._ambient_repaint_timer.stop()
        if self._coalesced_flash_repaint:
            self._flash_repaint_timer.stop()
            self._flash_repaint_until = 0.0
        super().hideEvent(event)

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
