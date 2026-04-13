# -*- coding: utf-8 -*-
"""Runtime environment diagnostics for the desktop app."""

from __future__ import annotations

import importlib
import os
import re
import sys
from importlib import metadata

from core.logger import get_logger

log = get_logger(__name__)

_WINDOWS_RUNTIME_MODULES = (
    ("PyQt6.QtWebEngineWidgets", "PyQt6-WebEngine"),
    ("akshare", "akshare"),
    ("yfinance", "yfinance"),
    ("win32gui", "pywin32"),
)


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
        preferred_python = os.path.join(project_root, ".venv", "Scripts", "python.exe")
        if os.path.exists(preferred_python):
            preferred_python = os.path.abspath(preferred_python)
            if os.path.normcase(preferred_python) != os.path.normcase(current_executable):
                issues.append(
                    f"当前解释器不是项目 .venv: {current_executable} (建议使用 {preferred_python})"
                )

    if _is_windows():
        for module_name, dependency_name in _WINDOWS_RUNTIME_MODULES:
            try:
                import_module(module_name)
            except Exception:
                issues.append(f"缺少运行时依赖: {dependency_name} ({module_name})")

    requests_version = package_version("requests")
    if requests_version and _version_lt(requests_version, "2.33.0"):
        issues.append(f"requests 版本过低: {requests_version} (需要 >= 2.33.0)")

    yfinance_version = package_version("yfinance")
    curl_cffi_version = package_version("curl_cffi") or package_version("curl-cffi")
    if yfinance_version.startswith("1.2."):
        if not curl_cffi_version:
            issues.append("yfinance 1.2.x 缺少 curl_cffi 依赖")
        else:
            if _version_lt(curl_cffi_version, "0.7.0") or _version_ge(curl_cffi_version, "0.14.0"):
                issues.append(
                    f"curl_cffi 与 yfinance 1.2.x 不兼容: {curl_cffi_version} (需要 >=0.7,<0.14)"
                )

    return issues


def log_runtime_env_report(project_root: str) -> list[str]:
    """Log the current runtime environment issues without stopping startup."""

    issues = collect_runtime_env_issues(project_root)
    if issues:
        for issue in issues:
            log.warning(f"[环境自检] {issue}")
    else:
        log.info(f"[环境自检] 通过 ({sys.executable})")
    return issues
