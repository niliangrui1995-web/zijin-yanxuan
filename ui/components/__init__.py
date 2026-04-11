# ui/components.py - 通用 UI 组件
# 从 main_window_qt.py 拆分出来的独立工具类
from functools import lru_cache

from PyQt6.QtWidgets import QTableView, QAbstractItemView, QWidget, QToolTip
from PyQt6.QtCore import Qt, QTimer, QEvent, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QPainter, QColor, QBrush, QPalette


class VCPTableView(QTableView):
    """
    紫金研选统一表格组件 (VCPTableView)
    """

    def __init__(self, parent=None, default_row_height: int = None):
        super().__init__(parent)
        self._init_common_styles(default_row_height)
        from ui.theme import theme_manager
        theme_manager.sig_theme_changed.connect(self._on_theme_changed)

    def _init_common_styles(self, default_row_height: int):
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSortingEnabled(True)

        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        max_w = screen.geometry().width() if screen else 1920
        self.horizontalHeader().setMaximumSectionSize(max_w)
        self.setMaximumWidth(max_w)

        self._apply_runtime_style()

        if default_row_height is not None:
            self.verticalHeader().setDefaultSectionSize(default_row_height)

    def _tooltip_qss(self) -> str:
        from ui.theme import theme_manager
        t = theme_manager.current_theme
        return (
            "QTableView::item { padding: 0px 10px; }\n"
            "QToolTip {"
            f" background-color: {t['BG_ELEVATED']};"
            f" color: {t['TEXT_PRIMARY']};"
            f" border: 1px solid {t['BORDER_DEFAULT']};"
            " border-radius: 8px;"
            " padding: 7px 10px;"
            " margin: 0px;"
            " }"
        )

    def _apply_runtime_style(self):
        self.setStyleSheet(self._tooltip_qss())

    def _on_theme_changed(self, _theme_name: str):
        self._apply_runtime_style()
        self.style().unpolish(self)
        self.style().polish(self)
        self.viewport().update()

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
