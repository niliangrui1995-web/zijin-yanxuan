# ui/components.py - 通用 UI 组件
# 从 main_window_qt.py 拆分出来的独立工具类
from PyQt6.QtWidgets import (
    QFrame, QPushButton, QTableWidgetItem,
    QStyleOption, QStyle, QWidget
)
from PyQt6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QTimer,
    QParallelAnimationGroup, pyqtProperty
)
from PyQt6.QtGui import QPainter, QColor, QBrush


class NumericTableWidgetItem(QTableWidgetItem):
    """支持数值排序的 QTableWidgetItem
    对含 %/+/亿/万 等后缀的文本提取数值进行比较,
    无法解析时退化为字符串比较.
    """
    def __init__(self, text=""):
        super().__init__(str(text))
        from PyQt6.QtGui import QFont
        font = QFont()
        # 兼容包含中文字符时的等宽显示(如市值 450亿)
        font.setFamilies(["Consolas", "Microsoft YaHei UI", "monospace"])
        font.setPointSize(10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)

    def __lt__(self, other):
        try:
            val1 = self._to_float()
            val2 = other._to_float() if hasattr(other, '_to_float') else float('-inf')
            return val1 < val2
        except Exception as _e:
            import logging
            logging.getLogger(__name__).debug(f"[排序] 数值比较失败，退化为字符串排序: {_e}")
            return super().__lt__(other)

    def _to_float(self):
        import re
        t = self.text().replace(',', '')
        if not t or t in ('--', '-'):
            return float('-inf')
        m = re.search(r'([-+]?\d*\.?\d+)', t)
        if m:
            return float(m.group(1))
        return float('-inf')


