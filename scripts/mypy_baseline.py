# -*- coding: utf-8 -*-
"""Enforce a deterministic gradual UI Mypy no-new-diagnostics baseline."""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPO_ROOT / "config" / "mypy_ui_baseline.json"
SCHEMA_VERSION = 1
DISPLAY_LIMIT = 10
BASELINE_POLICY = "exact-current-baseline; updates may only reduce unless --allow-new"
MYPY_ARGUMENTS = (
    "-m",
    "mypy",
)
MYPY_TARGETS = (
    "ui/kline_pool_state.py",
    "ui/kline_typing.py",
    "ui/kline_window_recovery.py",
)
MYPY_TARGET = " ".join(MYPY_TARGETS)
MYPY_RATCHET_PREFIXES = ("ui/",)
MYPY_OPTIONS = (
    "--output",
    "json",
    "--no-error-summary",
    "--no-pretty",
    "--no-warn-unused-configs",
    "--num-workers",
    "1",
)


@dataclass(frozen=True, order=True, slots=True)
class DiagnosticFingerprint:
    path: str
    severity: str
    code: str
    message: str


def normalize_repo_path(raw_path: str, *, repo_root: Path = REPO_ROOT) -> str:
    """Return one stable forward-slash path relative to the repository root."""
    normalized = str(raw_path or "").replace("\\", "/")
    candidate = Path(normalized)
    if candidate.is_absolute():
        try:
            normalized = candidate.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError(f"Mypy diagnostic path is outside the repository: {raw_path}") from exc
    path = PurePosixPath(posixpath.normpath(normalized)).as_posix()
    while path.startswith("./"):
        path = path[2:]
    if not path or path == "." or path == ".." or path.startswith("../"):
        raise ValueError(f"Invalid repository-relative Mypy path: {raw_path}")
    return path


def fingerprint_diagnostic(
    diagnostic: Mapping[str, object], *, repo_root: Path = REPO_ROOT
) -> DiagnosticFingerprint:
    return DiagnosticFingerprint(
        path=normalize_repo_path(str(diagnostic.get("file") or ""), repo_root=repo_root),
        severity=str(diagnostic.get("severity") or "error"),
        code=str(diagnostic.get("code") or "unknown"),
        message=str(diagnostic.get("message") or ""),
    )


