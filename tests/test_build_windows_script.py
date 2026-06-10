from pathlib import Path


def test_build_windows_delete_guard_uses_path_boundary():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "build_windows.ps1").read_text(
        encoding="utf-8",
    )

    assert ".StartsWith($RepoRoot" not in script
    assert "$repoRootPrefix" in script
    assert "Refusing to remove repo root" in script
    assert "TrimEnd([char[]]@" in script


def test_windows_scripts_use_yanxuan_display_name():
    repo = Path(__file__).resolve().parents[1]

    for script_name in ("build_windows.ps1", "install_windows_shortcut.ps1"):
        script = (repo / "scripts" / script_name).read_text(encoding="utf-8")
        assert "0x9009" in script
        assert "0x6295" not in script


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


def test_runtime_requirements_target_python314_scipy():
    requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8")

    assert "# Python 3.14+" in requirements
    assert "scipy>=1.17.1" in requirements
    assert "python_version" not in requirements


def test_dev_shortcut_uses_silent_launcher():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "install_windows_shortcut.ps1").read_text(
        encoding="utf-8",
    )

    assert "Ensure-SilentLauncherExe" in script
    assert "TargetPath = $LauncherExe" in script
    assert 'Arguments = (\'"{0}"\' -f $RepoRoot)' in script


def test_silent_launcher_uses_project_venv_pythonw():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "launch_windows_silent.cs").read_text(
        encoding="utf-8",
    )

    assert "var pythonw = venvPythonw;" in source
    assert "File.Exists(basePythonw) ? basePythonw : venvPythonw" not in source
