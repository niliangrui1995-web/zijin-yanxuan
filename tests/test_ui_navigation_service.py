from app.services import ui_navigation_service


def test_open_codex_desktop_thread_uses_local_launcher(monkeypatch, tmp_path):
    launcher = tmp_path / "open-codex-project.ps1"
    launcher.write_text("", encoding="utf-8")
    captured = {}

    def fake_spawn(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(ui_navigation_service, "spawn_silent_process", fake_spawn)

    assert ui_navigation_service.open_codex_desktop_thread("codex://new?path=/tmp/demo", launcher=launcher)
    assert captured["args"][-2:] == [str(launcher), "codex://new?path=/tmp/demo"]
    assert captured["kwargs"] == {}


def test_open_codex_desktop_thread_returns_false_when_launcher_missing(tmp_path):
    assert not ui_navigation_service.open_codex_desktop_thread(
        "codex://new?path=/tmp/demo",
        launcher=tmp_path / "missing.ps1",
    )


def test_open_codex_desktop_thread_returns_false_on_spawn_error(monkeypatch, tmp_path):
    launcher = tmp_path / "open-codex-project.ps1"
    launcher.write_text("", encoding="utf-8")

    def fake_spawn(_args, **_kwargs):
        raise OSError("blocked")

    monkeypatch.setattr(ui_navigation_service, "spawn_silent_process", fake_spawn)

    assert not ui_navigation_service.open_codex_desktop_thread("codex://new?path=/tmp/demo", launcher=launcher)
