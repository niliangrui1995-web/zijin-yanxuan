import sys
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTextEdit,
    QComboBox
)
from PyQt6.QtCore import QTimer, Qt
from core.event_bus import event_bus

class LogTab(QWidget):
    """独立的系统运行日志组件 - 负责渲染日志流并接住 stdout/stderr"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
        self._setup_log_redirect()
        
        # 挂载中心事件总线
        event_bus.sig_system_log.connect(self._on_log_msg, type=Qt.ConnectionType.QueuedConnection)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        
        # 1. 优先初始化日志文本区 (被下方按钮事件依赖)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setObjectName("systemLogText")
        # 控制文档块数量，避免长时间运行后日志文本过大拖慢 UI。
        self.log_text.document().setMaximumBlockCount(4000)
        
        # 2. 工具栏
        toolbar = QWidget()
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(8, 6, 8, 6)
        
        lbl = QLabel("系统运行日志")
        lbl.setObjectName("tabTitle")
        tb_layout.addWidget(lbl)
        tb_layout.addStretch()
        
        btn_clear_log = QPushButton("清空")
        btn_clear_log.setProperty("class", "ctaSecondary")
        btn_clear_log.clicked.connect(self.log_text.clear)
        tb_layout.addWidget(btn_clear_log)

        # 日志级别过滤下拉框
        self.level_filter = QComboBox()
        self.level_filter.addItems(["全部", "仅 Error", "仅 Warning"])
        self.level_filter.setFixedWidth(120)
        tb_layout.addWidget(self.level_filter)

        layout.addWidget(toolbar)
        
        # 3. 按照顺序加入布局
        layout.addWidget(self.log_text)

    def _setup_log_redirect(self):
        """将当前进程的 stdout/stderr 重置，统一往 event_bus 发送"""
        import io

        def _resolve_original_stream(*candidates):
            for candidate in candidates:
                current = candidate
                visited = set()
                while current is not None and id(current) not in visited:
                    visited.add(id(current))
                    if getattr(current, "_is_ui_log_redirect", False):
                        current = getattr(current, "original", None)
                        continue
                    if hasattr(current, "write"):
                        return current
                    break
            return None

        def _safe_fallback_write(message):
            fallback = _resolve_original_stream(
                getattr(sys, "__stderr__", None),
                getattr(sys, "__stdout__", None),
                getattr(sys, "stderr", None),
                getattr(sys, "stdout", None),
            )
            if fallback is None:
                return
            try:
                fallback.write(message)
                if hasattr(fallback, "flush"):
                    fallback.flush()
            except Exception:
                pass

        class LogStream(io.TextIOBase):
            _is_ui_log_redirect = True

            def __init__(self, original):
                super().__init__()
                self.original = _resolve_original_stream(original)

            def write(self, text):
                if not text:
                    return 0

                if text.strip():
                    try:
                        if self.original is not None:
                            self.original.write(text)
                            if hasattr(self.original, "flush"):
                                self.original.flush()
                    except Exception as _e:
                        _safe_fallback_write(f"[LogStream] 原始流写入失败: {_e}\n")
                    # 将截获的各种 print 抛入总线，使用 try-except 防止事件发送失败引发死循环
                    try:
                        event_bus.sig_system_log.emit("info", text)
                    except Exception as _e:
                        _safe_fallback_write(f"[LogStream] 事件总线发射失败: {_e}\n")
                return len(text)

            def flush(self):
                try:
                    if self.original is not None and hasattr(self.original, "flush"):
                        self.original.flush()
                except Exception as _e:
                    _safe_fallback_write(f"[LogStream] flush失败: {_e}\n")

        stdout_original = _resolve_original_stream(
            getattr(sys, "__stdout__", None),
            getattr(sys, "stdout", None),
            getattr(sys, "__stderr__", None),
        )
        stderr_original = _resolve_original_stream(
            getattr(sys, "__stderr__", None),
            getattr(sys, "stderr", None),
            stdout_original,
        )

        sys.stdout = LogStream(stdout_original)
        sys.stderr = LogStream(stderr_original)
        
        # 批量聚合刷新定时器，防止死锁与卡顿
        self._log_buffer = []
        self._log_buffer_max = 5000
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.timeout.connect(self._flush_log_buffer)
        self._log_flush_timer.start(200)

    def _on_log_msg(self, level, text):
        # 保存 (级别, 文本) 以支持客户端侧过滤
        self._log_buffer.append((level, text))
        if len(self._log_buffer) > self._log_buffer_max:
            overflow = len(self._log_buffer) - self._log_buffer_max
            del self._log_buffer[:overflow]

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
