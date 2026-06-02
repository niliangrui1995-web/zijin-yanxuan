# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]

SOURCE_ROOTS = (
    "app",
    "core",
    "domains",
    "infra",
    "ui",
    "vcp",
)

DIRECT_HTTP_METHODS = {
    "delete",
    "get",
    "head",
    "options",
    "patch",
    "post",
    "put",
    "request",
}

LEGACY_ALLOWED_DIRECT_HTTP_FILES = {
    "infra/http_safety.py": "central HTTPS wrapper implementation",
    "vcp/fetchers/asian_kline_fetcher.py": "legacy market-data fetcher migration target",
    "ui/tabs/asian_market_workers.py": "legacy Asian-market worker migration target",
    "domains/global_earnings_calendar/providers/alpha_vantage.py": "legacy earnings provider migration target",
    "domains/global_earnings_calendar/providers/asia_disclosures.py": "legacy earnings provider migration target",
    "domains/global_earnings_calendar/providers/company_ir.py": "legacy earnings provider migration target",
    "domains/global_earnings_calendar/providers/nasdaq.py": "legacy earnings provider migration target",
    "domains/global_earnings_calendar/providers/sec.py": "legacy earnings provider migration target",
}


@dataclass(frozen=True)
class HttpSafetyFinding:
    path: str
    line: int
    column: int
    call: str
    reason: str
    allowed: bool


def _to_repo_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*_attribute_chain(node.value), node.attr)
    return ()


def _direct_http_reason(call: ast.Call) -> str | None:
    chain = _attribute_chain(call.func)
    if not chain:
        return None

    method = chain[-1]
    if chain in {("urllib", "request", "urlopen"), ("urllib2", "urlopen")}:
        return "urllib.request.urlopen must be routed through urlopen_https"
    if method == "urlopen":
        return "urlopen must be routed through urlopen_https"
    if len(chain) >= 2 and chain[0] == "requests" and method in DIRECT_HTTP_METHODS:
        return "requests direct calls must be routed through requests_get_https"
    if len(chain) >= 2 and chain[-2] in {"session", "http_session"} and method in DIRECT_HTTP_METHODS:
        return "session direct calls must be routed through requests_get_https"
    return None


def _scan_file(path: Path, *, root: Path) -> list[HttpSafetyFinding]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    repo_path = _to_repo_path(path, root)
    allow_reason = LEGACY_ALLOWED_DIRECT_HTTP_FILES.get(repo_path)
    findings: list[HttpSafetyFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        reason = _direct_http_reason(node)
        if reason is None:
            continue
        call_text = ast.get_source_segment(text, node) or ".".join(_attribute_chain(node.func))
        findings.append(
            HttpSafetyFinding(
                path=repo_path,
                line=int(getattr(node, "lineno", 0)),
                column=int(getattr(node, "col_offset", 0)),
                call=call_text.strip().splitlines()[0],
                reason=allow_reason or reason,
                allowed=allow_reason is not None,
            )
        )
    return findings


def iter_python_files(root: Path, source_roots: Iterable[str] = SOURCE_ROOTS) -> Iterable[Path]:
    for source_root in source_roots:
        directory = root / source_root
        if not directory.exists():
            continue
        yield from sorted(directory.rglob("*.py"))


def scan_direct_http(root: Path = REPO_ROOT, source_roots: Iterable[str] = SOURCE_ROOTS) -> list[HttpSafetyFinding]:
    findings: list[HttpSafetyFinding] = []
    for path in iter_python_files(root, source_roots):
        findings.extend(_scan_file(path, root=root))
    return findings


def build_report(findings: list[HttpSafetyFinding]) -> dict:
    allowed = [finding for finding in findings if finding.allowed]
    disallowed = [finding for finding in findings if not finding.allowed]
    return {
        "status": "fail" if disallowed else "ok",
        "policy": "direct external HTTP requests must go through infra.http_safety wrappers",
        "allowed_legacy_files": LEGACY_ALLOWED_DIRECT_HTTP_FILES,
        "allowed_count": len(allowed),
        "disallowed_count": len(disallowed),
        "findings": [asdict(finding) for finding in findings],
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit direct external HTTP calls against safety-wrapper policy.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--source-root", action="append", default=None, help="Source root to scan; can be repeated.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    source_roots = tuple(args.source_root or SOURCE_ROOTS)
    findings = scan_direct_http(args.root, source_roots)
    report = build_report(findings)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 1 if report["status"] != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
