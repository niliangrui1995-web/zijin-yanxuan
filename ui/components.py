# ui/components.py - 通用 UI 组件
# 从 main_window_qt.py 拆分出来的独立工具类
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QTableWidgetItem,
    QStyleOption, QStyle, QWidget, QGraphicsOpacityEffect,
    QGraphicsDropShadowEffect
)
from PyQt6.QtCore import (
    Qt, QPoint, QPropertyAnimation, QEasingCurve, QTimer,
    QParallelAnimationGroup, pyqtProperty
)
from PyQt6.QtGui import QPainter, QColor, QBrush


class NumericTableWidgetItem(QTableWidgetItem):
    """支持数值排序的 QTableWidgetItem
    对含 %/+/亿/万 等后缀的文本提取数值进行比较,
    无法解析时退化为字符串比较.
    """
    def __lt__(self, other):
        try:
            val1 = self._to_float()
            val2 = other._to_float() if hasattr(other, '_to_float') else float('-inf')
            return val1 < val2
        except Exception:
            return super().__lt__(other)

    def _to_float(self):
        t = self.text().replace('%', '').replace('+', '').replace('亿', '').replace('万', '').replace('⭐', '').replace('🚀', '').replace('⏳', '').replace('⚠️', '').strip()
        if t in ('', '--', '--', '-'):
            return float('-inf')
        try:
            return float(t)
        except ValueError:
            return float('-inf')

