from app.services import ui_navigation_service


def test_open_codex_desktop_thread_uses_fast_appx_path(monkeypatch):
    captured = {}
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({1}), 1)

    def fake_activate(arguments):
        captured["arguments"] = arguments
        return True

    monkeypatch.setattr(ui_navigation_service.os, "name", "nt", raising=False)
    monkeypatch.setattr(ui_navigation_service, "_codex_window_snapshot", lambda: before)
    monkeypatch.setattr(ui_navigation_service, "_activate_codex_appx", fake_activate)
    monkeypatch.setattr(
        ui_navigation_service,
        "_schedule_codex_prompt_paste",
        lambda prompt, snapshot: captured.setdefault("paste", (prompt, snapshot)),
    )
    monkeypatch.setattr(
        ui_navigation_service,
        "spawn_silent_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback should not run")),
    )

    assert ui_navigation_service.open_codex_desktop_thread("codex://new?path=/tmp/demo&prompt=hello")
    assert captured == {
        "arguments": "--open-project=/tmp/demo",
        "paste": ("hello", before),
    }


def test_open_codex_desktop_thread_falls_back_to_local_launcher(monkeypatch, tmp_path):
    launcher = tmp_path / "open-codex-project.ps1"
    launcher.write_text("", encoding="utf-8")
    captured = {}

    def fake_spawn(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(ui_navigation_service.os, "name", "nt", raising=False)
    monkeypatch.setattr(ui_navigation_service, "CODEX_LOCAL_LAUNCHER", launcher)
    monkeypatch.setattr(ui_navigation_service, "_activate_codex_appx", lambda _arguments: False)
    monkeypatch.setattr(ui_navigation_service, "_powershell_executable", lambda: "powershell.exe")
    monkeypatch.setattr(ui_navigation_service, "spawn_silent_process", fake_spawn)

    assert ui_navigation_service.open_codex_desktop_thread("codex://new?path=/tmp/demo")
    assert captured["args"] == [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(launcher),
        "codex://new?path=/tmp/demo",
    ]
    assert captured["kwargs"] == {}


def test_open_codex_desktop_thread_strips_prompt_from_launcher_fallback(monkeypatch, tmp_path):
    launcher = tmp_path / "open-codex-project.ps1"
    launcher.write_text("", encoding="utf-8")
    captured = {}
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({1}), 1)

    def fake_spawn(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(ui_navigation_service.os, "name", "nt", raising=False)
    monkeypatch.setattr(ui_navigation_service, "_codex_window_snapshot", lambda: before)
    monkeypatch.setattr(ui_navigation_service, "CODEX_LOCAL_LAUNCHER", launcher)
    monkeypatch.setattr(ui_navigation_service, "_activate_codex_appx", lambda _arguments: False)
    monkeypatch.setattr(
        ui_navigation_service,
        "_schedule_codex_prompt_paste",
        lambda prompt, snapshot: captured.setdefault("paste", (prompt, snapshot)),
    )
    monkeypatch.setattr(ui_navigation_service, "_powershell_executable", lambda: "powershell.exe")
    monkeypatch.setattr(ui_navigation_service, "spawn_silent_process", fake_spawn)

    assert ui_navigation_service.open_codex_desktop_thread("codex://new?path=/tmp/demo&prompt=hello")
    assert captured["paste"] == ("hello", before)
    assert captured["args"][-1] == "codex://new?path=%2Ftmp%2Fdemo"
    assert "prompt" not in captured["args"][-1]


def test_paste_codex_prompt_waits_for_new_window_before_ctrl_v(monkeypatch):
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({1}), 1)
    calls = []

    monkeypatch.setattr(
        ui_navigation_service,
        "_copy_codex_prompt_to_clipboard",
        lambda prompt: calls.append(("copy", prompt)) or True,
    )
    monkeypatch.setattr(
        ui_navigation_service,
        "_wait_for_codex_paste_target",
        lambda snapshot: calls.append(("wait", snapshot)) or 2,
    )
    monkeypatch.setattr(
        ui_navigation_service,
        "_focus_window",
        lambda hwnd: calls.append(("focus", hwnd)) or True,
    )
    monkeypatch.setattr(ui_navigation_service.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(ui_navigation_service, "_send_ctrl_v", lambda: calls.append(("paste",)) or True)

    ui_navigation_service._paste_codex_prompt_when_target_ready("hello", before)

    assert calls == [
        ("copy", "hello"),
        ("wait", before),
        ("focus", 2),
        ("paste",),
    ]


def test_paste_codex_prompt_does_not_ctrl_v_without_new_window(monkeypatch):
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({1}), 1)
    calls = []

    monkeypatch.setattr(ui_navigation_service, "_copy_codex_prompt_to_clipboard", lambda _prompt: True)
    monkeypatch.setattr(ui_navigation_service, "_wait_for_codex_paste_target", lambda _snapshot: None)
    monkeypatch.setattr(ui_navigation_service, "_focus_window", lambda hwnd: calls.append(("focus", hwnd)) or True)
    monkeypatch.setattr(ui_navigation_service, "_send_ctrl_v", lambda: calls.append(("paste",)) or True)

    ui_navigation_service._paste_codex_prompt_when_target_ready("hello", before)

    assert calls == []


def test_select_codex_target_allows_reused_foreground_after_launch(monkeypatch):
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({10}), 99)
    after = ui_navigation_service._CodexWindowSnapshot(frozenset({10}), 10)

    monkeypatch.setattr(ui_navigation_service, "_codex_window_snapshot", lambda: after)

    assert ui_navigation_service._select_codex_paste_target(before) is None
    assert ui_navigation_service._select_codex_paste_target(before, allow_reused_foreground=True) == 10


def test_select_codex_target_rejects_reused_foreground_when_it_was_already_foreground(monkeypatch):
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({10}), 10)
    after = ui_navigation_service._CodexWindowSnapshot(frozenset({10}), 10)

    monkeypatch.setattr(ui_navigation_service, "_codex_window_snapshot", lambda: after)

    assert ui_navigation_service._select_codex_paste_target(before, allow_reused_foreground=True) is None


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
