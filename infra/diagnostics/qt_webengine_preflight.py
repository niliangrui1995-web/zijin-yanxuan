from __future__ import annotations

import os
import subprocess  # nosec B404
import sys
import time

from core.runtime_env import configure_qt_webengine_runtime
from infra.tasks.process_runner import windows_no_window_kwargs


def _webengine_smoke_code() -> str:
    return r"""
import faulthandler
import sys
import time
faulthandler.enable()
from PyQt6.QtCore import QEventLoop, Qt, QTimer, QUrl
from PyQt6.QtWidgets import QApplication
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
app = QApplication(sys.argv)
from PyQt6.QtWebEngineCore import QWebEnginePage
page = QWebEnginePage()
loop = QEventLoop()
load_state = {"done": False, "ok": False}
def _finish(ok):
    load_state["done"] = True
    load_state["ok"] = bool(ok)
    loop.quit()
page.loadFinished.connect(_finish)
QTimer.singleShot(1500, loop.quit)
page.setHtml("<!doctype html><html><body>ok</body></html>", QUrl("about:blank"))
loop.exec()
for _ in range(5):
    app.processEvents()
    time.sleep(0.02)
if load_state["done"] and not load_state["ok"]:
    sys.exit(2)
"""


def _smoke_env() -> dict[str, str]:
    env = dict(os.environ)
    configure_qt_webengine_runtime(env)
    flags = env.get("QTWEBENGINE_CHROMIUM_FLAGS", "").split()
    if "--no-sandbox" not in flags:
        flags.append("--no-sandbox")
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(flags)
    return env


def _returncode_hex(returncode) -> str:
    if returncode is None:
        return ""
    try:
        return hex(int(returncode))
    except (TypeError, ValueError):
        return ""


def check_qt_webengine_available(
    *,
    timeout_s: int = 8,
    env_var: str = "VCP_KLINE_WEBENGINE_PREFLIGHT",
) -> dict:
    value = str(os.environ.get(env_var, "") or "").strip().lower()
    if value in {"0", "false", "no", "off"}:
        return {"ok": True, "reason": "", "disabled": True}

    started_at = time.perf_counter()
    try:
        completed = subprocess.run(  # nosec B603
            [sys.executable, "-c", _webengine_smoke_code()],
            env=_smoke_env(),
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_s)),
            **windows_no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "reason": f"timeout>{timeout_s}s",
            "timeout": True,
            "elapsed_ms": round((time.perf_counter() - started_at) * 1000.0, 1),
        }
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "ok": False,
            "reason": str(exc),
            "elapsed_ms": round((time.perf_counter() - started_at) * 1000.0, 1),
        }

    returncode_hex = _returncode_hex(completed.returncode)
    reason = "" if completed.returncode == 0 else f"returncode={completed.returncode} {returncode_hex}".strip()
    return {
        "ok": completed.returncode == 0,
        "reason": reason,
        "returncode": completed.returncode,
        "returncode_hex": returncode_hex,
        "elapsed_ms": round((time.perf_counter() - started_at) * 1000.0, 1),
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-2000:],
    }
