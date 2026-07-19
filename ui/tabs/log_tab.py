from contextlib import suppress
from functools import partial

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
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

from ui.components import MultiSelectFilterButton, format_multi_select_summary
from ui.components.task_status_panel import TaskStatusPanel
from ui.services.log_buffer_service import get_log_buffer_service
from ui.theme_tokens import build_ui_tokens
from ui.workspaces.background_preload_receipt import BackgroundPreloadCancellationReceipt


def _reset_log_tab(tab) -> None:
    tab._log_buffer.clear()
    tab._log_history.clear()
    tab._visible_log_count = 0
    tab._hidden_diagnostic_count_cache = 0
    tab._hidden_diagnostic_cache_len = 0
    tab._log_status_refresh_pending = False
    tab._history_rebuild_entries.clear()
    tab._history_refresh_scheduled = False
    tab._history_refresh_token += 1
    tab.log_text.clear()
    tab._refresh_status_summary(0)


def _apply_shared_log_clear(tab, generation: int, sequence: int) -> None:
    if getattr(tab, "_closing", False):
        return
    generation = int(generation)
    sequence = int(sequence)
    if generation < int(tab._log_generation):
        return
    if generation == int(tab._log_generation) and int(tab._last_log_sequence) > sequence:
        return
    tab._log_generation = generation
    tab._last_log_sequence = sequence
    _reset_log_tab(tab)


def _take_history_refresh_batch(entries: list, *, entry_limit: int, char_limit: int) -> list:
    batch = []
    batch_chars = 0
    for entry in entries[:entry_limit]:
        text = str(entry[1] or "")
        entry_chars = len(text) if text.endswith("\n") else len(text) + 1
        if batch and batch_chars + entry_chars > char_limit:
            break
        batch.append(entry)
        batch_chars += entry_chars
        if batch_chars >= char_limit:
            break
    return batch


class _LogVisibilityTimerFilter(QObject):
    def __init__(self, timer: QTimer, parent: QObject):
        super().__init__(parent)
        self._timer = timer

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Hide:
            self._timer.stop()
        return super().eventFilter(watched, event)


class _LogBackgroundPreloadMixin:
    def prime_background_load(self) -> bool:
        self._background_preload_requested = True
        self._background_preload_done = True
        return True

    def is_background_preload_complete(self) -> bool:
        return bool(self._background_preload_requested and self._background_preload_done)

    def cancel_background_preload(self, *, reason: str):
        del reason
        return BackgroundPreloadCancellationReceipt.immediate()

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)

    def deleteLater(self):
        self.shutdown()
        super().deleteLater()


