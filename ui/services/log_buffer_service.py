from __future__ import annotations

import io
import sys
import threading
from collections import deque
from contextlib import suppress

from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot

from app.services.ui_event_service import domain_events as event_bus


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


def _safe_fallback_write(message: str) -> None:
    fallback = _resolve_original_stream(
        getattr(sys, "__stderr__", None),
        getattr(sys, "__stdout__", None),
        getattr(sys, "stderr", None),
        getattr(sys, "stdout", None),
    )
    if fallback is None:
        return
    with suppress(AttributeError, OSError, RuntimeError, TypeError, ValueError):
        fallback.write(message)
        if hasattr(fallback, "flush"):
            fallback.flush()


class _LogRedirectStream(io.TextIOBase):
    _is_ui_log_redirect = True

    def __init__(self, original):
        super().__init__()
        self.original = _resolve_original_stream(original)

    @property
    def encoding(self):
        return getattr(self.original, "encoding", None) or "utf-8"

    @property
    def errors(self):
        return getattr(self.original, "errors", None) or "replace"

    def write(self, text):
        if not text:
            return 0
        message = text if isinstance(text, str) else str(text)
        if not message.strip():
            return len(message)

        try:
            if self.original is not None:
                self.original.write(message)
                if hasattr(self.original, "flush"):
                    self.original.flush()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            _safe_fallback_write(f"[LogStream] 原始流写入失败: {exc}\n")

        try:
            event_bus.sig_system_log.emit("info", message)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            _safe_fallback_write(f"[LogStream] 事件总线发送失败: {exc}\n")
        return len(message)

    def flush(self):
        try:
            if self.original is not None and hasattr(self.original, "flush"):
                self.original.flush()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            _safe_fallback_write(f"[LogStream] flush失败: {exc}\n")

    def isatty(self):
        try:
            return bool(self.original is not None and self.original.isatty())
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return False


class LogBufferService(QObject):
    """Capture process/UI logs independently from the lazily mounted log tab."""

    sig_versioned_entry = pyqtSignal(int, int, str, str)
    sig_cleared = pyqtSignal(int, int)

    def __init__(self, parent=None, *, max_entries: int = 3000):
        super().__init__(parent)
        self._entries = deque(maxlen=max(1, int(max_entries)))
        self._installed = False
        self._persistent = False
        self._client_count = 0
        self._generation = 0
        self._sequence = 0
        self._lock = threading.RLock()
        self._previous_stdout = None
        self._previous_stderr = None
        self._stdout_redirect = None
        self._stderr_redirect = None

    @property
    def is_installed(self) -> bool:
        return self._installed

    def install(self, *, persistent: bool = False) -> bool:
        self._persistent = self._persistent or bool(persistent)
        if self._installed:
            return False

        self._previous_stdout = getattr(sys, "stdout", None)
        self._previous_stderr = getattr(sys, "stderr", None)
        self._stdout_redirect = _LogRedirectStream(
            _resolve_original_stream(
                self._previous_stdout,
                getattr(sys, "__stdout__", None),
                getattr(sys, "__stderr__", None),
            )
        )
        self._stderr_redirect = _LogRedirectStream(
            _resolve_original_stream(
                self._previous_stderr,
                getattr(sys, "__stderr__", None),
                self._stdout_redirect.original,
            )
        )
        # Capture the generation at emission time.  A queued connection here can
        # deliver pre-clear entries after ``clear()``, making deleted logs appear
        # again.  The slot only mutates a lock-protected Python deque and emits
        # versioned signals; widgets still receive those signals queued.
        event_bus.sig_system_log.connect(self._capture_entry, type=Qt.ConnectionType.DirectConnection)
        sys.stdout = self._stdout_redirect
        sys.stderr = self._stderr_redirect
        self._installed = True
        return True

    def acquire(self) -> None:
        self._client_count += 1
        self.install()

    def release(self) -> None:
        self._client_count = max(0, self._client_count - 1)
        if self._client_count == 0 and not self._persistent:
            self._shutdown(clear=True, reset_persistent=False)

    def snapshot(self) -> list[tuple[str, str]]:
        with self._lock:
            return [(level, text) for _sequence, level, text in self._entries]

    def snapshot_versioned(self) -> tuple[int, int, list[tuple[int, str, str]]]:
        with self._lock:
            return self._generation, self._sequence, list(self._entries)

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def clear(self) -> tuple[int, int]:
        with self._lock:
            self._generation += 1
            generation = self._generation
            sequence = self._sequence
            self._entries.clear()
        self.sig_cleared.emit(generation, sequence)
        return generation, sequence

    @pyqtSlot(str, str)
    def _capture_entry(self, level: str, text: str) -> None:
        level_text = str(level or "info")
        message = str(text or "")
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
            generation = self._generation
            self._entries.append((sequence, level_text, message))
        self.sig_versioned_entry.emit(generation, sequence, level_text, message)

    def shutdown(self) -> None:
        self._shutdown(clear=False, reset_persistent=True)

    def _shutdown(self, *, clear: bool, reset_persistent: bool) -> None:
        if self._installed:
            with suppress(AttributeError, RuntimeError, TypeError):
                event_bus.sig_system_log.disconnect(self._capture_entry)
            if getattr(sys, "stdout", None) is self._stdout_redirect:
                sys.stdout = self._previous_stdout
            if getattr(sys, "stderr", None) is self._stderr_redirect:
                sys.stderr = self._previous_stderr

        self._installed = False
        self._stdout_redirect = None
        self._stderr_redirect = None
        self._previous_stdout = None
        self._previous_stderr = None
        self._client_count = 0
        if clear:
            self.clear()
        if reset_persistent:
            self._persistent = False


_log_buffer_service: LogBufferService | None = None


def get_log_buffer_service(*, parent=None) -> LogBufferService:
    global _log_buffer_service
    if _log_buffer_service is None:
        _log_buffer_service = LogBufferService(parent=parent)
    elif parent is not None and _log_buffer_service.parent() is None:
        _log_buffer_service.setParent(parent)
    return _log_buffer_service


def install_log_buffer_service(*, parent=None, persistent: bool = False) -> LogBufferService:
    service = get_log_buffer_service(parent=parent)
    service.install(persistent=persistent)
    return service