class CustomTitleBar(QFrame):
    """
    自定义的深色深邃标题栏,接管拖拽,最小化,最大化与关闭功能.
    高度契合暗黑风格.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_window = parent
        self.setObjectName("customTitleBar")
        self.setFixedHeight(36)
        
        # 允许标题栏背景颜色通过 PaintEvent 生效
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QFrame#customTitleBar {
                background-color: #101216;
                border-bottom: 1px solid qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 rgba(59, 130, 246, 0.4),
                    stop:0.5 rgba(147, 197, 253, 0.15),
                    stop:1 rgba(59, 130, 246, 0.4));
            }
            QLabel#titleText {
                color: #A0A5B2;
                font-size: 13px;
                font-weight: 600;
                padding-left: 12px;
                letter-spacing: 0.5px;
            }
            QPushButton[class="titleBtn"] {
                background: transparent;
                border: none;
                color: #4B5563;
                font-size: 15px;
                width: 44px;
                height: 38px;
            }
            QPushButton[class="titleBtn"]:hover {
                background: rgba(59, 130, 246, 0.08);
                color: #93C5FD;
            }
            QPushButton#btnTitleClose:hover {
                background: rgba(220, 38, 38, 0.85);
                color: #FFFFFF;
            }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # 图标预留或者直接文本
        self.lbl_title = QLabel("  紫金研选量化终端")
        self.lbl_title.setObjectName("titleText")
        
        # 按钮组合
        self.btn_min = QPushButton("─")
        self.btn_min.setProperty("class", "titleBtn")
        self.btn_min.clicked.connect(self.parent_window.showMinimized)
        
        self.btn_max = QPushButton("□")
        self.btn_max.setProperty("class", "titleBtn")
        self.btn_max.clicked.connect(self.toggle_maximize)
        
        self.btn_close = QPushButton("×")
        self.btn_close.setObjectName("btnTitleClose")
        self.btn_close.setProperty("class", "titleBtn")
        # 更大且稍微不同的 X 字体
        font = self.btn_close.font()
        font.setPointSize(16)
        self.btn_close.setFont(font)
        self.btn_close.clicked.connect(self.parent_window.close)
        
        layout.addWidget(self.lbl_title)
        layout.addStretch()
        layout.addWidget(self.btn_min)
        layout.addWidget(self.btn_max)
        layout.addWidget(self.btn_close)
        
        self._is_tracking = False
        self._start_pos = QPoint()
        
    def toggle_maximize(self):
        if self.parent_window.isMaximized():
            self.parent_window.showNormal()
            self.btn_max.setText("□")
        else:
            self.parent_window.showMaximized()
            self.btn_max.setText("❐")
            
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_tracking = True
            # 获取相对于桌面的全局坐标减去窗口当前的全局坐标,以确定相对偏移量
            # 兼容 Qt6 pos() 行为
            self._start_pos = event.globalPosition().toPoint() - self.parent_window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._is_tracking and event.buttons() == Qt.MouseButton.LeftButton:
            if self.parent_window.isMaximized():
                # 拖动时自动恢复普通尺寸
                self.parent_window.showNormal()
                self.btn_max.setText("□")
                # 重新计算 _start_pos
                self._start_pos = QPoint(self.width() // 2, self.height() // 2)
            self.parent_window.move(event.globalPosition().toPoint() - self._start_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._is_tracking = False
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximize()

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
        # 悬浮投影
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setXOffset(0)
        shadow.setYOffset(5)
        shadow.setColor(QColor(0, 0, 0, 140))
        self.setGraphicsEffect(shadow)

class AnimatedHoverButton(QPushButton):
    """滑动微动效按钮，提供 0.25s 弹性过渡 + 紫色光影效果"""
    def __init__(self, text="", parent=None, icon_text=""):
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
            # 激活态：品牌紫背景 + 微光边框 + 左侧指示条 + 底部发光
            painter.setBrush(QColor(139, 92, 246, 30))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(r, 8, 8)
            
            # 微光边框
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(139, 92, 246, 50), 1))
            painter.drawRoundedRect(r, 8, 8)
            
            # 左侧品牌紫指示条
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(139, 92, 246))
            painter.drawRoundedRect(2, rect.height() // 4, 3, rect.height() // 2, 2, 2)
            
            # 底部发光线
            glow = QLinearGradient(rect.width() * 0.2, rect.height(), rect.width() * 0.8, rect.height())
            glow.setColorAt(0, QColor(139, 92, 246, 0))
            glow.setColorAt(0.5, QColor(139, 92, 246, 60))
            glow.setColorAt(1, QColor(139, 92, 246, 0))
            painter.setBrush(QBrush(glow))
            painter.drawRect(int(rect.width() * 0.15), rect.height() - 2, int(rect.width() * 0.7), 2)
            
        else:
            # 默认态：统一暗色底、细边框，让按钮有实体感
            base_alpha = 15  # 微弱底色
            border_alpha = 18  # 微弱边框
            painter.setBrush(QColor(255, 255, 255, base_alpha))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(r, 8, 8)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, border_alpha), 1))
            painter.drawRoundedRect(r, 8, 8)

            if self._hover_progress > 0:
                p = self._hover_progress
                
                # 悬停叠加：紫色微光背景
                painter.setBrush(QColor(139, 92, 246, int(p * 35)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(r, 8, 8)
                
                # hover 边框微光
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(139, 92, 246, int(p * 40)), 1))
                painter.drawRoundedRect(r, 8, 8)
                
                # hover 底部发光线
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
        self.anim.setLoopCount(-1)  # 无限循环
        
        # 错开动画启动，避免多个点同步显得死板
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
        
        # 发光外圈（柔和光晕）
        glow_color = QColor(self.dot_color)
        glow_color.setAlphaF(self._opacity * 0.3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow_color))
        painter.drawEllipse(self.rect().center(), int(self._radius + 3), int(self._radius + 3))
        
        # 内核实心点
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
        
        # 初始透明度和偏移设置
        self.op_effect = QGraphicsOpacityEffect(self)
        self.op_effect.setOpacity(0.0)
        
        # 悬浮投影
        self.shadow_effect = QGraphicsDropShadowEffect(self)
        self.shadow_effect.setBlurRadius(24)
        self.shadow_effect.setXOffset(0)
        self.shadow_effect.setYOffset(6)
        self.shadow_effect.setColor(QColor(0, 0, 0, 150))
        
        # 由于一个 Widget 只能有一个 GraphicsEffect，我们通过重写 paintEvent 处理透明度和位移
        # 这里为保持简单，保留透明度效果为主要 effect，阴影效果通过 StyleSheet 辅助或直接舍弃 GraphicsEffect 组合
        # 最佳实践：将 shadow_effect 应用于自身
        self.setGraphicsEffect(self.shadow_effect)
        self._opacity = 0.0
        self._y_offset = 20

        # 入场动画组
        self.enter_anim_group = QParallelAnimationGroup(self)
        
        # 1. 透明度淡入
        self.fade_anim = QPropertyAnimation(self, b"opacityStr")
        self.fade_anim.setDuration(600)
        self.fade_anim.setStartValue(0.0)
        self.fade_anim.setEndValue(1.0)
        self.fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        
        # 2. Y轴上浮
        self.pos_anim = QPropertyAnimation(self, b"yOffset")
        self.pos_anim.setDuration(600)
        self.pos_anim.setStartValue(20)
        self.pos_anim.setEndValue(0)
        self.pos_anim.setEasingCurve(QEasingCurve.Type.OutBack)
        
        self.enter_anim_group.addAnimation(self.fade_anim)
        self.enter_anim_group.addAnimation(self.pos_anim)

        # 延迟触发入场动画
        if delay > 0:
            QTimer.singleShot(delay, self.enter_anim_group.start)
        else:
            self.enter_anim_group.start()

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
        # 覆写 paintEvent 以应用 translateY 和透明度
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self._opacity)
        painter.translate(0, self._y_offset)
        
        # 让 QFrame 的原生 StyleSheet 绘制在偏移后的画布上
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
            self._collapse_anim.setEndValue(48)  # 仅保留标题栏高度
        else:
            self._collapse_anim.setStartValue(self.height())
            self._collapse_anim.setEndValue(expanded_height)
        self._collapse_anim.start()