class LogTab(_LogBackgroundPreloadMixin, QWidget):
    """独立的系统运行日志组件，负责承接 stdout/stderr 与系统日志事件。"""

    _DIAGNOSTIC_LOG_MARKERS = (
        "ui.stall.",
        "ui_event_loop_stall_ms",
        "ui_method_stall_ms",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._closing = False
        self._log_history = []
        self._visible_log_count = 0
        self._hidden_diagnostic_count_cache = 0
        self._hidden_diagnostic_cache_len = 0
        self._refresh_from_history_pending = False
        self._history_refresh_scheduled = False
        self._history_rebuild_entries = []
        self._history_refresh_token = 0
        self._history_refresh_batch_max = 96
        self._history_refresh_char_max = 24_000
        self._history_refresh_delay_ms = 250
        self._history_refresh_interval_ms = 25
        self._background_preload_requested = False
        self._background_preload_done = False
        self._init_ui()
        self._setup_log_capture()

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
        log_flush_timer = getattr(self, "_log_flush_timer", None)
        if log_flush_timer is not None and not log_flush_timer.isActive():
            log_flush_timer.start()
        if self._refresh_from_history_pending:
            self._schedule_history_refresh()
        else:
            self._flush_log_buffer()
            self._refresh_status_summary()

    def _clear_logs(self):
        log_service = getattr(self, "_log_service", None)
        if log_service is not None:
            generation, sequence = log_service.clear()
            self._log_generation = generation
            self._last_log_sequence = sequence
        _reset_log_tab(self)

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
        return int(getattr(self, "_visible_log_count", 0) or 0)

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
        history = getattr(self, "_log_history", []) or []
        if not selected_levels and self._hidden_diagnostic_cache_len == len(history):
            return int(getattr(self, "_hidden_diagnostic_count_cache", 0) or 0)

        count = self._compute_hidden_diagnostic_count(history, selected_levels)
        if not selected_levels:
            self._hidden_diagnostic_count_cache = count
            self._hidden_diagnostic_cache_len = len(history)
        return count

    @classmethod
    def _is_hidden_diagnostic_entry(cls, level, text) -> bool:
        normalized = cls._normalize_level(level)
        return normalized != "error" and cls._is_diagnostic_log(text)

    @classmethod
    def _compute_hidden_diagnostic_count(cls, entries, selected_levels: set[str]) -> int:
        count = 0
        for level, text in entries or []:
            normalized = cls._normalize_level(level)
            if normalized == "error" or not cls._is_diagnostic_log(text):
                continue
            if selected_levels and normalized not in selected_levels:
                continue
            count += 1
        return count

    def _refresh_level_filter_button_text(self):
        self.level_filter.apply_summary("级别", all_text="全部")

    def _level_filter_status_text(self) -> str:
        return format_multi_select_summary("", self.level_filter.selected_labels(), all_text="全部")[0]

    def _entry_visible(self, level, text, selected_levels: set[str], search_text: str) -> bool:
        normalized = self._normalize_level(level)
        if selected_levels and normalized not in selected_levels:
            return False
        payload = str(text).lower()
        if search_text and search_text not in payload:
            return False
        return not (not search_text and normalized != "error" and self._is_diagnostic_log(payload))

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

    def _append_log_entries(self, entries, *, clear_existing: bool, auto_scroll: bool = True):
        if clear_existing:
            self.log_text.clear()
            self._visible_log_count = 0

        if not entries:
            return

        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        formats = {}
        pending_level = None
        pending_parts = []
        appended_count = 0

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
            appended_count += 1
            normalized_level = self._normalize_level(level)
            if pending_level is not None and normalized_level != pending_level:
                _flush_pending()
            pending_level = normalized_level
            pending_parts.append(payload)

        _flush_pending()

        self.log_text.setTextCursor(cursor)
        max_blocks = self.log_text.document().maximumBlockCount()
        next_count = int(getattr(self, "_visible_log_count", 0) or 0) + appended_count
        self._visible_log_count = min(next_count, max_blocks) if max_blocks > 0 else next_count
        if auto_scroll:
            self.log_text.ensureCursorVisible()

    def _setup_log_capture(self):
        self._log_buffer = []
        self._log_buffer_max = 3000
        self._log_flush_batch_max = 48
        self._log_status_refresh_pending = False
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setInterval(200)
        self._log_flush_timer.timeout.connect(self._flush_log_buffer)
        self._log_visibility_filter = _LogVisibilityTimerFilter(self._log_flush_timer, self)
        self.installEventFilter(self._log_visibility_filter)
        self._log_service = get_log_buffer_service()
        self._log_service.acquire()
        self._log_generation = 0
        self._last_log_sequence = 0
        self._log_service.sig_versioned_entry.connect(
            self._on_versioned_log_msg,
            type=Qt.ConnectionType.QueuedConnection,
        )
        self._shared_clear_slot = partial(_apply_shared_log_clear, self)
        self._log_service.sig_cleared.connect(
            self._shared_clear_slot,
            type=Qt.ConnectionType.QueuedConnection,
        )
        generation, sequence, history = self._log_service.snapshot_versioned()
        self._log_generation = generation
        self._last_log_sequence = sequence
        for _entry_sequence, level, text in history:
            self._on_log_msg(level, text)

    def shutdown(self) -> None:
        if getattr(self, "_closing", False):
            return
        self._closing = True
        self._background_preload_done = True
        self._history_refresh_scheduled = False
        self._history_rebuild_entries.clear()
        self._history_refresh_token += 1
        log_flush_timer = getattr(self, "_log_flush_timer", None)
        if log_flush_timer is not None:
            log_flush_timer.stop()
        task_status_panel = getattr(self, "task_status_panel", None)
        if task_status_panel is not None:
            task_status_panel.shutdown()
        log_service = getattr(self, "_log_service", None)
        if log_service is not None:
            with suppress(AttributeError, RuntimeError, TypeError):
                log_service.sig_versioned_entry.disconnect(self._on_versioned_log_msg)
            with suppress(AttributeError, RuntimeError, TypeError):
                log_service.sig_cleared.disconnect(self._shared_clear_slot)
            log_service.release()
            self._log_service = None

    def _on_versioned_log_msg(self, generation: int, sequence: int, level: str, text: str) -> None:
        service = getattr(self, "_log_service", None)
        if service is None or int(generation) != int(service.generation):
            return
        if int(generation) < int(self._log_generation):
            return
        if int(generation) > int(self._log_generation):
            self._log_generation = int(generation)
            self._last_log_sequence = 0
            _reset_log_tab(self)
        if int(sequence) <= int(self._last_log_sequence):
            return
        self._last_log_sequence = int(sequence)
        self._on_log_msg(level, text)

    def _on_log_msg(self, level, text):
        selected_levels = self.level_filter.selected_values() if hasattr(self, "level_filter") else set()
        search_text = self.search_box.text().strip().lower() if hasattr(self, "search_box") else ""
        if self._entry_visible(level, text, selected_levels, search_text):
            self._log_buffer.append((level, text))
        else:
            self._log_status_refresh_pending = True
        cache_in_sync = self._hidden_diagnostic_cache_len == len(self._log_history)
        self._log_history.append((level, text))
        if cache_in_sync:
            if self._is_hidden_diagnostic_entry(level, text):
                self._hidden_diagnostic_count_cache += 1
            self._hidden_diagnostic_cache_len += 1
        else:
            self._hidden_diagnostic_cache_len = -1
        if len(self._log_history) > self._log_buffer_max:
            overflow = len(self._log_history) - self._log_buffer_max
            removed_entries = self._log_history[:overflow]
            del self._log_history[:overflow]
            if cache_in_sync:
                removed_hidden = self._compute_hidden_diagnostic_count(removed_entries, set())
                self._hidden_diagnostic_count_cache = max(0, self._hidden_diagnostic_count_cache - removed_hidden)
                self._hidden_diagnostic_cache_len = len(self._log_history)
            else:
                self._hidden_diagnostic_cache_len = -1
        if len(self._log_buffer) > self._log_buffer_max:
            overflow = len(self._log_buffer) - self._log_buffer_max
            del self._log_buffer[:overflow]

    def _apply_log_filter(self):
        self._history_rebuild_entries.clear()
        self._history_refresh_scheduled = False
        self._history_refresh_token += 1
        self._refresh_level_filter_button_text()
        self._log_buffer.clear()
        self._log_status_refresh_pending = False
        filtered_entries = self._filtered_entries()
        self._append_log_entries(filtered_entries, clear_existing=True)
        self._refresh_status_summary(self._visible_log_count)

    def _schedule_history_refresh(self, delay_ms: int | None = None):
        if self._history_refresh_scheduled:
            return
        self._history_refresh_scheduled = True
        self._history_refresh_token += 1
        token = self._history_refresh_token
        delay = self._history_refresh_delay_ms if delay_ms is None else delay_ms
        QTimer.singleShot(max(0, int(delay)), lambda token=token: self._start_history_refresh(token))

    def _start_history_refresh(self, token: int | None = None):
        if getattr(self, "_closing", False):
            return
        if token is not None and token != self._history_refresh_token:
            return
        self._history_refresh_scheduled = False
        if not self.isVisible():
            self._refresh_from_history_pending = True
            return

        self._refresh_from_history_pending = False
        self._refresh_level_filter_button_text()
        self._log_buffer.clear()
        self._log_status_refresh_pending = False
        self.log_text.clear()
        self._visible_log_count = 0
        self._history_rebuild_entries = list(self._filtered_entries())
        if not self._history_rebuild_entries:
            self._refresh_status_summary(0)
            return
        self._drain_history_refresh()

    def _drain_history_refresh(self):
        if getattr(self, "_closing", False):
            return
        if not self.isVisible():
            self._history_rebuild_entries.clear()
            self._refresh_from_history_pending = True
            return

        batch_size = max(1, int(getattr(self, "_history_refresh_batch_max", 96) or 96))
        char_budget = max(1, int(getattr(self, "_history_refresh_char_max", 24_000) or 24_000))
        pending_entries = _take_history_refresh_batch(
            self._history_rebuild_entries,
            entry_limit=batch_size,
            char_limit=char_budget,
        )
        del self._history_rebuild_entries[: len(pending_entries)]
        self._append_log_entries(
            pending_entries,
            clear_existing=False,
            auto_scroll=not self._history_rebuild_entries and not self._log_buffer,
        )
        if self._history_rebuild_entries:
            QTimer.singleShot(max(0, int(self._history_refresh_interval_ms)), self._drain_history_refresh)
            return

        self._refresh_status_summary(self._visible_log_count)
        if self._log_buffer:
            QTimer.singleShot(0, self._flush_log_buffer)

    def _flush_log_buffer(self):
        if getattr(self, "_closing", False):
            return
        if self._history_refresh_scheduled or self._history_rebuild_entries:
            return

        if not self._log_buffer:
            if self._log_status_refresh_pending:
                if not self.isVisible():
                    self._refresh_from_history_pending = True
                    return
                self._log_status_refresh_pending = False
                self._refresh_status_summary()
            return

        if not self.isVisible():
            self._log_buffer.clear()
            self._log_status_refresh_pending = False
            self._refresh_from_history_pending = True
            return

        batch_size = max(1, int(getattr(self, "_log_flush_batch_max", 160) or 160))
        pending_entries = self._log_buffer[:batch_size]
        del self._log_buffer[: len(pending_entries)]

        filtered_entries = self._filtered_entries(pending_entries)

        if filtered_entries:
            self._append_log_entries(
                filtered_entries,
                clear_existing=False,
                auto_scroll=not self._log_buffer,
            )
        self._log_status_refresh_pending = False
        self._refresh_status_summary(self._visible_log_count)
