# -*- coding: utf-8 -*-
"""Contain uncaught Python exceptions raised from Qt callbacks."""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, TextIO

ExceptionRecordSink = Callable[[dict], None]


@dataclass
class UiExceptionHookHandle:
    """Installed hook lease that can restore the previous process hook."""

    previous_hook: object
    installed_hook: object

    def restore(self) -> None:
        if sys.excepthook is self.installed_hook:
            sys.excepthook = self.previous_hook


def _write_exception_text(text: str, log_file: TextIO | None) -> None:
    if sys.stderr is not None:
        try:
            sys.stderr.write(f"Uncaught exception:\n{text}\n")
            sys.stderr.flush()
        except (AttributeError, OSError, ValueError):
            pass
    if log_file is not None:
        try:
            log_file.write(f"[{datetime.now().isoformat(timespec='seconds')}] Uncaught exception:\n{text}\n")
            log_file.flush()
        except (AttributeError, OSError, ValueError):
            pass


def _show_exception_dialog(app, exc_type, exc_value, traceback_text: str) -> None:
    from PyQt6.QtWidgets import QMessageBox

    friendly_msg = (
        "程序运行时发生了未处理异常。\n\n"
        f"错误类型: {exc_type.__name__}\n"
        f"错误信息: {exc_value}\n\n"
        "程序未必会立刻退出，但部分功能可能不可用。"
    )
    msg_box = QMessageBox()
    msg_box.setIcon(QMessageBox.Icon.Critical)
    msg_box.setWindowTitle("系统异常")
    msg_box.setText(friendly_msg)
    msg_box.setDetailedText(traceback_text)
    msg_box.setStyleSheet(app.styleSheet())
    msg_box.exec()


def _build_ui_exception_hook(
    *,
    app=None,
    log_file: TextIO | None = None,
    show_dialog: bool = False,
    on_exception: ExceptionRecordSink | None = None,
) -> Callable:
    handling = False

    def ui_exception_hook(exc_type, exc_value, exc_traceback) -> None:
        nonlocal handling
        if isinstance(exc_type, type) and issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        if handling:
            return

        handling = True
        try:
            traceback_text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
            record = {
                "type": getattr(exc_type, "__name__", str(exc_type)),
                "message": str(exc_value),
                "traceback": traceback_text,
            }
            _write_exception_text(traceback_text, log_file)
            if on_exception is not None:
                try:
                    on_exception(record)
                except Exception:  # noqa: BLE001 - the exception boundary itself must never escape into Qt.
                    pass
            if show_dialog and app is not None:
                try:
                    _show_exception_dialog(app, exc_type, exc_value, traceback_text)
                except Exception:  # noqa: BLE001 - reporting must not replace the original callback failure.
                    pass
        finally:
            handling = False

    return ui_exception_hook


def install_ui_exception_hook(
    *,
    app=None,
    log_file: TextIO | None = None,
    show_dialog: bool = False,
    on_exception: ExceptionRecordSink | None = None,
) -> UiExceptionHookHandle:
    """Install a non-raising hook so a Qt callback error cannot trigger ``qFatal``.

    PyQt delegates an exception escaping a signal/slot callback to
    ``sys.excepthook``. The boundary records it and returns normally.
    """

    previous_hook = sys.excepthook
    ui_exception_hook = _build_ui_exception_hook(
        app=app,
        log_file=log_file,
        show_dialog=show_dialog,
        on_exception=on_exception,
    )
    sys.excepthook = ui_exception_hook
    return UiExceptionHookHandle(previous_hook=previous_hook, installed_hook=ui_exception_hook)


__all__ = ["UiExceptionHookHandle", "install_ui_exception_hook"]
