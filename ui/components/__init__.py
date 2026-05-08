# ui/components.py - 通用 UI 组件
# 从 main_window_qt.py 拆分出来的独立工具类
import time
from functools import lru_cache

from PyQt6.QtCore import QEasingCurve, QEvent, QItemSelectionModel, QPropertyAnimation, QSize, Qt, QTimer, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QFont, QFontMetrics, QPainter, QPalette
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
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ui.theme_tokens import build_ui_tokens, get_state_tone


class VCPTableView(QTableView):
    """
    紫金研选统一表格组件 (VCPTableView)
    """

    def __init__(self, parent=None, default_row_height: int = None):
        super().__init__(parent)
        self._base_row_height = None
        self._refresh_state_snapshot = None
        self._restoring_refresh_state = False
        self._bound_refresh_model = None
        self._flash_repaint_until = 0.0
        self._flash_repaint_timer = QTimer(self)
        self._flash_repaint_timer.setInterval(60)
        self._flash_repaint_timer.timeout.connect(self._tick_flash_repaint)
        self._init_common_styles(default_row_height)
        from ui.theme import theme_manager
        theme_manager.sig_theme_changed.connect(self._on_theme_changed)

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
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._sorted_column = -1
        header.setHighlightSections(False)
        header.setSectionsClickable(True)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header.sortIndicatorChanged.connect(self._on_sort_indicator_changed)
        self._apply_screen_width_limit()

        self._apply_runtime_style()

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

    def _tooltip_qss(self) -> str:
        tokens = build_ui_tokens()
        t = tokens["theme"]
        return (
            f"QTableView::item {{ padding: {tokens['table']['cell_padding_y']}px {tokens['table']['cell_padding_x']}px; }}\n"
            "QToolTip {"
            f" background-color: {t['BG_ELEVATED']};"
            f" color: {t['TEXT_PRIMARY']};"
            f" border: 1px solid {t['BORDER_DEFAULT']};"
            f" border-radius: 0px;"
            f" padding: {tokens['space']['sm']}px {tokens['space']['md']}px;"
            f" font-size: {tokens['font']['size_lg']}px;"
            f" font-family: {tokens['font']['family']};"
            " margin: 0px;"
            " }"
        )

    def _apply_runtime_style(self):
        self.setStyleSheet(self._tooltip_qss())

    def _on_theme_changed(self, _theme_name: str):
        self._apply_screen_width_limit()
        self._apply_runtime_style()
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
            try:
                signal.connect(slot)
            except (TypeError, RuntimeError):
                pass

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
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._bound_refresh_model = None

    def _model_header_text(self, model, column: int) -> str:
        try:
            return str(model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or "").strip()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return ""

    def _code_column(self, model) -> int:
        if model is None:
            return -1
        try:
            column_count = int(model.columnCount())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return -1
        for column in range(column_count):
            if self._model_header_text(model, column) == "代码":
                return column
        return -1

    def _row_identity(self, row: int) -> str:
        model = self.model()
        code_column = self._code_column(model)
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
        if model is None:
            self._refresh_state_snapshot = None
            return

        current = self.currentIndex()
        selected_rows = []
        selected_codes = []
        selection_model = self.selectionModel()
        if selection_model is not None:
            try:
                selected_rows = [index.row() for index in selection_model.selectedRows()]
            except (AttributeError, RuntimeError, TypeError, ValueError):
                selected_rows = []
            selected_codes = [
                self._row_identity(row)
                for row in selected_rows
                if self._row_identity(row)
            ]

        header = self.horizontalHeader()
        proxy_sort_column = -1
        proxy_sort_order = Qt.SortOrder.AscendingOrder
        if hasattr(model, "sortColumn"):
            try:
                proxy_sort_column = int(model.sortColumn())
                proxy_sort_order = model.sortOrder()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                proxy_sort_column = -1

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
        QTimer.singleShot(0, lambda snapshot=dict(snapshot): self._restore_refresh_state(snapshot))

    def _bounded_row(self, row: int) -> int:
        model = self.model()
        if model is None:
            return -1
        try:
            row_count = int(model.rowCount())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return -1
        if row_count <= 0:
            return -1
        return max(0, min(row_count - 1, int(row)))

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
                try:
                    header.restoreState(header_state)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass

            sort_column = int(snapshot.get("proxy_sort_column", snapshot.get("sort_column", -1)) or -1)
            sort_order = snapshot.get("proxy_sort_order", snapshot.get("sort_order", Qt.SortOrder.AscendingOrder))
            if sort_column >= 0:
                try:
                    self.sortByColumn(sort_column, sort_order)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass

            selection_model = self.selectionModel()
            if selection_model is not None:
                try:
                    selection_model.clearSelection()
                except (AttributeError, RuntimeError):
                    pass

            restored_rows = []
            for code in snapshot.get("selected_codes", []) or []:
                row = self._find_row_by_identity(code)
                if row >= 0 and row not in restored_rows:
                    restored_rows.append(row)
            if not restored_rows:
                restored_rows = [
                    self._bounded_row(row)
                    for row in (snapshot.get("selected_rows", []) or [])
                    if self._bounded_row(row) >= 0
                ]

            current_row = self._find_row_by_identity(snapshot.get("current_code", ""))
            if current_row < 0:
                current_row = self._bounded_row(int(snapshot.get("current_row", -1) or -1))
            current_col = max(0, int(snapshot.get("current_col", 0) or 0))

            if selection_model is not None:
                for row in restored_rows:
                    index = self.model().index(row, 0)
                    selection_model.select(
                        index,
                        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                    )

            if current_row >= 0:
                current_index = self.model().index(current_row, min(current_col, max(0, self.model().columnCount() - 1)))
                self.setCurrentIndex(current_index)

            self._restore_scrollbars(v_scroll, h_scroll)
            QTimer.singleShot(0, lambda v_scroll=v_scroll, h_scroll=h_scroll: self._restore_scrollbars(v_scroll, h_scroll))
        finally:
            self._restoring_refresh_state = False
            self._refresh_state_snapshot = None

    def _restore_scrollbars(self, v_scroll: int, h_scroll: int) -> None:
        if self.model() is None:
            return
        self.verticalScrollBar().setValue(v_scroll)
        self.horizontalScrollBar().setValue(h_scroll)

    def _on_model_data_changed(self, *_args) -> None:
        self._flash_repaint_until = max(self._flash_repaint_until, time.time() + 0.8)
        if not self._flash_repaint_timer.isActive():
            self._flash_repaint_timer.start()

    def _tick_flash_repaint(self) -> None:
        if time.time() >= self._flash_repaint_until:
            self._flash_repaint_timer.stop()
            self._flash_repaint_until = 0.0
            self.viewport().update()
            return
        self.viewport().update()

    def showEvent(self, event):
        self._apply_screen_width_limit()
        super().showEvent(event)

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

        display_text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not display_text:
            return False

        fm = QFontMetrics(self._display_font_for_index(index))
        visible_rect = self.visualRect(index)
        available_width = self.columnWidth(index.column())
        if visible_rect.isValid() and visible_rect.width() > 0:
            available_width = min(available_width, visible_rect.width())
        available_width = max(0, available_width - 14)
        if available_width <= 0:
            return False

        pill_color = index.data(Qt.ItemDataRole.UserRole + 2)
        if pill_color:
            required_width = fm.horizontalAdvance(display_text) + 12
            return required_width > available_width

        return fm.horizontalAdvance(display_text) > available_width

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            index = self.indexAt(event.pos())
            if index.isValid():
                tooltip_text = index.data(Qt.ItemDataRole.ToolTipRole)
                if tooltip_text and self._should_show_tooltip_for_index(index):
                    from ui.theme import theme_manager
                    t = theme_manager.current_theme
                    pal = QPalette(QToolTip.palette())
                    for group in (
                        QPalette.ColorGroup.Active,
                        QPalette.ColorGroup.Inactive,
                        QPalette.ColorGroup.Disabled,
                    ):
                        pal.setColor(group, QPalette.ColorRole.ToolTipBase, QColor(t["BG_ELEVATED"]))
                        pal.setColor(group, QPalette.ColorRole.ToolTipText, QColor(t["TEXT_PRIMARY"]))
                    QToolTip.setPalette(pal)
                    QToolTip.showText(event.globalPos(), str(tooltip_text), self.viewport(), self.visualRect(index))
                    return True
            QToolTip.hideText()
            event.ignore()
            return True
        return super().viewportEvent(event)


