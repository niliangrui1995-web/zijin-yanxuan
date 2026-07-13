# -*- coding: utf-8 -*-
"""Runtime environment diagnostics and interpreter selection helpers."""

from __future__ import annotations

import importlib
import os
import re
import sys
from datetime import datetime
from importlib import metadata

_WINDOWS_RUNTIME_MODULES = (
    ("PyQt6.QtWebEngineWidgets", "PyQt6-WebEngine"),
    ("akshare", "akshare"),
    ("yfinance", "yfinance"),
    ("win32gui", "pywin32"),
)
WINDOWS_APP_USER_MODEL_ID = "com.zijinresearch.vcphunter.quantterminal"
QTWEBENGINE_RUNTIME_FLAGS = (
    "--disable-gpu",
    "--disable-gpu-compositing",
    "--disable-extensions",
    "--disable-background-networking",
)
NUMERIC_THREAD_ENV_KEYS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "NUMEXPR_MAX_THREADS",
)
DEFAULT_NUMERIC_THREAD_COUNT = "2"


def _runtime_log():
    from core.logger import get_logger

    return get_logger(__name__)


def _merge_chromium_flags(current: str, required: tuple[str, ...] = QTWEBENGINE_RUNTIME_FLAGS) -> str:
    flags = [flag for flag in str(current or "").split() if flag]
    seen = set(flags)
    for flag in required:
        if flag not in seen:
            flags.append(flag)
            seen.add(flag)
    return " ".join(flags)


def configure_qt_webengine_runtime(env: dict[str, str] | None = None) -> dict[str, str]:
    """Apply low-overhead QtWebEngine defaults before the first WebEngine import."""

    target = os.environ if env is None else env
    configure_numeric_thread_runtime(target)
    target.setdefault("QT_OPENGL", "software")
    target["QTWEBENGINE_CHROMIUM_FLAGS"] = _merge_chromium_flags(target.get("QTWEBENGINE_CHROMIUM_FLAGS", ""))
    return target


def configure_numeric_thread_runtime(env: dict[str, str] | None = None) -> dict[str, str]:
    target = os.environ if env is None else env
    default_count = str(target.get("VCP_NUMERIC_THREAD_COUNT") or DEFAULT_NUMERIC_THREAD_COUNT).strip()
    if not default_count:
        default_count = DEFAULT_NUMERIC_THREAD_COUNT
    for key in NUMERIC_THREAD_ENV_KEYS:
        target.setdefault(key, default_count)
    return target


def _parse_version_tuple(version_text: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(version_text or ""))
    if not parts:
        return tuple()
    return tuple(int(part) for part in parts[:4])


def _version_lt(version_text: str, minimum: str) -> bool:
    return _parse_version_tuple(version_text) < _parse_version_tuple(minimum)


def _version_ge(version_text: str, minimum: str) -> bool:
    return _parse_version_tuple(version_text) >= _parse_version_tuple(minimum)


def _safe_package_version(package_name: str) -> str:
    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return ""


def _is_windows() -> bool:
    return os.name == "nt"


def set_windows_app_user_model_id(
    app_id: str = WINDOWS_APP_USER_MODEL_ID,
    *,
    shell32=None,
) -> bool:
    if not _is_windows():
        return False

    normalized_app_id = str(app_id or "").strip()
    if not normalized_app_id:
        return False

    try:
        import ctypes

        shell32_api = shell32 or ctypes.windll.shell32
        set_app_id = shell32_api.SetCurrentProcessExplicitAppUserModelID
        if hasattr(set_app_id, "argtypes"):
            set_app_id.argtypes = [ctypes.c_wchar_p]
            set_app_id.restype = ctypes.c_long
        return set_app_id(normalized_app_id) == 0
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False


def project_venv_python_candidates(project_root: str) -> list[str]:
    root = os.path.abspath(project_root or "")
    if not root:
        return []

    candidates: list[str] = []
    if _is_windows():
        scripts_dir = os.path.join(root, ".venv", "Scripts")
        for executable_name in ("python.exe", "pythonw.exe"):
            candidate = os.path.abspath(os.path.join(scripts_dir, executable_name))
            if os.path.exists(candidate):
                candidates.append(candidate)
        return candidates

    candidate = os.path.abspath(os.path.join(root, ".venv", "bin", "python"))
    if os.path.exists(candidate):
        candidates.append(candidate)
    return candidates


def resolve_project_python(
    project_root: str,
    executable: str | None = None,
    *,
    prefer_windowless: bool = False,
) -> str:
    candidates = project_venv_python_candidates(project_root)
    if not candidates:
        return ""

    current_name = os.path.basename(os.path.abspath(executable or sys.executable)).lower()
    if _is_windows():
        if prefer_windowless or current_name == "pythonw.exe":
            preferred_names = ("pythonw.exe", "python.exe")
        else:
            preferred_names = (
                "python.exe",
                "pythonw.exe",
            )
        for preferred_name in preferred_names:
            for candidate in candidates:
                if os.path.basename(candidate).lower() == preferred_name:
                    return candidate

    return candidates[0]


