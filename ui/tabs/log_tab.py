import io
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.services.ui_event_service import domain_events as event_bus
from ui.components import MultiSelectFilterButton, format_multi_select_summary
from ui.components.task_status_panel import TaskStatusPanel
from ui.theme_tokens import build_ui_tokens


class LogTab(QWidget):
    """独立的系统运行日志组件，负责承接 stdout/stderr 与系统日志事件。"""

    _DIAGNOSTIC_LOG_MARKERS = (
        "ui.stall.",
        "ui_event_loop_stall_ms",
        "ui_method_stall_ms",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._log_history = []
        self._refresh_from_history_pending = False
        self._init_ui()
        self._setup_log_redirect()
        event_bus.sig_system_log.connect(self._on_log_msg, type=Qt.ConnectionType.QueuedConnection)

    @staticmethod
    def _prepare_toolbar_widget(widget):
        if widget is None:
            return
        widget.setProperty("inToolbar", True)
        if isinstance(widget, QLineEdit):
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if widget.minimumWidth() == widget.maximumWidth() and widget.maximumWidth() > 0:
                preferred_width = widget.maximumWidth()
                widget.setMinimumWidth(max(150, preferred_width - 20))
                widget.setMaximumWidth(max(260, preferred_width + 80))
            if widget.minimumWidth() < 150:
                widget.setMinimumWidth(150)
        elif isinstance(widget, QPushButton):
            widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

    def _init_ui(self):
        tokens = build_ui_tokens()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setObjectName("systemLogText")
        self.log_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.log_text.document().setMaximumBlockCount(2500)

        toolbar = QWidget()
        toolbar.setObjectName("tabToolbar")
        toolbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(
            tokens["shell"]["toolbar_padding_x"],
            tokens["shell"]["toolbar_padding_y"],
            tokens["shell"]["toolbar_padding_x"],
            tokens["shell"]["toolbar_padding_y"],
        )
        tb_layout.setSpacing(tokens["shell"]["toolbar_section_gap"])

        title_wrap = QFrame()
        title_wrap.setObjectName("tabToolbarTitleWrap")
        title_wrap.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        title_layout = QHBoxLayout(title_wrap)
        title_layout.setContentsMargins(
            max(8, tokens["shell"]["toolbar_padding_x"] - 4),
            0,
            max(8, tokens["shell"]["toolbar_padding_x"] - 4),
            0,
        )
        title_layout.setSpacing(tokens["shell"]["toolbar_group_gap"] + 1)

        lbl = QLabel("系统运行日志")
        lbl.setObjectName("tabTitle")
        title_layout.addWidget(lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self.lbl_status = QLabel("日志 0条")
        self.lbl_status.setObjectName("tabStatusLabel")
        self.lbl_status.setProperty("toolbarRole", "status")
        title_layout.addWidget(self.lbl_status, 0, Qt.AlignmentFlag.AlignVCenter)

        tb_layout.addWidget(title_wrap)
        tb_layout.addStretch(1)

        action_wrap = QWidget()
        action_wrap.setObjectName("tabToolbarActions")
        action_wrap.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        action_layout = QHBoxLayout(action_wrap)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(max(6, tokens["shell"]["toolbar_group_gap"] + 2))
        action_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.btn_clear_log = QPushButton("清空")
        self.btn_clear_log.setProperty("class", "ctaSecondary")
        self.btn_clear_log.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prepare_toolbar_widget(self.btn_clear_log)
        self.btn_clear_log.setMinimumWidth(58)
        self.btn_clear_log.clicked.connect(self._clear_logs)
        action_layout.addWidget(self.btn_clear_log)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("搜索日志...")
        self._prepare_toolbar_widget(self.search_box)
        self.search_box.setMinimumWidth(150)
        self.search_box.setMaximumWidth(260)
        self.search_box.textChanged.connect(self._apply_log_filter)
        action_layout.addWidget(self.search_box)

        self.level_filter = MultiSelectFilterButton("全部")
        self._prepare_toolbar_widget(self.level_filter)
        self.level_filter.setMaximumWidth(120)
        self.level_filter.set_options(
            [("error", "Error"), ("warning", "Warning")],
            preserve_selection=False,
        )
        self.level_filter.selectionChanged.connect(self._apply_log_filter)
        self._refresh_level_filter_button_text()
        self.level_filter.setMinimumWidth(92)
        action_layout.addWidget(self.level_filter)

        tb_layout.addWidget(action_wrap, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.task_status_panel = TaskStatusPanel(self)
        layout.addWidget(toolbar)
        layout.addWidget(self.task_status_panel)
        layout.addWidget(self.log_text)

    def showEvent(self, event):
        super().showEvent(event)
        if self._refresh_from_history_pending:
            self._refresh_from_history_pending = False
            self._apply_log_filter()
        else:
            self._refresh_status_summary()

    def _clear_logs(self):
        self._log_buffer.clear()
        self._log_history.clear()
        self.log_text.clear()
        self._refresh_status_summary(0)

    def _refresh_status_summary(self, visible_count: int | None = None):
        total = len(getattr(self, "_log_history", []) or [])
        if visible_count is None:
            visible_count = self._count_visible_logs()

        filter_text = self._level_filter_status_text() if hasattr(self, "level_filter") else "全部"
        search_text = self.search_box.text().strip() if hasattr(self, "search_box") else ""
        primary = f"日志 {visible_count}条" if visible_count == total else f"可见 {visible_count}/{total}条"
        segments = [f"级别 {filter_text}"]
        hidden_diagnostics = self._hidden_diagnostic_count()
        if hidden_diagnostics:
            segments.append(f"隐藏诊断 {hidden_diagnostics}条")
        if search_text:
            segments.append(f"搜索 {search_text[:18]}")
        self.lbl_status.setText(" | ".join([primary, *segments]))

    def _count_visible_logs(self) -> int:
        return len(self._filtered_entries())

    @staticmethod
    def _normalize_level(level) -> str:
        normalized = str(level or "info").strip().lower()
        if normalized == "warn":
            return "warning"
        return normalized

    @classmethod
    def _is_diagnostic_log(cls, text) -> bool:
        payload = str(text or "").lower()
        return any(marker in payload for marker in cls._DIAGNOSTIC_LOG_MARKERS)

    def _hidden_diagnostic_count(self) -> int:
        search_text = self.search_box.text().strip().lower() if hasattr(self, "search_box") else ""
        if search_text:
            return 0

        selected_levels = self.level_filter.selected_values() if hasattr(self, "level_filter") else set()
        count = 0
        for level, text in getattr(self, "_log_history", []) or []:
            normalized = self._normalize_level(level)
            if normalized == "error" or not self._is_diagnostic_log(text):
                continue
            if selected_levels and normalized not in selected_levels:
                continue
            count += 1
        return count

    def _refresh_level_filter_button_text(self):
        text, tooltip = format_multi_select_summary(
            "级别",
            self.level_filter.selected_labels(),
            all_text="全部",
        )
        self.level_filter.setText(text)
        self.level_filter.setToolTip(tooltip)

    def _level_filter_status_text(self) -> str:
        labels = self.level_filter.selected_labels()
        if not labels:
            return "全部"
        if len(labels) <= 2:
            return " / ".join(labels)
        return f"{len(labels)}项"

    def _entry_visible(self, level, text, selected_levels: set[str], search_text: str) -> bool:
        normalized = self._normalize_level(level)
        if selected_levels and normalized not in selected_levels:
            return False
        payload = str(text).lower()
        if search_text and search_text not in payload:
            return False
        if not search_text and normalized != "error" and self._is_diagnostic_log(payload):
            return False
        return True

    def _filtered_entries(self, entries=None):
        selected_levels = self.level_filter.selected_values() if hasattr(self, "level_filter") else set()
        search_text = self.search_box.text().strip().lower() if hasattr(self, "search_box") else ""
        source_entries = entries if entries is not None else self._log_history
        return [
            (level, text)
            for level, text in source_entries
            if self._entry_visible(level, text, selected_levels, search_text)
        ]

    def _log_level_color(self, level) -> QColor:
        from ui.theme import theme_manager

        theme = theme_manager.current_theme
        normalized = self._normalize_level(level)
        if normalized == "error":
            return QColor(theme["COLOR_ERROR"])
        if normalized in ("warn", "warning"):
            return QColor(theme["COLOR_WARNING"])
        if normalized == "debug":
            return QColor(theme["TEXT_MUTED"])
        if normalized == "success":
            return QColor(theme["COLOR_SUCCESS"])
        return QColor(theme["TEXT_PRIMARY"])

    def _append_log_entries(self, entries, *, clear_existing: bool):
        if clear_existing:
            self.log_text.clear()

        if not entries:
            return

        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        formats = {}
        pending_level = None
        pending_parts = []

        def _flush_pending():
            if not pending_parts:
                return
            text_format = formats.get(pending_level)
            if text_format is None:
                text_format = QTextCharFormat()
                text_format.setForeground(self._log_level_color(pending_level))
                formats[pending_level] = text_format
            cursor.insertText("".join(pending_parts), text_format)
            pending_parts.clear()

        for level, text in entries:
            payload = str(text or "")
            if not payload:
                continue
            if not payload.endswith("\n"):
                payload += "\n"
            normalized_level = self._normalize_level(level)
            if pending_level is not None and normalized_level != pending_level:
                _flush_pending()
            pending_level = normalized_level
            pending_parts.append(payload)

        _flush_pending()

        self.log_text.setTextCursor(cursor)
        self.log_text.ensureCursorVisible()

    def _setup_log_redirect(self):
        """将当前进程的 stdout/stderr 重定向，统一往 event_bus 发送。"""

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
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
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
                    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                        _safe_fallback_write(f"[LogStream] 原始流写入失败: {exc}\n")

                    try:
                        event_bus.sig_system_log.emit("info", text)
                    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                        _safe_fallback_write(f"[LogStream] 事件总线发送失败: {exc}\n")
                return len(text)

            def flush(self):
                try:
                    if self.original is not None and hasattr(self.original, "flush"):
                        self.original.flush()
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    _safe_fallback_write(f"[LogStream] flush失败: {exc}\n")

        stdout_original = _resolve_original_stream(
            getattr(sys, "stdout", None),
            getattr(sys, "__stdout__", None),
            getattr(sys, "__stderr__", None),
        )
        stderr_original = _resolve_original_stream(
            getattr(sys, "stderr", None),
            getattr(sys, "__stderr__", None),
            stdout_original,
        )

        sys.stdout = LogStream(stdout_original)
        sys.stderr = LogStream(stderr_original)

        self._log_buffer = []
        self._log_buffer_max = 3000
        self._log_flush_batch_max = 160
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.timeout.connect(self._flush_log_buffer)
        self._log_flush_timer.start(200)

    @staticmethod
    def _restore_log_redirect() -> None:
        for stream_name in ("stdout", "stderr"):
            current = getattr(sys, stream_name, None)
            if not getattr(current, "_is_ui_log_redirect", False):
                continue
            original = getattr(current, "original", None)
            if original is not None:
                setattr(sys, stream_name, original)

    def shutdown(self) -> None:
        log_flush_timer = getattr(self, "_log_flush_timer", None)
        if log_flush_timer is not None:
            log_flush_timer.stop()
        task_status_panel = getattr(self, "task_status_panel", None)
        if task_status_panel is not None:
            task_status_panel.shutdown()
        try:
            event_bus.sig_system_log.disconnect(self._on_log_msg)
        except (AttributeError, RuntimeError, TypeError):
            pass
        self._restore_log_redirect()

    def _on_log_msg(self, level, text):
        self._log_buffer.append((level, text))
        self._log_history.append((level, text))
        if len(self._log_history) > self._log_buffer_max:
            overflow = len(self._log_history) - self._log_buffer_max
            del self._log_history[:overflow]
        if len(self._log_buffer) > self._log_buffer_max:
            overflow = len(self._log_buffer) - self._log_buffer_max
            del self._log_buffer[:overflow]

    def _apply_log_filter(self):
        self._refresh_level_filter_button_text()
        self._log_buffer.clear()
        filtered_entries = self._filtered_entries()
        self._append_log_entries(filtered_entries, clear_existing=True)
        self._refresh_status_summary(len(filtered_entries))

    def _flush_log_buffer(self):
        if not self._log_buffer:
            return

        if not self.isVisible():
            self._log_buffer.clear()
            self._refresh_from_history_pending = True
            return

        batch_size = max(1, int(getattr(self, "_log_flush_batch_max", 160) or 160))
        pending_entries = self._log_buffer[:batch_size]
        del self._log_buffer[: len(pending_entries)]

        filtered_entries = self._filtered_entries(pending_entries)

        if filtered_entries:
            self._append_log_entries(filtered_entries, clear_existing=False)
        self._refresh_status_summary()
