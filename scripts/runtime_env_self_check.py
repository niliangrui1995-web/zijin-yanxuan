from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _path_snapshot(path: str | Path) -> dict[str, Any]:
    item = Path(path)
    exists = item.exists()
    result: dict[str, Any] = {
        "path": str(item),
        "exists": exists,
    }
    if exists:
        try:
            stat = item.stat()
            result["size_bytes"] = int(stat.st_size)
            result["updated_at"] = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
        except OSError as exc:
            result["stat_error"] = f"{exc.__class__.__name__}:{exc}"
    return result


def _import_snapshot(module_name: str) -> dict[str, Any]:
    try:
        module = importlib.import_module(module_name)
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return {
            "module": module_name,
            "ok": False,
            "error": f"{exc.__class__.__name__}:{exc}",
        }
    return {
        "module": module_name,
        "ok": True,
        "version": str(getattr(module, "__version__", "") or ""),
    }


def _git_value(args: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired, TypeError, ValueError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _tdx_vipdoc_snapshot() -> dict[str, Any]:
    try:
        from vcp.utils import _load_tdx_local_config

        vipdoc = _load_tdx_local_config()
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        vipdoc = None

    path = Path(vipdoc or "")
    return {
        "path": str(path) if vipdoc else "",
        "exists": bool(vipdoc and path.exists()),
        "sh_exists": bool(vipdoc and (path / "sh").exists()),
        "sz_exists": bool(vipdoc and (path / "sz").exists()),
    }


def build_report(*, skip_webengine_preflight: bool = False, webengine_timeout_s: int = 8) -> dict[str, Any]:
    from app.services.runtime_constants import APP_VERSION, FINANCE_CACHE_FILE, RPS_CACHE_FILE

    imports = {
        "PyQt6": _import_snapshot("PyQt6"),
        "PyQt6-WebEngine": _import_snapshot("PyQt6.QtWebEngineWidgets"),
        "psutil": _import_snapshot("psutil"),
    }
    if skip_webengine_preflight:
        webengine_preflight = {"ok": True, "skipped": True}
    else:
        from app.services.kline_webengine_preflight import check_qt_webengine_available

        webengine_preflight = check_qt_webengine_available(timeout_s=webengine_timeout_s)

    cache_files = {
        "rps_cache": _path_snapshot(RPS_CACHE_FILE),
        "finance_cache": _path_snapshot(FINANCE_CACHE_FILE),
        "sqlite_state": _path_snapshot(PROJECT_ROOT / "data" / "vcp_hunter.db"),
    }
    hard_failures = [
        key
        for key, value in imports.items()
        if key in {"PyQt6", "PyQt6-WebEngine"} and not value.get("ok")
    ]
    if not webengine_preflight.get("ok"):
        hard_failures.append("qt_webengine_preflight")

    status = "fail" if hard_failures else "ok"
    return {
        "schema_version": 1,
        "report_type": "runtime_env_self_check",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "python": {
            "executable": sys.executable,
            "version": sys.version,
            "version_info": list(sys.version_info[:3]),
        },
        "app": {
            "version": APP_VERSION,
            "project_root": str(PROJECT_ROOT),
        },
        "git": {
            "branch": _git_value(["branch", "--show-current"]),
            "commit": _git_value(["rev-parse", "HEAD"]),
            "commit_short": _git_value(["log", "-1", "--oneline"]),
            "upstream": _git_value(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]),
        },
        "imports": imports,
        "qt_webengine_preflight": webengine_preflight,
        "tdx_vipdoc": _tdx_vipdoc_snapshot(),
        "cache_files": cache_files,
        "diagnostics": {
            "psutil_available": bool(imports["psutil"].get("ok")),
            "qt_qpa_platform": os.environ.get("QT_QPA_PLATFORM", ""),
            "qtwebengine_chromium_flags": os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", ""),
        },
        "failures": hard_failures,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Emit a JSON runtime environment self-check report.")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--skip-webengine-preflight", action="store_true")
    parser.add_argument("--webengine-timeout-s", type=int, default=8)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report = build_report(
        skip_webengine_preflight=bool(args.skip_webengine_preflight),
        webengine_timeout_s=int(args.webengine_timeout_s),
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 1 if report.get("status") == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
