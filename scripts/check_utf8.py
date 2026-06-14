from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGETS = (
    "core",
    "ui",
    "vcp",
    "tests",
    "scripts",
    "app",
    "infra",
    "domains",
    "earnings",
    "docs",
    ".github",
    "README.md",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    ".pre-commit-config.yaml",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
)
TARGET_SUFFIXES = {
    ".py",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".txt",
    ".qss",
}
TARGET_NAMES = {
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
}
MOJIBAKE_TOKENS = (
    "\u934f",
    "\u9428",
    "\u7459",
    "\u95ab",
    "\u7ef1",
    "\u942e",
    "\u951b",
    "\u9286",
    "\u5b2b",
    "\u701b",
    "\u20ac",
)
MOJIBAKE_WINDOW_CHARS = 160
MOJIBAKE_TOKEN_THRESHOLD = 3
MOJIBAKE_ALLOWLIST_SNIPPETS = ('LEGACY_MOJIBAKE_CODE_KEY = "\\u6d60\\uff47\\u721c"',)


def _should_scan_file(path: Path) -> bool:
    return path.suffix.lower() in TARGET_SUFFIXES or path.name in TARGET_NAMES


def _iter_files_under(base: Path):
    if base.is_file():
        if _should_scan_file(base):
            yield base
        return

    if not base.exists() or not base.is_dir():
        return

    for path in base.rglob("*"):
        if path.is_file() and _should_scan_file(path):
            yield path


def iter_target_files(targets: list[str] | None = None):
    candidate_targets = targets or list(DEFAULT_TARGETS)
    seen: set[Path] = set()
    for raw_target in candidate_targets:
        base = (ROOT / raw_target).resolve() if not Path(raw_target).is_absolute() else Path(raw_target).resolve()
        for path in _iter_files_under(base):
            if path not in seen:
                seen.add(path)
                yield path


def _strip_mojibake_allowlist(text: str) -> str:
    scanned = text
    for snippet in MOJIBAKE_ALLOWLIST_SNIPPETS:
        scanned = scanned.replace(snippet, "")
    return scanned


def _has_suspicious_mojibake(text: str) -> bool:
    scanned = _strip_mojibake_allowlist(text)
    matches: list[tuple[int, str]] = []
    for token in MOJIBAKE_TOKENS:
        start = 0
        while True:
            index = scanned.find(token, start)
            if index < 0:
                break
            matches.append((index, token))
            start = index + len(token)

    if len(matches) < MOJIBAKE_TOKEN_THRESHOLD:
        return False

    matches.sort()
    for match_index, (start, _token) in enumerate(matches):
        window_end = start + MOJIBAKE_WINDOW_CHARS
        window_tokens = {token for index, token in matches[match_index:] if index <= window_end}
        if len(window_tokens) >= MOJIBAKE_TOKEN_THRESHOLD:
            return True
    return False


def scan_text_issues(text: str) -> list[str]:
    issues: list[str] = []
    if "\x00" in text:
        issues.append("包含 NUL 字节")
    if "\ufffd" in text:
        issues.append("包含 Unicode 替换字符(可能已发生乱码)")
    if _has_suspicious_mojibake(text):
        issues.append("包含疑似 mojibake 文本(合法 UTF-8 但像是错误解码后的中文)")
    return issues


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError, OSError, ValueError:
        return


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Scan source files for UTF-8 and text corruption issues.")
    parser.add_argument("paths", nargs="*", help="Optional files or directories to scan, relative to repo root.")
    args = parser.parse_args(argv)

    invalid_files: list[str] = []
    suspicious_files: list[tuple[str, list[str]]] = []
    for path in iter_target_files(args.paths):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            invalid_files.append(str(path.relative_to(ROOT)))
            continue

        issues = scan_text_issues(text)
        if issues:
            suspicious_files.append((str(path.relative_to(ROOT)), issues))

    if invalid_files:
        print("以下文件不是 UTF-8 编码：")
        for file_name in invalid_files:
            print(f" - {file_name}")
        return 1

    if suspicious_files:
        print("以下文件疑似存在编码/文本异常：")
        for file_name, issues in suspicious_files:
            print(f" - {file_name}: {'; '.join(issues)}")
        return 1

    print("UTF-8 检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
