# ui/components.py - 通用 UI 组件
# 从 main_window_qt.py 拆分出来的独立工具类
from functools import lru_cache

from PyQt6.QtWidgets import (
    QApplication, QTableView, QAbstractItemView, QWidget, QToolTip, QVBoxLayout, QStackedLayout, QLabel, QFrame
)
from PyQt6.QtCore import Qt, QSize, QTimer, QEvent, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QPainter, QColor, QBrush, QPalette

from ui.theme_tokens import build_ui_tokens, get_state_tone


class VCPTableView(QTableView):
    """
    紫金研选统一表格组件 (VCPTableView)
    """

    def __init__(self, parent=None, default_row_height: int = None):
        super().__init__(parent)
        self._base_row_height = None
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
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
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
            f" border-radius: {tokens['radius']['md']}px;"
            f" padding: {tokens['space']['sm']}px {tokens['space']['md']}px;"
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

    def showEvent(self, event):
        self._apply_screen_width_limit()
        super().showEvent(event)

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            index = self.indexAt(event.pos())
            if index.isValid():
                tooltip_text = index.data(Qt.ItemDataRole.ToolTipRole)
                if tooltip_text:
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


class TableStateOverlay(QWidget):
    """统一空/加载状态覆盖层"""

    _DEFAULT_SUBTITLES = {
        "empty": "当前条件下暂无可显示内容，可调整筛选条件或稍后刷新。",
        "loading": "正在同步最新数据，请稍候。",
        "offline": "当前处于离线模式，仅展示本地缓存。",
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

        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(6)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._dot = PulsingDot(parent=self)
        self._dot.setVisible(False)

        self._title = QLabel("")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._subtitle = QLabel("")
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle.setWordWrap(True)

        card_layout.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self._title)
        card_layout.addWidget(self._subtitle)
        layout.addWidget(self._card, 0, Qt.AlignmentFlag.AlignCenter)

        from ui.theme import theme_manager
        theme_manager.sig_theme_changed.connect(lambda _name: self._apply_style())
        self._apply_style()

    def _apply_style(self):
        tokens = build_ui_tokens()
        t = tokens["theme"]
        tone = get_state_tone(self._mode)
        card_border = tone["border"] if self._mode != "empty" else t["BORDER_DEFAULT"]
        card_bg = tone["bg"] if self._mode != "empty" else t["BG_ELEVATED"]
        self._card.setStyleSheet(
            f"""
            QFrame#tableStateCard {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: {tokens['radius']['lg']}px;
            }}
            """
        )
        self._title.setStyleSheet(
            f"color: {tone['fg'] if self._mode != 'empty' else t['TEXT_PRIMARY']};"
            f" font-size: {tokens['font']['size_md']}px; font-weight: {tokens['font']['weight_semibold']};"
        )
        self._subtitle.setStyleSheet(
            f"color: {t['TEXT_SECONDARY']}; font-size: {tokens['font']['size_xs']}px;"
        )
        dot_color = tone["fg"] if self._mode in ("loading", "success", "warning", "error", "info") else t.get("COLOR_INFO", "#3B82F6")
        self._dot.set_color(dot_color)

    def set_state(self, mode: str, title: str, subtitle: str = ""):
        self._mode = mode
        self._title.setText(title)
        self._subtitle.setText(subtitle or self._DEFAULT_SUBTITLES.get(mode, ""))
        self._dot.setVisible(mode == "loading")
        self._apply_style()


class TableStateWrapper(QWidget):
    """表格 + 状态覆盖层容器"""

    def __init__(self, table: QTableView, empty_title: str = "暂无数据", loading_title: str = "加载中..."):
        super().__init__(table.parent())
        self._table = table
        self._empty_title = empty_title
        self._loading_title = loading_title

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
        self._stack.setCurrentWidget(self._table)

    def show_empty(self, title: str | None = None, subtitle: str = ""):
        self._overlay.set_state("empty", title or self._empty_title, subtitle)
        self._stack.setCurrentWidget(self._overlay)

    def show_loading(self, title: str | None = None, subtitle: str = ""):
        self._overlay.set_state("loading", title or self._loading_title, subtitle)
        self._stack.setCurrentWidget(self._overlay)

    def show_offline(self, title: str = "离线模式", subtitle: str = ""):
        self._overlay.set_state("offline", title, subtitle)
        self._stack.setCurrentWidget(self._overlay)


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
