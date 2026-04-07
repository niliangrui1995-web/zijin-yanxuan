import sys
import datetime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTextEdit,
    QFileDialog
)
from PyQt6.QtCore import QTimer
from core.event_bus import event_bus

class LogTab(QWidget):
    """独立的系统运行日志组件 - 负责渲染日志流并接住 stdout/stderr"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: transparent;")
        self._init_ui()
        self._setup_log_redirect()
        
        # 挂载中心事件总线
        from PyQt6.QtCore import Qt
        event_bus.sig_system_log.connect(self._on_log_msg, type=Qt.ConnectionType.QueuedConnection)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # 1. 优先初始化日志文本区 (被下方按钮事件依赖)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("""
            QTextEdit { 
                background-color: #0A0C10; color: #6B7280; 
                font-family: 'Consolas', 'Courier New', monospace; 
                font-size: 12px; border: none; padding: 12px;
                border-top: 1px solid #1A1E28;
            }
        """)
        
        # 2. 工具栏
        toolbar = QWidget()
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(6, 4, 6, 4)
        
        lbl = QLabel("系统运行日志")
        lbl.setStyleSheet("color: #6B7280; font-weight: 600; font-size: 12px;")
        tb_layout.addWidget(lbl)
        tb_layout.addStretch()
        
        btn_export_log = QPushButton("📄 导出日志")
        btn_export_log.setProperty("class", "ctaSecondary")
        btn_export_log.setFixedHeight(32)
        btn_export_log.clicked.connect(self._export_log)
        tb_layout.addWidget(btn_export_log)
        
        btn_clear_log = QPushButton("🗑 清空")
        btn_clear_log.setProperty("class", "ctaSecondary")
        btn_clear_log.setFixedHeight(32)
        btn_clear_log.clicked.connect(self.log_text.clear)
        tb_layout.addWidget(btn_clear_log)

        # 日志级别过滤下拉框
        from PyQt6.QtWidgets import QComboBox
        self.level_filter = QComboBox()
        self.level_filter.addItems(["全部", "仅 Error", "仅 Warning"])
        self.level_filter.setFixedHeight(32)
        self.level_filter.setFixedWidth(120)
        self.level_filter.setStyleSheet("""
            QComboBox {
                background-color: #1A1E28; color: #9CA3AF; border: 1px solid #3A3A3C;
                border-radius: 4px; padding: 4px 8px; font-size: 12px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background-color: #1A1E28; color: #9CA3AF; }
        """)
        tb_layout.addWidget(self.level_filter)

        layout.addWidget(toolbar)
        
        # 3. 按照顺序加入布局
        layout.addWidget(self.log_text)

    def _setup_log_redirect(self):
        """将当前进程的 stdout/stderr 重置，统一往 event_bus 发送"""
        import io
        class LogStream(io.TextIOBase):
            def __init__(self, original):
                super().__init__()
                self.original = original

            def write(self, text):
                if text and text.strip():
                    try:
                        self.original.write(text)
                        self.original.flush()
                    except Exception as _e:
                        sys.__stderr__.write(f"[LogStream] 原始流写入失败: {_e}\n")
                    # 将截获的各种 print 抛入总线，使用 try-except 防止事件发送失败引发死循环
                    try:
                        event_bus.sig_system_log.emit("info", text)
                    except Exception as _e:
                        sys.__stderr__.write(f"[LogStream] 事件总线发射失败: {_e}\n")
                return len(text) if text else 0

            def flush(self):
                try:
                    self.original.flush()
                except Exception as _e:
                    sys.__stderr__.write(f"[LogStream] flush失败: {_e}\n")
        
        sys.stdout = LogStream(sys.__stdout__)
        sys.stderr = LogStream(sys.__stderr__)
        
        # 批量聚合刷新定时器，防止死锁与卡顿
        self._log_buffer = []
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.timeout.connect(self._flush_log_buffer)
        self._log_flush_timer.start(200)

    def _on_log_msg(self, level, text):
        # 保存 (级别, 文本) 以支持客户端侧过滤
        self._log_buffer.append((level, text))

    def _flush_log_buffer(self):
        if not self._log_buffer:
            return

        # 读取当前过滤级别
        filter_idx = self.level_filter.currentIndex() if hasattr(self, 'level_filter') else 0

        filtered_texts = []
        for level, text in self._log_buffer:
            if filter_idx == 1 and level != 'error':
                continue
            if filter_idx == 2 and level not in ('warn', 'warning'):
                continue
            filtered_texts.append(text)

        self._log_buffer.clear()

        if filtered_texts:
            batch = ''.join(filtered_texts)
            self.log_text.append(batch.rstrip())
            # 自动滚动到底端
            sb = self.log_text.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _export_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", f"系统日志_{datetime.date.today().strftime('%Y%m%d')}.txt", "Text Files (*.txt)")
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self.log_text.toPlainText())
            # 组件解耦，不能直接调主窗口状态栏，用总线发射
            event_bus.sig_system_log.emit("info", f"✅ 日志已导出到 {path}")
