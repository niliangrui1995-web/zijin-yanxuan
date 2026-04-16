from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET_DIRS = ("core", "ui", "vcp", "tests", "scripts")
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


def iter_target_files():
    for rel_dir in TARGET_DIRS:
        base = ROOT / rel_dir
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix.lower() in TARGET_SUFFIXES:
                yield path


def scan_text_issues(text: str) -> list[str]:
    issues: list[str] = []
    if "\x00" in text:
        issues.append("包含 NUL 字节")
    if "\ufffd" in text:
        issues.append("包含 Unicode 替换字符(可能已发生乱码)")
    return issues


def main() -> int:
    invalid_files: list[str] = []
    suspicious_files: list[tuple[str, list[str]]] = []
    for path in iter_target_files():
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
