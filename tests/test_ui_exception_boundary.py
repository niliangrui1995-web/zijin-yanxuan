from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

from infra.diagnostics.ui_exception_boundary import install_ui_exception_hook

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_ui_exception_hook_records_and_restores_previous_hook():
    records = []
    log_file = io.StringIO()
    previous = sys.excepthook
    handle = install_ui_exception_hook(log_file=log_file, on_exception=records.append)
    try:
        try:
            raise TypeError("slot failure")
        except TypeError:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            sys.excepthook(exc_type, exc_value, exc_traceback)
    finally:
        handle.restore()

    assert sys.excepthook is previous
    assert records[0]["type"] == "TypeError"
    assert records[0]["message"] == "slot failure"
    assert "TypeError: slot failure" in records[0]["traceback"]
    assert "TypeError: slot failure" in log_file.getvalue()


def test_ui_exception_hook_prevents_pyqt_callback_error_from_fast_fail(tmp_path):
    log_path = tmp_path / "qt_callback_exception.log"
    source = f"""
from PyQt6.QtCore import QCoreApplication, QTimer
from infra.diagnostics.ui_exception_boundary import install_ui_exception_hook

app = QCoreApplication([])
with open({str(log_path)!r}, "a", encoding="utf-8") as log_file:
    install_ui_exception_hook(log_file=log_file)
    QTimer.singleShot(0, lambda _: None)
    QTimer.singleShot(25, app.quit)
    raise SystemExit(app.exec())
"""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"

    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    log_text = log_path.read_text(encoding="utf-8")
    assert "TypeError" in log_text
    assert "missing 1 required positional argument: '_'" in log_text
