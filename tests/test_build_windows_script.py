from pathlib import Path


def test_build_windows_delete_guard_uses_path_boundary():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "build_windows.ps1").read_text(
        encoding="utf-8",
    )

    assert ".StartsWith($RepoRoot" not in script
    assert "$repoRootPrefix" in script
    assert "Refusing to remove repo root" in script
    assert "TrimEnd([char[]]@" in script