class PulsingDot(QWidget):
    """呼吸灯指示器组件"""

    def __init__(self, color="#10B981", parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.dot_color = QColor(color)
        self._radius = 3.5
        self._opacity = 1.0

        self.anim = QPropertyAnimation(self, b"opacity")
        self.anim.setDuration(1500)
        self.anim.setStartValue(0.2)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.anim.setLoopCount(-1)

        QTimer.singleShot(100, self.anim.start)

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
    if len(labels) <= max(1, int(inline_limit or 1)):
        body = separator.join(labels)
    else:
        body = f"{len(labels)}{count_suffix}"

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
        return {
            value
            for value, action in self._actions.items()
            if action.isChecked()
        }

    def selected_labels(self) -> list[str]:
        selected = self.selected_values()
        return [self._labels[value] for value in self.option_values() if value in selected]

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
        selected = {
            str(value or "").strip()
            for value in (values or [])
            if str(value or "").strip() in self._actions
        }

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
            {
                value
                for value, action in self._actions.items()
                if action.isChecked()
            },
            emit=True,
        )


class TableStateOverlay(QWidget):
    """统一空/加载状态覆盖层"""

    _DEFAULT_SUBTITLES = {
        "empty": "当前条件下暂无可显示内容，可调整筛选条件或稍后刷新。",
        "loading": "正在同步最新数据，请稍候。",
        "offline": "当前处于离线模式，仅展示本地缓存。",
        "cached": "本次未拿到新结果，当前展示的是最近一次成功数据。",
        "error": "本次加载失败，请检查网络或稍后重试。",
        "success": "最新数据已同步完成。",
        "info": "当前状态已更新。",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mode = "empty"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._card = QFrame(self)
        self._card.setObjectName("tableStateCard")
        self._card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._card.setMinimumWidth(360)
        self._card.setMaximumWidth(500)

        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(22, 18, 22, 18)
        card_layout.setSpacing(10)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._dot = PulsingDot(parent=self)
        self._dot.setVisible(False)

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

        self._action = QPushButton("")
        self._action.setVisible(False)
        self._action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action.setProperty("class", "secondary")
        self._action_callback = None
        self._action.clicked.connect(self._handle_action)

        card_layout.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self._title)
        card_layout.addWidget(self._subtitle)
        card_layout.addWidget(self._meta)
        card_layout.addWidget(self._action, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._card, 0, Qt.AlignmentFlag.AlignCenter)

        from ui.theme import theme_manager
        theme_manager.sig_theme_changed.connect(lambda _name: self._apply_style())
        self._apply_style()

    def _handle_action(self):
        if callable(self._action_callback):
            self._action_callback()

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
                border-radius: {tokens['radius']['xl']}px;
            }}
            """
        )
        self._title.setStyleSheet(
            f"color: {tone['fg'] if self._mode != 'empty' else t['TEXT_PRIMARY']};"
            f" font-size: {tokens['font']['size_lg']}px; font-weight: {tokens['font']['weight_bold']};"
        )
        self._subtitle.setStyleSheet(
            f"color: {t['TEXT_SECONDARY']}; font-size: {tokens['font']['size_sm']}px;"
        )
        self._meta.setStyleSheet(
            f"color: {t['TEXT_MUTED']}; font-size: {tokens['font']['size_sm']}px;"
            f" font-family: {tokens['font']['mono_family']};"
        )
        dot_color = tone["fg"] if self._mode in ("loading", "success", "warning", "error", "info", "cached") else t.get("COLOR_INFO", "#3B82F6")
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
        self._dot.setVisible(mode == "loading")
        self._apply_style()


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
        animation = self._state_animation
        if animation is not None:
            try:
                animation.stop()
            except RuntimeError:
                pass
            self._state_animation = None

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


class SearchFilter:
    @staticmethod
    @lru_cache(maxsize=4096)
    def _build_initial_options(name_text: str):
        import pypinyin

        options = []
        heteronym_groups = pypinyin.pinyin(
            name_text,
            style=pypinyin.Style.FIRST_LETTER,
            heteronym=True,
            errors=lambda item: list(str(item).lower()),
        )
        for group in heteronym_groups:
            normalized = {
                str(val).strip().lower()
                for val in group
                if str(val).strip()
            }
            if normalized:
                options.append(normalized)
        return tuple(options)

    @classmethod
    def _match_pinyin_initials(cls, search_val: str, name_text: str) -> bool:
        if not search_val or not name_text:
            return False

        initial_options = cls._build_initial_options(name_text)
        query = str(search_val).strip().lower()
        query_len = len(query)
        total = len(initial_options)
        if query_len == 0 or total == 0 or query_len > total:
            return False

        for start in range(total - query_len + 1):
            if all(query[offset] in initial_options[start + offset] for offset in range(query_len)):
                return True
        return False

    @staticmethod
    def match_pinyin_or_text(search_val, code_text, name_text):
        """辅助方法: 判断 search_val 是否匹配代码、名称或拼音首字母"""
        if not search_val:
            return True
        if search_val in code_text or search_val in name_text:
            return True

        return SearchFilter._match_pinyin_initials(search_val, name_text)
