# -*- coding: utf-8 -*-
"""Unified logger factory with per-module logger cache."""

import logging
import os
import sys
import threading
from datetime import datetime
from typing import Optional

# 防止日志处理器自身异常把 UI/终端刷爆（例如控制台编码不支持 emoji）。
logging.raiseExceptions = False


_logger_cache: dict[str, logging.Logger] = {}
_shared_handlers: Optional[list[logging.Handler]] = None
_logger_lock = threading.Lock()


def _unwrap_stream(stream):
    visited = set()
    current = stream
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if getattr(current, "_is_ui_log_redirect", False):
            current = getattr(current, "original", None)
            continue
        if hasattr(current, "write"):
            return current
        break
    return None


def _resolve_console_stream():
    for candidate in (
        getattr(sys, "__stdout__", None),
        getattr(sys, "__stderr__", None),
        getattr(sys, "stdout", None),
        getattr(sys, "stderr", None),
    ):
        stream = _unwrap_stream(candidate)
        if stream is not None:
            return stream
    return None


def _safe_stderr_write(message: str):
    stream = (
        _unwrap_stream(getattr(sys, "__stderr__", None))
        or _unwrap_stream(getattr(sys, "stderr", None))
        or _resolve_console_stream()
    )
    if stream is None:
        return
    try:
        stream.write(message)
        if hasattr(stream, "flush"):
            stream.flush()
    except (OSError, ValueError):
        pass


class _EncodingSafeStream:
    """Wrap console stream and replace unencodable chars instead of raising."""

    def __init__(self, stream):
        self._stream = stream
        self.encoding = getattr(stream, "encoding", None)
        self.errors = "replace"

    def write(self, message):
        if message is None:
            return 0
        text = message if isinstance(message, str) else str(message)
        try:
            self._stream.write(text)
        except UnicodeEncodeError:
            enc = self.encoding or "utf-8"
            safe_text = text.encode(enc, errors="replace").decode(enc, errors="replace")
            self._stream.write(safe_text)
        return len(text)

    def flush(self):
        try:
            if hasattr(self._stream, "flush"):
                self._stream.flush()
        except (OSError, ValueError):
            pass

    def isatty(self):
        try:
            if hasattr(self._stream, "isatty"):
                return bool(self._stream.isatty())
        except (OSError, ValueError):
            return False
        return False


class EventBusHandler(logging.Handler):
    """Forward logs to UI event bus."""

    def emit(self, record):
        try:
            msg = self.format(record)
            level = record.levelname.lower()
            if level == "warning":
                level = "warn"
            from domains.runtime import domain_events as event_bus

            event_bus.sig_system_log.emit(level, msg + "\n")
        except (ImportError, RuntimeError, AttributeError):
            self.handleError(record)


def _clean_old_logs(log_dir: str, max_age_days: int = 7):
    import time

    now = time.time()
    cutoff = now - (max_age_days * 86400)
    try:
        for filename in os.listdir(log_dir):
            is_plain_log = filename.endswith(".log")
            is_rotated_log = ".log." in filename and filename.rsplit(".log.", 1)[-1].isdigit()
            if not (is_plain_log or is_rotated_log):
                continue
            filepath = os.path.join(log_dir, filename)
            if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
                os.remove(filepath)
    except OSError as e:
        _safe_stderr_write(f"[logger] clean old logs failed: {e}\n")


def _resolve_log_dir() -> str:
    configured = os.environ.get("VCP_HUNTER_LOG_DIR", "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "logs")


def _build_shared_handlers() -> list[logging.Handler]:
    global _shared_handlers
    if _shared_handlers is not None:
        return _shared_handlers

    handlers: list[logging.Handler] = []
    console_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
    )

    console_stream = _resolve_console_stream()
    if console_stream is not None:
        console_handler = logging.StreamHandler(_EncodingSafeStream(console_stream))
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(console_fmt)
        handlers.append(console_handler)

    eb_handler = EventBusHandler()
    eb_handler.setLevel(logging.INFO)
    eb_handler.setFormatter(console_fmt)
    handlers.append(eb_handler)

    log_dir = _resolve_log_dir()
    try:
        os.makedirs(log_dir, exist_ok=True)
        _clean_old_logs(log_dir, max_age_days=7)

        log_file = os.path.join(log_dir, f"vcp_{datetime.now().strftime('%Y%m%d')}.log")
        from logging.handlers import RotatingFileHandler

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=1 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        handlers.append(file_handler)
    except OSError as e:
        _safe_stderr_write(f"[logger] create log file failed: {e}\n")

    for handler in handlers:
        setattr(handler, "_vcp_shared_handler", True)

    _shared_handlers = handlers
    return _shared_handlers


def get_logger(name: str = "vcp_hunter") -> logging.Logger:
    """Get logger by module name and reuse shared handlers."""
    logger_name = (name or "vcp_hunter").strip() or "vcp_hunter"

    with _logger_lock:
        cached = _logger_cache.get(logger_name)
        if cached is not None:
            return cached

        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        shared_handlers = _build_shared_handlers()
        for handler in shared_handlers:
            if handler not in logger.handlers:
                logger.addHandler(handler)

        _logger_cache[logger_name] = logger
        return logger
