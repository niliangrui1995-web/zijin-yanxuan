# ui/splash_screen.py - 启动闪屏组件
# 程序启动时立即显示，减少用户等待焦虑
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QLabel, QProgressBar, QVBoxLayout, QWidget

from app.services import APP_VERSION


class SplashScreen(QWidget):
    """
    启动闪屏：深色科技风格，居中显示 Logo + 加载状态 + 进度条。
    在主窗口初始化完成前展示，避免双击后长时间无反馈。
    """

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.SplashScreen
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(420, 380)

        # 居中到屏幕
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                (geo.width() - self.width()) // 2,
                (geo.height() - self.height()) // 2,
            )

        # 布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 32, 40, 24)
        layout.setSpacing(0)

        # 图标（使用 QIcon 提取 ICO 内最大分辨率）
        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet("background: transparent;")
        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "bull_icon.ico"
        )
        if os.path.exists(icon_path):
            from PyQt6.QtGui import QIcon
            icon = QIcon(icon_path)
            # 请求 128x128 以获取 ICO 内最高分辨率
            pixmap = icon.pixmap(128, 128)
            icon_label.setPixmap(pixmap)
        icon_label.setFixedHeight(136)
        layout.addWidget(icon_label)

        layout.addSpacing(8)

        # 品牌标题
        self.lbl_brand = QLabel("紫金研选")
        self.lbl_brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_brand.setStyleSheet(
            "font-size: 28px; font-weight: 900; color: #93C5FD; "
            "letter-spacing: 0px; background: transparent;"
        )
        layout.addWidget(self.lbl_brand)

        # 副标题
        self.lbl_sub = QLabel("量化终端")
        self.lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_sub.setStyleSheet(
            "font-size: 13px; color: #6B7280; letter-spacing: 0px; "
            "margin-top: 4px; background: transparent;"
        )
        layout.addWidget(self.lbl_sub)

        layout.addSpacing(30)

        # 加载状态文字
        self.lbl_status = QLabel("正在初始化...")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setStyleSheet(
            "font-size: 11px; color: #4B5563; background: transparent;"
        )
        layout.addWidget(self.lbl_status)

        layout.addSpacing(10)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(3)
        self.progress.setStyleSheet("""
            QProgressBar {
                background-color: rgba(255, 255, 255, 0.06);
                border: none;
                border-radius: 1px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #2563EB, stop:0.5 #60A5FA, stop:1 #93C5FD);
                border-radius: 1px;
            }
        """)
        layout.addWidget(self.progress)

        layout.addSpacing(12)

        # 版本号
        self.lbl_ver = QLabel(f"v{APP_VERSION}")
        self.lbl_ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_ver.setStyleSheet(
            "font-size: 10px; color: #3A3F4D; background: transparent;"
        )
        layout.addWidget(self.lbl_ver)

    def set_progress(self, value: int, status: str = ""):
        """更新进度条和状态文字"""
        self.progress.setValue(value)
        if status:
            self.lbl_status.setText(status)
        # Keep the splash visually current without re-entering the whole app
        # event loop during MainWindow construction.
        self.progress.repaint()
        self.lbl_status.repaint()
        self.repaint()

    def paintEvent(self, event):
        """绘制圆角深色背景"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 深色圆角矩形背景
        painter.setPen(QPen(QColor(255, 255, 255, 8), 1))
        painter.setBrush(QColor(11, 13, 18, 245))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 16, 16)

        painter.end()