def parse_mypy_json_lines(output: str, *, repo_root: Path = REPO_ROOT) -> Counter[DiagnosticFingerprint]:
    fingerprints: Counter[DiagnosticFingerprint] = Counter()
    for line_number, raw_line in enumerate(output.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid Mypy JSON output on line {line_number}: {raw_line}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Mypy JSON output line {line_number} is not an object")
        fingerprints[fingerprint_diagnostic(payload, repo_root=repo_root)] += 1
    return fingerprints


def remove_mypy_worker_shards(*, repo_root: Path = REPO_ROOT) -> None:
    """Remove only Mypy JSON worker fragments owned by this repository gate."""
    resolved_root = repo_root.resolve()
    for candidate in repo_root.glob(".mypy_worker.*.json"):
        if candidate.is_file() and candidate.resolve().parent == resolved_root:
            candidate.unlink()


def _subprocess_env() -> dict[str, str]:
    child_env = dict(os.environ)
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    return child_env


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        command,
        cwd=REPO_ROOT,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def collect_mypy_diagnostics() -> tuple[str, Counter[DiagnosticFingerprint]]:
    version_result = _run([sys.executable, "-m", "mypy", "--version"])
    if version_result.returncode != 0:
        raise RuntimeError(version_result.stderr.strip() or "Unable to determine Mypy version")
    result = _run([sys.executable, *MYPY_ARGUMENTS, *MYPY_TARGETS, *MYPY_OPTIONS])
    if result.returncode not in {0, 1}:
        details = result.stderr.strip() or result.stdout.strip() or "unknown Mypy failure"
        raise RuntimeError(f"Mypy failed for {MYPY_TARGET} ({result.returncode}): {details}")
    if result.stderr.strip():
        raise RuntimeError(f"Unexpected Mypy stderr for {MYPY_TARGET}: {result.stderr.strip()}")
    return version_result.stdout.strip(), parse_mypy_json_lines(result.stdout)


def git_untracked_repo_paths() -> frozenset[str]:
    command = ["git", "ls-files", "--others", "--exclude-standard"]
    result = _run(command)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Git command failed: {' '.join(command)}")
    paths = {
        normalize_repo_path(line)
        for line in result.stdout.splitlines()
        if line.strip()
    }
    return frozenset(paths)


def _entry(fingerprint: DiagnosticFingerprint, count: int) -> dict[str, object]:
    return {
        "path": fingerprint.path,
        "severity": fingerprint.severity,
        "code": fingerprint.code,
        "message": fingerprint.message,
        "count": int(count),
    }


def baseline_document(
    diagnostics: Mapping[DiagnosticFingerprint, int], *, mypy_version: str, reason: str
) -> dict[str, object]:
    entries = [_entry(item, diagnostics[item]) for item in sorted(diagnostics)]
    return {
        "schema_version": SCHEMA_VERSION,
        "policy": BASELINE_POLICY,
        "scope": MYPY_TARGET,
        "identity_fields": ["path", "severity", "code", "message"],
        "mypy_version": mypy_version,
        "command": ["python", *MYPY_ARGUMENTS, *MYPY_TARGETS, *MYPY_OPTIONS],
        "update_reason": reason,
        "diagnostic_count": sum(diagnostics.values()),
        "diagnostics": entries,
    }


def write_baseline(
    path: Path,
    diagnostics: Mapping[DiagnosticFingerprint, int],
    *,
    mypy_version: str,
    reason: str,
) -> None:
    document = baseline_document(diagnostics, mypy_version=mypy_version, reason=reason)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load_entry(payload: Mapping[str, object]) -> tuple[DiagnosticFingerprint, int]:
    fingerprint = DiagnosticFingerprint(
        path=normalize_repo_path(str(payload.get("path") or "")),
        severity=str(payload.get("severity") or "error"),
        code=str(payload.get("code") or "unknown"),
        message=str(payload.get("message") or ""),
    )
    count = payload.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError(f"Invalid baseline count for {fingerprint.path}: {count}")
    return fingerprint, count


def load_baseline(path: Path) -> tuple[str, Counter[DiagnosticFingerprint]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported Mypy baseline schema in {path}")
    entries = payload.get("diagnostics")
    if not isinstance(entries, list):
        raise ValueError(f"Invalid Mypy baseline diagnostics in {path}")
    diagnostics: Counter[DiagnosticFingerprint] = Counter()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"Invalid Mypy baseline entry in {path}")
        fingerprint, count = _load_entry(entry)
        if fingerprint in diagnostics:
            raise ValueError(f"Duplicate Mypy baseline fingerprint: {fingerprint}")
        diagnostics[fingerprint] = count
    expected_count = payload.get("diagnostic_count")
    if expected_count != sum(diagnostics.values()):
        raise ValueError(f"Mypy baseline diagnostic_count does not match entries in {path}")
    return str(payload.get("mypy_version") or ""), diagnostics


def compare_diagnostics(
    baseline: Mapping[DiagnosticFingerprint, int],
    current: Mapping[DiagnosticFingerprint, int],
) -> tuple[Counter[DiagnosticFingerprint], Counter[DiagnosticFingerprint]]:
    return Counter(current) - Counter(baseline), Counter(baseline) - Counter(current)


def diagnostic_count_for_prefix(
    diagnostics: Mapping[DiagnosticFingerprint, int], prefix: str
) -> int:
    """Count diagnostics in one explicitly protected repository subtree."""
    normalized_prefix = str(prefix or "").replace("\\", "/")
    return sum(int(count) for fingerprint, count in diagnostics.items() if fingerprint.path.startswith(normalized_prefix))


def _format_diagnostics(diagnostics: Mapping[DiagnosticFingerprint, int]) -> Iterable[str]:
    for item in sorted(diagnostics):
        yield f"{item.path}: [{item.code}] {item.message} (count={diagnostics[item]})"


def _print_diagnostics(prefix: str, diagnostics: Mapping[DiagnosticFingerprint, int]) -> None:
    lines = list(_format_diagnostics(diagnostics))
    for line in lines[:DISPLAY_LIMIT]:
        print(f"[mypy-baseline] {prefix} {line}")
    if len(lines) > DISPLAY_LIMIT:
        print(f"[mypy-baseline] {prefix} ... {len(lines) - DISPLAY_LIMIT} more fingerprints omitted")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enforce the gradual UI Mypy diagnostic baseline.")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--update", action="store_true", help="Replace the baseline with current diagnostics.")
    parser.add_argument(
        "--allow-new",
        action="store_true",
        help="Explicitly accept new diagnostics during --update; the reason remains in version control.",
    )
    parser.add_argument("--reason", default="", help="Required audit reason when --update is used.")
    return parser.parse_args(argv)


