# -*- coding: utf-8 -*-
"""Runtime environment diagnostics and interpreter selection helpers."""

from __future__ import annotations

import importlib
import os
import re
import sys
from datetime import datetime
from importlib import metadata

from core.logger import get_logger

log = get_logger(__name__)

_WINDOWS_RUNTIME_MODULES = (
    ("PyQt6.QtWebEngineWidgets", "PyQt6-WebEngine"),
    ("akshare", "akshare"),
    ("yfinance", "yfinance"),
    ("win32gui", "pywin32"),
)
WINDOWS_APP_USER_MODEL_ID = "com.zijinresearch.vcphunter.quantterminal"


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


def resolve_project_python(project_root: str, executable: str | None = None) -> str:
    candidates = project_venv_python_candidates(project_root)
    if not candidates:
        return ""

    current_name = os.path.basename(os.path.abspath(executable or sys.executable)).lower()
    if _is_windows():
        preferred_names = ("pythonw.exe", "python.exe") if current_name == "pythonw.exe" else (
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
    return os.path.normcase(current_executable) not in normalized_candidates


def _append_bootstrap_log(project_root: str, message: str) -> None:
    root = os.path.abspath(project_root or "")
    if not root:
        return

    log_dir = os.path.join(root, "data", "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"bootstrap_{datetime.now().strftime('%Y%m%d')}.log")
        with open(log_path, "a", encoding="utf-8") as file_obj:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            file_obj.write(f"{timestamp} {message}\n")
    except OSError:
        return


def relaunch_into_project_venv_if_needed(
    project_root: str,
    *,
    executable: str | None = None,
    argv: list[str] | tuple[str, ...] | None = None,
    env: dict[str, str] | None = None,
    script_path: str | None = None,
    execve=os.execve,
) -> bool:
    if not should_relaunch_into_project_venv(
        project_root,
        executable=executable,
        env=env,
    ):
        return False

    target_python = resolve_project_python(project_root, executable=executable)
    if not target_python:
        return False

    argv_list = list(argv or sys.argv or [])
    raw_script = script_path or (argv_list[0] if argv_list else "")
    if not raw_script:
        return False

    current_executable = os.path.abspath(executable or sys.executable)
    target_script = os.path.abspath(raw_script)
    child_env = dict(os.environ)
    if env:
        child_env.update(env)
    child_env["VCP_ALREADY_RELAUNCHED"] = "1"

    child_argv = [target_python, target_script, *argv_list[1:]]
    _append_bootstrap_log(
        project_root,
        f"[runtime_env] relaunch {current_executable} -> {target_python}",
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
                    f"current executable is not project .venv: {current_executable} "
                    f"(recommended: {preferred_python})"
                )

    if _is_windows():
        for module_name, dependency_name in _WINDOWS_RUNTIME_MODULES:
            try:
                import_module(module_name)
            except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
                issues.append(f"missing runtime dependency: {dependency_name} ({module_name})")

    requests_version = package_version("requests")
    if requests_version and _version_lt(requests_version, "2.33.0"):
        issues.append(f"requests version too old: {requests_version} (need >= 2.33.0)")

    yfinance_version = package_version("yfinance")
    curl_cffi_version = package_version("curl_cffi") or package_version("curl-cffi")
    if yfinance_version.startswith("1.2."):
        if not curl_cffi_version:
            issues.append("yfinance 1.2.x requires curl_cffi")
        elif _version_lt(curl_cffi_version, "0.7.0") or _version_ge(curl_cffi_version, "0.14.0"):
            issues.append(
                f"curl_cffi incompatible with yfinance 1.2.x: {curl_cffi_version} "
                "(need >=0.7,<0.14)"
            )

    return issues


def log_runtime_env_report(project_root: str) -> list[str]:
    """Log the current runtime environment issues without stopping startup."""

    issues = collect_runtime_env_issues(project_root)
    if issues:
        for issue in issues:
            log.warning(f"[runtime_env] {issue}")
    else:
        log.info(f"[runtime_env] ok ({sys.executable})")
    return issues
