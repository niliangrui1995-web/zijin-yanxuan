from __future__ import annotations

import os
import subprocess  # nosec B404
import sys
import threading
import time

from core.runtime_env import configure_qt_webengine_runtime
from infra.tasks.process_runner import windows_no_window_kwargs

_PREFLIGHT_POLL_SECONDS = 0.05
_PREFLIGHT_TERMINATE_GRACE_SECONDS = 0.2
_PROCESS_OPERATION_ERRORS = (OSError, RuntimeError, TypeError, ValueError)
_PROCESS_STOP_ERRORS = (*_PROCESS_OPERATION_ERRORS, subprocess.TimeoutExpired)


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
QTimer.singleShot(5000, loop.quit)
page.setHtml("<!doctype html><html><body>ok</body></html>", QUrl("about:blank"))
loop.exec()
for _ in range(5):
    app.processEvents()
    time.sleep(0.02)
if not load_state["done"]:
    sys.exit(3)
if not load_state["ok"]:
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


def _webengine_subprocess_kwargs() -> dict:
    kwargs = dict(windows_no_window_kwargs() or {})
    if os.name != "nt":
        return kwargs
    below_normal = int(getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0) or 0)
    if below_normal:
        kwargs["creationflags"] = int(kwargs.get("creationflags", 0) or 0) | below_normal
    return kwargs


def _cancel_requested(cancellation_event: threading.Event | None) -> bool:
    return cancellation_event is not None and cancellation_event.is_set()


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000.0, 1)


def _communicate_with_grace(process) -> tuple[str, str] | None:
    try:
        return process.communicate(timeout=_PREFLIGHT_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        return None


def _stop_process(process) -> tuple[str, str, bool]:
    """Stop a preflight child within a hard deadline and report whether it was reaped."""
    if process.poll() is None:
        try:
            process.terminate()
        except _PROCESS_OPERATION_ERRORS:
            pass

    output = _communicate_with_grace(process)
    if output is None:
        if process.poll() is None:
            try:
                process.kill()
            except _PROCESS_OPERATION_ERRORS:
                pass
        output = _communicate_with_grace(process)
    if output is None:
        clean = process.poll() is not None
        if clean:
            process.wait()
        return "", "", clean

    stdout, stderr = output
    process.wait()
    return str(stdout or ""), str(stderr or ""), True


def _cancelled_result(
    started_at: float,
    *,
    stdout: str | None = None,
    stderr: str | None = None,
    process_cleanup_ok: bool = True,
) -> dict:
    result = {
        "ok": False,
        "reason": "cancelled",
        "cancelled": True,
        "elapsed_ms": _elapsed_ms(started_at),
        "process_cleanup_ok": bool(process_cleanup_ok),
    }
    if stdout is not None:
        result["stdout"] = stdout[-2000:]
        result["stderr"] = str(stderr or "")[-2000:]
    return result


def _timeout_result(
    timeout_s: int,
    started_at: float,
    *,
    stdout: str,
    stderr: str,
    process_cleanup_ok: bool,
) -> dict:
    return {
        "ok": False,
        "reason": f"timeout>{timeout_s}s",
        "timeout": True,
        "elapsed_ms": _elapsed_ms(started_at),
        "stdout": stdout[-2000:],
        "stderr": stderr[-2000:],
        "process_cleanup_ok": bool(process_cleanup_ok),
    }


def _error_result(
    started_at: float,
    error: Exception,
    *,
    process_cleanup_ok: bool = True,
) -> dict:
    return {
        "ok": False,
        "reason": str(error),
        "elapsed_ms": _elapsed_ms(started_at),
        "process_cleanup_ok": bool(process_cleanup_ok),
    }


def _completed_result(process, started_at: float, stdout: str, stderr: str) -> dict:
    process.wait()
    returncode_hex = _returncode_hex(process.returncode)
    reason = (
        ""
        if process.returncode == 0
        else f"returncode={process.returncode} {returncode_hex}".strip()
    )
    return {
        "ok": process.returncode == 0,
        "reason": reason,
        "returncode": process.returncode,
        "returncode_hex": returncode_hex,
        "elapsed_ms": _elapsed_ms(started_at),
        "stdout": str(stdout or "")[-2000:],
        "stderr": str(stderr or "")[-2000:],
        "process_cleanup_ok": True,
    }


def _spawn_webengine_process():
    return subprocess.Popen(  # nosec B603
        [sys.executable, "-c", _webengine_smoke_code()],
        env=_smoke_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **_webengine_subprocess_kwargs(),
    )


def _poll_webengine_process(
    process,
    *,
    timeout_s: int,
    deadline: float,
    started_at: float,
    cancellation_event: threading.Event | None,
) -> dict:
    while True:
        if _cancel_requested(cancellation_event):
            stdout, stderr, clean = _stop_process(process)
            return _cancelled_result(
                started_at,
                stdout=stdout,
                stderr=stderr,
                process_cleanup_ok=clean,
            )

        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            stdout, stderr, clean = _stop_process(process)
            return _timeout_result(
                timeout_s,
                started_at,
                stdout=stdout,
                stderr=stderr,
                process_cleanup_ok=clean,
            )
        try:
            stdout, stderr = process.communicate(
                timeout=min(_PREFLIGHT_POLL_SECONDS, remaining)
            )
        except subprocess.TimeoutExpired:
            continue
        return _completed_result(process, started_at, stdout, stderr)


def _run_webengine_process(
    process,
    *,
    timeout_s: int,
    deadline: float,
    started_at: float,
    cancellation_event: threading.Event | None,
) -> dict:
    try:
        return _poll_webengine_process(
            process,
            timeout_s=timeout_s,
            deadline=deadline,
            started_at=started_at,
            cancellation_event=cancellation_event,
        )
    except _PROCESS_OPERATION_ERRORS as exc:
        process_cleanup_ok = False
        try:
            _stdout, _stderr, process_cleanup_ok = _stop_process(process)
        except _PROCESS_STOP_ERRORS:
            pass
        return _error_result(
            started_at,
            exc,
            process_cleanup_ok=process_cleanup_ok,
        )


def check_qt_webengine_available(
    *,
    timeout_s: int = 8,
    env_var: str = "VCP_KLINE_WEBENGINE_PREFLIGHT",
    cancellation_event: threading.Event | None = None,
) -> dict:
    value = str(os.environ.get(env_var, "") or "").strip().lower()
    if value in {"0", "false", "no", "off"}:
        return {"ok": True, "reason": "", "disabled": True}

    started_at = time.perf_counter()
    if _cancel_requested(cancellation_event):
        return _cancelled_result(started_at)

    timeout_seconds = max(1, int(timeout_s))
    try:
        process = _spawn_webengine_process()
    except _PROCESS_OPERATION_ERRORS as exc:
        return _error_result(started_at, exc)
    return _run_webengine_process(
        process,
        timeout_s=timeout_s,
        deadline=started_at + timeout_seconds,
        started_at=started_at,
        cancellation_event=cancellation_event,
    )
