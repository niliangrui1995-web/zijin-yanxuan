# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_OUTPUT = Path("tmp/dependency_audit.json")
DEFAULT_TIMEOUT_SECONDS = 120
PIP_AUDIT_TIMEOUT_SECONDS = 180

MANIFEST_PATTERNS = (
    "pyproject.toml",
    "requirements*.txt",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "poetry.lock",
    "uv.lock",
    "pdm.lock",
    "pylock.toml",
)


def build_pip_version_command(python: str) -> list[str]:
    return [python, "-m", "pip", "--version"]


def build_pip_check_command(python: str) -> list[str]:
    return [python, "-m", "pip", "check"]


def build_pip_inspect_command(python: str) -> list[str]:
    return [python, "-m", "pip", "inspect", "--local"]


def build_pip_audit_command(python: str) -> list[str]:
    return [python, "-m", "pip_audit", "--local", "--format", "json", "--progress-spinner", "off"]


def _relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def discover_manifests(root: Path = REPO_ROOT) -> list[dict[str, str]]:
    manifests: list[dict[str, str]] = []
    seen: set[Path] = set()
    for pattern in MANIFEST_PATTERNS:
        for path in sorted(root.glob(pattern)):
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            manifests.append({"path": _relative_path(path, root), "name": path.name})
    return manifests


def _run_command(command: list[str], cwd: Path, timeout_seconds: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "status": "failed",
            "returncode": None,
            "timeout": True,
            "timeout_seconds": timeout_seconds,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
        }
    except OSError as exc:
        return {
            "command": command,
            "status": "failed",
            "returncode": None,
            "timeout": False,
            "timeout_seconds": timeout_seconds,
            "stdout": "",
            "stderr": str(exc),
        }

    return {
        "command": command,
        "status": "ok" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "timeout": False,
        "timeout_seconds": timeout_seconds,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def collect_python_info(python: str) -> dict[str, Any]:
    return {
        "executable": python,
        "current_executable": sys.executable,
        "version": sys.version,
        "version_info": {
            "major": sys.version_info.major,
            "minor": sys.version_info.minor,
            "micro": sys.version_info.micro,
        },
    }


def collect_pip_version(python: str, root: Path = REPO_ROOT) -> dict[str, Any]:
    result = _run_command(build_pip_version_command(python), root, DEFAULT_TIMEOUT_SECONDS)
    return {
        "command": result["command"],
        "status": result["status"],
        "returncode": result["returncode"],
        "version": result["stdout"].strip(),
        "stderr": result["stderr"].strip(),
    }


def collect_pip_check(python: str, root: Path = REPO_ROOT) -> dict[str, Any]:
    result = _run_command(build_pip_check_command(python), root, DEFAULT_TIMEOUT_SECONDS)
    return {
        "command": result["command"],
        "status": result["status"],
        "returncode": result["returncode"],
        "stdout": result["stdout"].strip(),
        "stderr": result["stderr"].strip(),
        "timeout": result["timeout"],
    }


def _package_name(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return str(metadata.get("name") or item.get("name") or "")


def _package_version(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return str(metadata.get("version") or item.get("version") or "")


def collect_pip_inspect(python: str, root: Path = REPO_ROOT) -> dict[str, Any]:
    result = _run_command(build_pip_inspect_command(python), root, DEFAULT_TIMEOUT_SECONDS)
    base = {
        "command": result["command"],
        "returncode": result["returncode"],
        "stderr": result["stderr"].strip(),
        "timeout": result["timeout"],
    }
    if result["status"] != "ok":
        return {**base, "status": "failed", "package_count": 0, "packages": []}

    try:
        payload = json.loads(result["stdout"])
    except json.JSONDecodeError as exc:
        return {
            **base,
            "status": "failed",
            "package_count": 0,
            "packages": [],
            "error": f"invalid pip inspect json: {exc}",
        }

    installed = payload.get("installed", [])
    packages = [
        {"name": _package_name(item), "version": _package_version(item)}
        for item in installed
        if isinstance(item, dict)
    ]
    packages.sort(key=lambda item: item["name"].lower())
    return {
        **base,
        "status": "ok",
        "package_count": len(packages),
        "packages": packages,
        "environment": payload.get("environment", {}),
    }


def _summarize_pip_audit_payload(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        return {"parse_status": "failed", "finding_count": 0, "findings": [], "error": str(exc)}

    dependencies = payload.get("dependencies", [])
    findings = []
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            continue
        vulns = dependency.get("vulns") or dependency.get("vulnerabilities") or []
        if not isinstance(vulns, list):
            continue
        for vuln in vulns:
            if not isinstance(vuln, dict):
                continue
            findings.append(
                {
                    "package": dependency.get("name", ""),
                    "version": dependency.get("version", ""),
                    "id": vuln.get("id") or vuln.get("vulnerability_id") or "",
                    "aliases": vuln.get("aliases", []),
                    "fix_versions": vuln.get("fix_versions", []),
                }
            )

    return {
        "parse_status": "ok",
        "dependency_count": len(dependencies) if isinstance(dependencies, list) else 0,
        "finding_count": len(findings),
        "findings": findings,
    }


def collect_pip_audit(
    python: str,
    root: Path = REPO_ROOT,
    timeout_seconds: int = PIP_AUDIT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    command = build_pip_audit_command(python)
    if importlib.util.find_spec("pip_audit") is None:
        return {
            "command": command,
            "status": "skipped",
            "reason": "pip_audit module is not installed",
            "returncode": None,
            "timeout": False,
            "finding_count": 0,
            "findings": [],
        }

    result = _run_command(command, root, timeout_seconds)
    base = {
        "command": result["command"],
        "returncode": result["returncode"],
        "stderr": result["stderr"].strip(),
        "timeout": result["timeout"],
        "timeout_seconds": timeout_seconds,
    }
    if result["timeout"]:
        return {**base, "status": "failed", "finding_count": 0, "findings": []}

    summary = _summarize_pip_audit_payload(result["stdout"])
    if summary["finding_count"] > 0:
        return {**base, "status": "findings", **summary}
    if result["returncode"] == 0 and summary["parse_status"] == "ok":
        return {**base, "status": "ok", **summary}
    return {**base, "status": "failed", **summary}


def collect_report(root: Path = REPO_ROOT, python: str | None = None) -> dict[str, Any]:
    python_executable = python or sys.executable
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(root),
        "manifests": discover_manifests(root),
        "python": collect_python_info(python_executable),
        "pip": {
            "version": collect_pip_version(python_executable, root),
            "check": collect_pip_check(python_executable, root),
            "inspect": collect_pip_inspect(python_executable, root),
        },
        "pip_audit": collect_pip_audit(python_executable, root),
    }


def audit_exit_code(report: dict[str, Any]) -> int:
    return 1 if report.get("pip_audit", {}).get("status") == "findings" else 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a reproducible dependency audit JSON report.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def _resolve_output_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report = collect_report()
    output = _resolve_output_path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[dependency-audit] wrote {output}", flush=True)
    return audit_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
