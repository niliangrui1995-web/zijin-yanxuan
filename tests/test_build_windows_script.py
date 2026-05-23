from pathlib import Path


def test_build_windows_delete_guard_uses_path_boundary():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "build_windows.ps1").read_text(
        encoding="utf-8",
    )

    assert ".StartsWith($RepoRoot" not in script
    assert "$repoRootPrefix" in script
    assert "Refusing to remove repo root" in script
    assert "TrimEnd([char[]]@" in script


def test_qdarkstyle_is_removed_from_startup_packaging_and_docs():
    repo = Path(__file__).resolve().parents[1]
    checked_paths = [
        repo / "vcp_hunter_qt.pyw",
        repo / "requirements.txt",
        repo / "scripts" / "build_windows.ps1",
        repo / "README.md",
        repo / "docs" / "technical-architecture.md",
    ]

    for path in checked_paths:
        assert "qdarkstyle" not in path.read_text(encoding="utf-8").lower()

    assert "generate_global_qss" in (repo / "vcp_hunter_qt.pyw").read_text(encoding="utf-8")