def should_relaunch_into_project_venv(
    project_root: str,
    *,
    executable: str | None = None,
    env: dict[str, str] | None = None,
    prefer_windowless: bool = False,
) -> bool:
    current_env = env or os.environ
    if current_env.get("VCP_SKIP_VENV_RELAUNCH") == "1":
        return False
    if current_env.get("VCP_ALREADY_RELAUNCHED") == "1":
        return False

    preferred_candidates = project_venv_python_candidates(project_root)
    if not preferred_candidates:
        return False

    current_executable = os.path.abspath(executable or sys.executable)
    normalized_candidates = {os.path.normcase(path) for path in preferred_candidates}
    if os.path.normcase(current_executable) not in normalized_candidates:
        return True

    if prefer_windowless and _is_windows():
        target_python = resolve_project_python(
            project_root,
            executable=executable,
            prefer_windowless=True,
        )
        return bool(target_python) and os.path.normcase(current_executable) != os.path.normcase(target_python)

    return False


def _format_bootstrap_value(value) -> str:
    text = str(value)
    return text.replace("\r", "\\r").replace("\n", "\\n")


def append_bootstrap_event(
    project_root: str,
    event: str,
    *,
    extra: dict[str, object] | None = None,
) -> str:
    root = os.path.abspath(project_root or "")
    if not root:
        return ""

    log_dir = os.path.join(root, "data", "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"bootstrap_{datetime.now().strftime('%Y%m%d')}.log")
        parts = [f"[bootstrap] {_format_bootstrap_value(event)}", f"pid={os.getpid()}"]
        for key, value in sorted((extra or {}).items()):
            if value is None or value == "":
                continue
            parts.append(f"{key}={_format_bootstrap_value(value)}")
        with open(log_path, "a", encoding="utf-8") as file_obj:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            file_obj.write(f"{timestamp} {' | '.join(parts)}\n")
        return log_path
    except OSError:
        return ""


def relaunch_into_project_venv_if_needed(
    project_root: str,
    *,
    executable: str | None = None,
    argv: list[str] | tuple[str, ...] | None = None,
    env: dict[str, str] | None = None,
    script_path: str | None = None,
    execve=os.execve,
) -> bool:
    argv_list = list(argv or sys.argv or [])
    raw_script = script_path or (argv_list[0] if argv_list else "")
    if not raw_script:
        return False

    prefer_windowless = str(raw_script).lower().endswith(".pyw")
    if not should_relaunch_into_project_venv(
        project_root,
        executable=executable,
        env=env,
        prefer_windowless=prefer_windowless,
    ):
        return False

    target_python = resolve_project_python(
        project_root,
        executable=executable,
        prefer_windowless=prefer_windowless,
    )
    if not target_python:
        return False

    current_executable = os.path.abspath(executable or sys.executable)
    target_script = os.path.abspath(raw_script)
    child_env = dict(os.environ)
    if env:
        child_env.update(env)
    child_env["VCP_ALREADY_RELAUNCHED"] = "1"

    child_argv = [target_python, target_script, *argv_list[1:]]
    append_bootstrap_event(
        project_root,
        "runtime_env.relaunch",
        extra={
            "from": current_executable,
            "to": target_python,
            "script": target_script,
        },
    )
    execve(target_python, child_argv, child_env)
    return True


def collect_runtime_env_issues(
    project_root: str,
    executable: str | None = None,
    import_module=importlib.import_module,
    package_version=_safe_package_version,
) -> list[str]:
    """Collect non-fatal runtime environment issues."""

    issues: list[str] = []
    current_executable = os.path.abspath(executable or sys.executable)

    if project_root:
        preferred_candidates = project_venv_python_candidates(project_root)
        if preferred_candidates:
            normalized_candidates = {os.path.normcase(path) for path in preferred_candidates}
            preferred_python = resolve_project_python(project_root, executable=current_executable)
            if os.path.normcase(current_executable) not in normalized_candidates:
                issues.append(
                    f"current executable is not project .venv: {current_executable} (recommended: {preferred_python})"
                )

    if _is_windows():
        for module_name, dependency_name in _WINDOWS_RUNTIME_MODULES:
            try:
                import_module(module_name)
            except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
                issues.append(f"missing runtime dependency: {dependency_name} ({module_name})")

    requests_version = package_version("requests")
    if requests_version and _version_lt(requests_version, "2.34.2"):
        issues.append(f"requests version too old: {requests_version} (need >= 2.34.2)")

    yfinance_version = package_version("yfinance")
    curl_cffi_version = package_version("curl_cffi") or package_version("curl-cffi")
    if yfinance_version and _version_lt(yfinance_version, "1.4.1"):
        issues.append(f"yfinance version incompatible: {yfinance_version} (need >=1.4.1)")
    if not curl_cffi_version:
        issues.append("missing runtime dependency: curl_cffi")
    elif _version_lt(curl_cffi_version, "0.15.0") or _version_ge(curl_cffi_version, "0.16.0"):
        issues.append(f"curl_cffi version incompatible: {curl_cffi_version} (need >=0.15.0,<0.16)")

    lxml_version = package_version("lxml")
    if lxml_version and _version_lt(lxml_version, "6.1.1"):
        issues.append(f"lxml version too old: {lxml_version} (need >=6.1.1)")

    return issues


def log_runtime_env_report(project_root: str) -> list[str]:
    """Log the current runtime environment issues without stopping startup."""

    issues = collect_runtime_env_issues(project_root)
    logger = _runtime_log()
    if issues:
        for issue in issues:
            logger.warning(f"[runtime_env] {issue}")
    else:
        logger.info(f"[runtime_env] ok ({sys.executable})")
    return issues