class GlassPanel(QFrame):
    """毛玻璃质感面板，提供深邃和空间层级感"""
    def __init__(self, parent=None, radius=12, alpha=0.90):
        super().__init__(parent)
        self.setObjectName("glassPanel")
        self._radius = radius
        self._alpha = alpha
        # 让样式表背景生效
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            QFrame#glassPanel {{
                background-color: rgba(18, 20, 26, {self._alpha});
                border-radius: {self._radius}px;
                border: 1px solid rgba(255, 255, 255, 0.04);
            }}
        """)
        # 不再应用 QGraphicsDropShadowEffect
        # 原因：在嵌套 QSplitter 布局中会触发 Qt6 底层渲染崩溃 (C++ SegFault)

class AnimatedHoverButton(QPushButton):
    """滑动微动效按钮，提供 0.25s 弹性过渡 + 紫色光影效果"""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("animatedHoverBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)
        
        self._hover_progress = 0.0
        self._is_active = False
        
        self._anim = QPropertyAnimation(self, b"hoverProgress")
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(250)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        # 移除默认的样式表背景，完全由 paintEvent 接管
        self.setStyleSheet("""
            QPushButton#animatedHoverBtn {
                background: transparent;
                border: none;
                color: #A0AEC0;
                font-size: 13px;
                font-weight: 500;
                text-align: left;
                padding-left: 36px;
            }
            QPushButton#animatedHoverBtn:disabled {
                color: #4B5563;
            }
        """)

    @pyqtProperty(float)
    def hoverProgress(self):
        return self._hover_progress

    @hoverProgress.setter
    def hoverProgress(self, val):
        self._hover_progress = val
        self.update()

    def set_active(self, active: bool):
        self._is_active = active
        # 激活状态文字颜色变亮
        if active:
            self.setStyleSheet(self.styleSheet().replace("color: #A0AEC0;", "color: #FFFFFF; font-weight: bold;"))
        else:
            self.setStyleSheet(self.styleSheet().replace("color: #FFFFFF; font-weight: bold;", "color: #A0AEC0;"))
        self.update()

    def enterEvent(self, event):
        if self.isEnabled():
            self._anim.setDirection(QPropertyAnimation.Direction.Forward)
            self._anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self.isEnabled():
            self._anim.setDirection(QPropertyAnimation.Direction.Backward)
            self._anim.start()
        super().leaveEvent(event)

    def paintEvent(self, event):
        from PyQt6.QtGui import QPen, QLinearGradient
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        rect = self.rect()
        r = rect.adjusted(2, 1, -2, -1)
        
        if self._is_active:
            painter.setBrush(QColor(139, 92, 246, 30))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(r, 8, 8)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(139, 92, 246, 50), 1))
            painter.drawRoundedRect(r, 8, 8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(139, 92, 246))
            painter.drawRoundedRect(2, rect.height() // 4, 3, rect.height() // 2, 2, 2)
            glow = QLinearGradient(rect.width() * 0.2, rect.height(), rect.width() * 0.8, rect.height())
            glow.setColorAt(0, QColor(139, 92, 246, 0))
            glow.setColorAt(0.5, QColor(139, 92, 246, 60))
            glow.setColorAt(1, QColor(139, 92, 246, 0))
            painter.setBrush(QBrush(glow))
            painter.drawRect(int(rect.width() * 0.15), rect.height() - 2, int(rect.width() * 0.7), 2)
        else:
            base_alpha = 15
            border_alpha = 18
            painter.setBrush(QColor(255, 255, 255, base_alpha))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(r, 8, 8)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, border_alpha), 1))
            painter.drawRoundedRect(r, 8, 8)

            if self._hover_progress > 0:
                p = self._hover_progress
                painter.setBrush(QColor(139, 92, 246, int(p * 35)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(r, 8, 8)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(139, 92, 246, int(p * 40)), 1))
                painter.drawRoundedRect(r, 8, 8)
                glow = QLinearGradient(rect.width() * 0.25, rect.height(), rect.width() * 0.75, rect.height())
                glow.setColorAt(0, QColor(167, 139, 250, 0))
                glow.setColorAt(0.5, QColor(167, 139, 250, int(p * 45)))
                glow.setColorAt(1, QColor(167, 139, 250, 0))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(glow))
                painter.drawRect(int(rect.width() * 0.2), rect.height() - 2, int(rect.width() * 0.6), 2)
                
        painter.end()
        super().paintEvent(event)

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

class AnimatedCard(QFrame):
    """可复用的动画卡片组件，入场动画+Hover微交互"""
    def __init__(self, parent=None, delay=0):
        super().__init__(parent)
        self.setObjectName("moduleCard")
        self.delay = delay
        
        # 不再应用 QGraphicsOpacityEffect / QGraphicsDropShadowEffect
        # 原因：在嵌套 QSplitter 布局中会触发 Qt6 底层渲染崩溃 (C++ SegFault)
        self._opacity = 1.0
        self._y_offset = 0

        self.enter_anim_group = QParallelAnimationGroup(self)
        
        self.fade_anim = QPropertyAnimation(self, b"opacityStr")
        self.fade_anim.setDuration(600)
        self.fade_anim.setStartValue(0.0)
        self.fade_anim.setEndValue(1.0)
        self.fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        self.pos_anim = QPropertyAnimation(self, b"yOffset")
        self.pos_anim.setDuration(600)
        self.pos_anim.setStartValue(20)
        self.pos_anim.setEndValue(0)
        self.pos_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        
        self.enter_anim_group.addAnimation(self.fade_anim)
        self.enter_anim_group.addAnimation(self.pos_anim)

        # 所有 delay 都通过 QTimer.singleShot 延迟——在 __init__ 中直接 start()
        # 会触发 "non-existing property" 错误，因为 pyqtProperty 的元类注册还没完成
        QTimer.singleShot(max(delay, 50), self.enter_anim_group.start)

    @pyqtProperty(int)
    def yOffset(self):
        return self._y_offset

    @yOffset.setter
    def yOffset(self, val):
        self._y_offset = val
        self.update()
        
    @pyqtProperty(float)
    def opacityStr(self):
        return self._opacity
        
    @opacityStr.setter
    def opacityStr(self, val):
        self._opacity = val
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self._opacity)
        painter.translate(0, self._y_offset)
        
        opt = QStyleOption()
        opt.initFrom(self)
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, painter, self)
        
        painter.end()

    def toggle_collapse(self, is_collapsed: bool, expanded_height: int = 400):
        """控制卡片的折叠与展开动画"""
        if not hasattr(self, '_collapse_anim'):
            self._collapse_anim = QPropertyAnimation(self, b"maximumHeight")
            self._collapse_anim.setDuration(300)
            self._collapse_anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
            
        self._collapse_anim.stop()
        if is_collapsed:
            self._collapse_anim.setStartValue(self.height())
            self._collapse_anim.setEndValue(48)
        else:
            self._collapse_anim.setStartValue(self.height())
            self._collapse_anim.setEndValue(expanded_height)
        self._collapse_anim.start()


class SvgIconBuilder:
    """构建无失真矢量的纯净 SVG 图标（替代原始 Emoji）"""
    @staticmethod
    def create_icon(svg_str: str, color: str = "#8B5CF6", size: int = 16):
        from PyQt6.QtSvg import QSvgRenderer
        from PyQt6.QtGui import QPixmap, QIcon, QPainter
        from PyQt6.QtCore import Qt, QByteArray

        if 'currentColor' in svg_str:
            svg_str = svg_str.replace('currentColor', color)
        
        renderer = QSvgRenderer(QByteArray(svg_str.encode('utf-8')))
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        
        return QIcon(pixmap)

    @staticmethod
    def gear(color="#A0AEC0"):
        return SvgIconBuilder.create_icon(
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>',
            color
        )

    @staticmethod
    def download(color="#10B981"):
        return SvgIconBuilder.create_icon(
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>',
            color
        )


class SlidingDrawer(QWidget):
    """边缘向下滑入/滑出的抽屉容器"""
    def __init__(self, parent=None, expanded_height=120):
        super().__init__(parent)
        self.expanded_height = expanded_height
        self.setFixedHeight(0)
        
        from PyQt6.QtWidgets import QVBoxLayout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 0, 16, 8)
        
        self.panel = GlassPanel(self, radius=8, alpha=0.95)
        main_layout.addWidget(self.panel, alignment=Qt.AlignmentFlag.AlignTop)
        
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
        self.anim = QPropertyAnimation(self, b"maximumHeight")
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.setDuration(300)
        self._is_open = False

    def set_content_layout(self, layout):
        self.panel.setLayout(layout)

    def toggle(self):
        self.anim.stop()
        if self._is_open:
            self.anim.setStartValue(self.height())
            self.anim.setEndValue(0)
        else:
            self.anim.setStartValue(self.height())
            self.anim.setEndValue(self.expanded_height)
        self._is_open = not self._is_open
        self.anim.start()

    def close_drawer(self):
        if self._is_open:
            self.toggle()

class SearchFilter:
    @staticmethod
    def match_pinyin_or_text(search_val, code_text, name_text):
        """辅助方法: 判断 search_val 是否匹配代码、名称或拼音首字母"""
        if not search_val:
            return True
        if search_val in code_text or search_val in name_text:
            return True
            
        import pypinyin
        py_initials = "".join(pypinyin.lazy_pinyin(name_text, style=pypinyin.Style.FIRST_LETTER)).lower()
        return search_val in py_initials