def _check_baseline(
    path: Path, mypy_version: str, current: Counter[DiagnosticFingerprint]
) -> int:
    baseline_version, baseline = load_baseline(path)
    if baseline_version != mypy_version:
        print(f"[mypy-baseline] version mismatch: baseline={baseline_version!r}, current={mypy_version!r}")
        return 2
    added, resolved = compare_diagnostics(baseline, current)
    print(
        f"[mypy-baseline] baseline={sum(baseline.values())} current={sum(current.values())} "
        f"new={sum(added.values())} resolved={sum(resolved.values())}"
    )
    for prefix in MYPY_RATCHET_PREFIXES:
        print(
            f"[mypy-baseline] ratchet={prefix} current={diagnostic_count_for_prefix(current, prefix)} "
            f"new={diagnostic_count_for_prefix(added, prefix)}"
        )
    _print_diagnostics("NEW", added)
    if resolved:
        print(
            "[mypy-baseline] baseline is stale after resolved diagnostics; "
            "update it with --update --reason <audit-reason>"
        )
    return 1 if added or resolved else 0


def _update_baseline(
    path: Path,
    current: Counter[DiagnosticFingerprint],
    *,
    mypy_version: str,
    reason: str,
    allow_new: bool,
) -> int:
    print(f"[mypy-baseline] current diagnostics={sum(current.values())}")
    if path.exists():
        _baseline_version, baseline = load_baseline(path)
        added, resolved = compare_diagnostics(baseline, current)
        if added and not allow_new:
            print("[mypy-baseline] update refused: current diagnostics contain new fingerprints")
            _print_diagnostics("NEW", added)
            print("[mypy-baseline] review, then repeat with --allow-new and the audit reason")
            return 1
        print(
            f"[mypy-baseline] update delta: new={sum(added.values())} "
            f"resolved={sum(resolved.values())}"
        )
    elif not allow_new:
        untracked_paths = git_untracked_repo_paths()
        untracked = Counter(
            {item: count for item, count in current.items() if item.path in untracked_paths}
        )
        if untracked:
            print("[mypy-baseline] initial update refused: untracked files contain diagnostics")
            _print_diagnostics("UNTRACKED", untracked)
            print("[mypy-baseline] fix them, or review and repeat with --allow-new")
            return 1
    write_baseline(path, current, mypy_version=mypy_version, reason=reason)
    print(f"[mypy-baseline] updated {path} with {sum(current.values())} diagnostics")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    try:
        if args.allow_new and not args.update:
            raise ValueError("--allow-new is valid only with --update")
        reason = str(args.reason or "").strip()
        if args.update and not reason:
            raise ValueError("--reason is required when updating the Mypy baseline")
        remove_mypy_worker_shards()
        try:
            mypy_version, current = collect_mypy_diagnostics()
        finally:
            remove_mypy_worker_shards()
        if args.update:
            return _update_baseline(
                args.baseline,
                current,
                mypy_version=mypy_version,
                reason=reason,
                allow_new=bool(args.allow_new),
            )
        return _check_baseline(args.baseline, mypy_version, current)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[mypy-baseline] error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
