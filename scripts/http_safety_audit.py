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
    "earnings",
    "infra",
    "scripts",
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


def _request_import_aliases(tree: ast.AST) -> tuple[set[str], set[str], set[str], set[str], set[str]]:
    request_method_names: set[str] = set()
    request_session_factory_names: set[str] = set()
    curl_method_names: set[str] = set()
    curl_session_factory_names: set[str] = set()
    curl_request_module_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "curl_cffi.requests":
                    curl_request_module_names.add(alias.asname or alias.name)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module == "curl_cffi":
            for alias in node.names:
                if alias.name == "requests":
                    curl_request_module_names.add(alias.asname or alias.name)
            continue
        if node.module == "curl_cffi.requests":
            for alias in node.names:
                local_name = alias.asname or alias.name
                if alias.name in DIRECT_HTTP_METHODS:
                    curl_method_names.add(local_name)
                if alias.name == "Session":
                    curl_session_factory_names.add(local_name)
            continue
        if node.module != "requests":
            continue
        for alias in node.names:
            local_name = alias.asname or alias.name
            if alias.name in DIRECT_HTTP_METHODS:
                request_method_names.add(local_name)
            if alias.name == "Session":
                request_session_factory_names.add(local_name)
    return (
        request_method_names,
        request_session_factory_names,
        curl_method_names,
        curl_session_factory_names,
        curl_request_module_names,
    )


def _is_requests_session_factory(node: ast.AST, session_factory_names: set[str]) -> bool:
    chain = _attribute_chain(node)
    return chain in {("requests", "Session"), ("requests", "sessions", "Session")} or (
        isinstance(node, ast.Name) and node.id in session_factory_names
    )


def _is_curl_session_factory(
    node: ast.AST,
    session_factory_names: set[str],
    module_names: set[str],
) -> bool:
    chain = _attribute_chain(node)
    return (
        chain == ("curl_cffi", "requests", "Session")
        or (len(chain) == 2 and chain[0] in module_names and chain[1] == "Session")
        or (isinstance(node, ast.Name) and node.id in session_factory_names)
    )


def _direct_http_reason(
    call: ast.Call,
    *,
    request_method_names: set[str] | None = None,
    session_factory_names: set[str] | None = None,
    curl_method_names: set[str] | None = None,
    curl_session_factory_names: set[str] | None = None,
    curl_request_module_names: set[str] | None = None,
) -> str | None:
    request_method_names = request_method_names or set()
    session_factory_names = session_factory_names or set()
    curl_method_names = curl_method_names or set()
    curl_session_factory_names = curl_session_factory_names or set()
    curl_request_module_names = curl_request_module_names or set()
    if isinstance(call.func, ast.Name) and call.func.id in request_method_names:
        return "requests imported direct calls must be routed through requests_get_https"
    if isinstance(call.func, ast.Name) and call.func.id in curl_method_names:
        return "curl_cffi imported direct calls must be routed through requests_get_https or an approved session helper"

    if (
        isinstance(call.func, ast.Attribute)
        and call.func.attr in DIRECT_HTTP_METHODS
        and isinstance(call.func.value, ast.Call)
        and _is_requests_session_factory(call.func.value.func, session_factory_names)
    ):
        return "requests Session direct calls must be routed through requests_get_https"
    if (
        isinstance(call.func, ast.Attribute)
        and call.func.attr in DIRECT_HTTP_METHODS
        and isinstance(call.func.value, ast.Call)
        and _is_curl_session_factory(call.func.value.func, curl_session_factory_names, curl_request_module_names)
    ):
        return "curl_cffi Session direct calls must be routed through requests_get_https or an approved session helper"

    chain = _attribute_chain(call.func)
    if not chain:
        return None

    method = chain[-1]
    if (
        method in DIRECT_HTTP_METHODS
        and (
            chain[:2] == ("curl_cffi", "requests")
            or (len(chain) >= 2 and chain[0] in curl_request_module_names)
        )
    ):
        return "curl_cffi direct calls must be routed through requests_get_https or an approved session helper"
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
    (
        request_method_names,
        session_factory_names,
        curl_method_names,
        curl_session_factory_names,
        curl_request_module_names,
    ) = _request_import_aliases(tree)
    findings: list[HttpSafetyFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        reason = _direct_http_reason(
            node,
            request_method_names=request_method_names,
            session_factory_names=session_factory_names,
            curl_method_names=curl_method_names,
            curl_session_factory_names=curl_session_factory_names,
            curl_request_module_names=curl_request_module_names,
        )
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
