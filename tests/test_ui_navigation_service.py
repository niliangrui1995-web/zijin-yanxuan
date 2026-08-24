import base64
import gzip
import io
import re
import subprocess

import pytest

from app.services import ui_navigation_service
from infra.storage import file_integrity
from infra.storage.file_integrity import FileIntegrityError, fingerprint_bytes, fingerprint_file


def _seal_codex_launcher(monkeypatch, launcher):
    fingerprint = fingerprint_file(launcher)
    monkeypatch.setattr(
        ui_navigation_service,
        "CODEX_LOCAL_LAUNCHER_FINGERPRINT",
        fingerprint,
        raising=False,
    )


def _decoded_trusted_launcher_command(command):
    wrapper = base64.b64decode(command[-1]).decode("utf-16-le")
    encoded_values = re.findall(r"FromBase64String\('([^']+)'\)", wrapper)
    return gzip.decompress(base64.b64decode(encoded_values[0])), wrapper, encoded_values


def test_verified_file_read_bounds_untrusted_artifact_size(monkeypatch, tmp_path):
    artifact = tmp_path / "open-codex-project.ps1"
    trusted_fingerprint = fingerprint_bytes(b"trusted")
    read_sizes = []

    class GuardedReader(io.BytesIO):
        def read(self, size=-1):
            read_sizes.append(size)
            return super().read(size)

    def fake_open(path, *args, **kwargs):
        assert path == artifact
        return GuardedReader(b"untrusted artifact is much larger than the sealed launcher")

    monkeypatch.setattr(file_integrity.Path, "open", fake_open)

    with pytest.raises(FileIntegrityError, match="size mismatch"):
        file_integrity.read_verified_file_bytes(
            artifact,
            expected_size_bytes=trusted_fingerprint.size_bytes,
            expected_sha256=trusted_fingerprint.sha256,
        )

    assert read_sizes == [trusted_fingerprint.size_bytes + 1]


def test_powershell_executable_prefers_systemroot_binary(monkeypatch, tmp_path):
    powershell = tmp_path / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    powershell.parent.mkdir(parents=True)
    powershell.write_text("", encoding="utf-8")

    monkeypatch.setenv("SystemRoot", str(tmp_path))

    assert ui_navigation_service._powershell_executable() == str(powershell)


def test_powershell_executable_falls_back_when_binary_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SystemRoot", str(tmp_path))

    assert ui_navigation_service._powershell_executable() == "powershell.exe"


def test_powershell_param_block_end_ignores_quoted_and_commented_closing_parentheses():
    source = (
        "\ufeff  Param(\n"
        "    [string]$ProjectPath = \"quoted ) and `\" marker\",\n"
        "    [string]$Name = 'it''s )'\n"
        "    # ) ignored\n"
        ")\n"
        "Write-Output $ProjectPath\n"
    )

    end = ui_navigation_service._powershell_param_block_end(source)

    assert source[end] == ")"
    assert source[end + 1 :].startswith("\nWrite-Output")
    with pytest.raises(ValueError, match="must begin"):
        ui_navigation_service._powershell_param_block_end("Write-Output 'not a launcher'")


def test_codex_thread_url_helpers_parse_and_build_native_activation_route():
    assert ui_navigation_service._parse_codex_thread_url("https://example.test") is None
    assert ui_navigation_service._parse_codex_thread_url("codex://new?path=&prompt=") == ui_navigation_service._CodexThreadRequest(
        path=None,
        prompt=None,
    )

    request = ui_navigation_service._parse_codex_thread_url("codex://new?path=C%3A%5Cdemo%20app&prompt=%20hello%20")
    assert request == ui_navigation_service._CodexThreadRequest(path=r"C:\demo app", prompt=" hello ")
    assert (
        ui_navigation_service._codex_activation_arguments(request)
        == "codex://new?path=C%3A%5Cdemo+app&prompt=+hello+"
    )
    assert ui_navigation_service._codex_activation_arguments(ui_navigation_service._CodexThreadRequest(None, "x")) == "codex://new?prompt=x"
    assert (
        ui_navigation_service._codex_activation_arguments(
            ui_navigation_service._CodexThreadRequest(r"C:\demo&unsafe", 'hello&"unsafe"')
        )
        == "codex://new?path=C%3A%5Cdemo%26unsafe&prompt=hello%26%22unsafe%22"
    )
    assert ui_navigation_service._codex_thread_url_without_prompt("https://example.test?prompt=x") == "https://example.test?prompt=x"
    assert ui_navigation_service._codex_thread_url_without_prompt("codex://new?path=%2Ftmp%2Fdemo") == "codex://new?path=%2Ftmp%2Fdemo"
    assert (
        ui_navigation_service._codex_thread_url_without_prompt("codex://new?path=%2Ftmp%2Fdemo&prompt=hello&mode=&x=1#frag")
        == "codex://new?path=%2Ftmp%2Fdemo&mode=&x=1#frag"
    )

def test_windows_helper_shortcuts_on_non_windows(monkeypatch):
    monkeypatch.setattr(ui_navigation_service.os, "name", "posix", raising=False)

    assert ui_navigation_service._copy_text_to_windows_clipboard("hello") is False
    assert ui_navigation_service._list_codex_window_handles() == frozenset()
    assert ui_navigation_service._foreground_window_handle() is None
    assert ui_navigation_service._focus_window(1) is False
    assert ui_navigation_service._send_ctrl_v() is False
    assert ui_navigation_service._try_open_codex_desktop_thread_fast("codex://new?path=/tmp/demo") is False


def test_prompt_clipboard_strips_text_and_rejects_empty(monkeypatch):
    copied = []
    monkeypatch.setattr(
        ui_navigation_service,
        "_copy_text_to_windows_clipboard",
        lambda text: copied.append(text) or True,
    )

    assert ui_navigation_service._copy_codex_prompt_to_clipboard("  hello  ") is True
    assert copied == ["hello"]
    assert ui_navigation_service._copy_codex_prompt_to_clipboard("   ") is False
    assert copied == ["hello"]


def test_codex_window_title_and_selection_edges(monkeypatch):
    assert ui_navigation_service._is_codex_window_title("Codex") is True
    assert ui_navigation_service._is_codex_window_title("demo - Codex") is True
    assert ui_navigation_service._is_codex_window_title("Codex Preview") is False

    before = ui_navigation_service._CodexWindowSnapshot(frozenset({1}), 1)
    monkeypatch.setattr(
        ui_navigation_service,
        "_codex_window_snapshot",
        lambda: ui_navigation_service._CodexWindowSnapshot(frozenset({1, 3, 7}), 3),
    )
    assert ui_navigation_service._select_codex_paste_target(before) == 3

    monkeypatch.setattr(
        ui_navigation_service,
        "_codex_window_snapshot",
        lambda: ui_navigation_service._CodexWindowSnapshot(frozenset({1, 4, 9}), 1),
    )
    assert ui_navigation_service._select_codex_paste_target(before) == 9

    before_empty = ui_navigation_service._CodexWindowSnapshot(frozenset(), None)
    monkeypatch.setattr(
        ui_navigation_service,
        "_codex_window_snapshot",
        lambda: ui_navigation_service._CodexWindowSnapshot(frozenset({5}), 5),
    )
    assert ui_navigation_service._select_codex_paste_target(before_empty) == 5


def test_codex_window_recognizes_verified_chatgpt_desktop_client(monkeypatch):
    def executable_path(hwnd):
        if hwnd in {42, 44}:
            return r"C:\Program Files\WindowsApps\OpenAI.Codex_26.818.8289.0_x64__2p2nqsd0c76g0\app\ChatGPT.exe"
        if hwnd == 45:
            return None
        return r"C:\Program Files\Other\ChatGPT.exe"

    monkeypatch.setattr(
        ui_navigation_service,
        "_codex_window_executable_path",
        executable_path,
    )

    assert ui_navigation_service._is_codex_window(42, "ChatGPT") is True
    assert ui_navigation_service._is_codex_window(42, "产业链投研 - ChatGPT") is True
    assert ui_navigation_service._is_codex_window(43, "ChatGPT") is False
    assert ui_navigation_service._is_codex_window(44, "Codex") is True
    assert ui_navigation_service._is_codex_window(45, "ChatGPT") is False


def test_wait_for_codex_paste_target_allows_reused_foreground_after_delay(monkeypatch):
    clock = {"now": 0.0}
    calls = []

    monkeypatch.setattr(ui_navigation_service.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(ui_navigation_service.time, "sleep", lambda _seconds: clock.__setitem__("now", 1.3))

    def fake_select(before, *, allow_reused_foreground=False):
        calls.append((before, allow_reused_foreground))
        return 42 if allow_reused_foreground else None

    before = ui_navigation_service._CodexWindowSnapshot(frozenset({1}), 1)
    monkeypatch.setattr(ui_navigation_service, "_select_codex_paste_target", fake_select)

    assert ui_navigation_service._wait_for_codex_paste_target(before) == 42
    assert calls == [(before, False), (before, True)]


def test_wait_for_codex_paste_target_times_out(monkeypatch):
    clock = {"now": 0.0}
    sleeps = []

    monkeypatch.setattr(ui_navigation_service, "_CODEX_TARGET_WINDOW_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(ui_navigation_service.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(ui_navigation_service.time, "sleep", lambda seconds: sleeps.append(seconds) or clock.__setitem__("now", 0.3))
    monkeypatch.setattr(ui_navigation_service, "_select_codex_paste_target", lambda *_args, **_kwargs: None)

    assert ui_navigation_service._wait_for_codex_paste_target(ui_navigation_service._CodexWindowSnapshot(frozenset(), None)) is None
    assert sleeps == [ui_navigation_service._CODEX_TARGET_WINDOW_POLL_SECONDS]


def test_paste_codex_prompt_stops_when_copy_or_focus_fails(monkeypatch):
    calls = []
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({1}), 1)
    snapshot = ui_navigation_service._WindowsClipboardSnapshot(text="original")

    monkeypatch.setattr(ui_navigation_service, "_capture_windows_clipboard_snapshot", lambda: snapshot)
    monkeypatch.setattr(ui_navigation_service, "_restore_windows_clipboard_snapshot", lambda _snapshot: True)
    monkeypatch.setattr(ui_navigation_service, "_copy_codex_prompt_to_clipboard", lambda _prompt: False)
    monkeypatch.setattr(ui_navigation_service, "_wait_for_codex_paste_target", lambda _snapshot: calls.append("wait") or 2)
    ui_navigation_service._paste_codex_prompt_when_target_ready("hello", before)
    assert calls == []

    monkeypatch.setattr(ui_navigation_service, "_copy_codex_prompt_to_clipboard", lambda _prompt: True)
    monkeypatch.setattr(ui_navigation_service, "_wait_for_codex_paste_target", lambda _snapshot: 2)
    monkeypatch.setattr(ui_navigation_service, "_focus_window", lambda hwnd: calls.append(("focus", hwnd)) or False)
    monkeypatch.setattr(ui_navigation_service, "_send_ctrl_v", lambda: calls.append("paste") or True)
    ui_navigation_service._paste_codex_prompt_when_target_ready("hello", before)
    assert calls == [("focus", 2)]


def test_paste_codex_prompt_restores_clipboard_when_target_is_missing(monkeypatch):
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({1}), 1)
    snapshot = object()
    calls = []

    monkeypatch.setattr(
        ui_navigation_service,
        "_capture_windows_clipboard_snapshot",
        lambda: calls.append("capture") or snapshot,
        raising=False,
    )
    monkeypatch.setattr(
        ui_navigation_service,
        "_restore_windows_clipboard_snapshot",
        lambda value: calls.append(("restore", value)),
        raising=False,
    )
    monkeypatch.setattr(
        ui_navigation_service,
        "_copy_codex_prompt_to_clipboard",
        lambda prompt: calls.append(("copy", prompt)) or True,
    )
    monkeypatch.setattr(
        ui_navigation_service,
        "_wait_for_codex_paste_target",
        lambda value: calls.append(("wait", value)) or None,
    )

    ui_navigation_service._paste_codex_prompt_when_target_ready("hello", before)

    assert calls == ["capture", ("copy", "hello"), ("wait", before), ("restore", snapshot)]


def test_paste_codex_prompt_rechecks_foreground_before_ctrl_v_and_restores_clipboard(monkeypatch):
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({1}), 1)
    snapshot = object()
    calls = []

    monkeypatch.setattr(ui_navigation_service, "_capture_windows_clipboard_snapshot", lambda: snapshot, raising=False)
    monkeypatch.setattr(
        ui_navigation_service,
        "_restore_windows_clipboard_snapshot",
        lambda value: calls.append(("restore", value)),
        raising=False,
    )
    monkeypatch.setattr(
        ui_navigation_service,
        "_copy_codex_prompt_to_clipboard",
        lambda prompt: calls.append(("copy", prompt)) or True,
    )
    monkeypatch.setattr(ui_navigation_service, "_wait_for_codex_paste_target", lambda _before: 2)
    monkeypatch.setattr(ui_navigation_service, "_focus_window", lambda hwnd: calls.append(("focus", hwnd)) or True)
    monkeypatch.setattr(ui_navigation_service, "_foreground_window_handle", lambda: 3)
    monkeypatch.setattr(ui_navigation_service.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(ui_navigation_service, "_send_ctrl_v", lambda: calls.append("paste") or True)

    ui_navigation_service._paste_codex_prompt_when_target_ready("hello", before)

    assert calls == [("copy", "hello"), ("focus", 2), ("restore", snapshot)]


def test_paste_codex_prompt_keeps_current_clipboard_when_sequence_is_unknown(monkeypatch):
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({1}), 1)
    snapshot = object()
    restored = []

    monkeypatch.setattr(ui_navigation_service, "_capture_windows_clipboard_snapshot", lambda: snapshot)
    monkeypatch.setattr(ui_navigation_service, "_copy_codex_prompt_to_clipboard", lambda _prompt: True)
    monkeypatch.setattr(ui_navigation_service, "_clipboard_sequence_number", lambda: None)
    monkeypatch.setattr(ui_navigation_service, "_wait_for_codex_paste_target", lambda _before: None)
    monkeypatch.setattr(
        ui_navigation_service,
        "_restore_windows_clipboard_snapshot",
        lambda value: restored.append(value),
    )

    ui_navigation_service._paste_codex_prompt_when_target_ready("hello", before)

    assert restored == []


def test_schedule_codex_prompt_paste_starts_daemon_thread(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, **kwargs):
            started.append(kwargs)

        def start(self):
            started.append("started")

    before = ui_navigation_service._CodexWindowSnapshot(frozenset({1}), 1)
    monkeypatch.setattr(ui_navigation_service.threading, "Thread", FakeThread)

    ui_navigation_service._schedule_codex_prompt_paste("   ", before)
    assert started == []

    ui_navigation_service._schedule_codex_prompt_paste("  hello  ", before)
    assert started[0]["target"] == ui_navigation_service._paste_codex_prompt_when_target_ready
    assert started[0]["args"] == ("hello", before)
    assert started[0]["name"] == "CodexPromptPaste"
    assert started[0]["daemon"] is True
    assert started[1] == "started"


def test_try_open_codex_desktop_thread_fast_rejects_invalid_or_failed_activation(monkeypatch):
    monkeypatch.setattr(ui_navigation_service.os, "name", "nt", raising=False)
    monkeypatch.setattr(
        ui_navigation_service,
        "_codex_window_snapshot",
        lambda: ui_navigation_service._CodexWindowSnapshot(frozenset(), None),
    )
    monkeypatch.setattr(
        ui_navigation_service,
        "_schedule_codex_prompt_paste",
        lambda *_args: (_ for _ in ()).throw(AssertionError("paste should not be scheduled")),
    )

    assert ui_navigation_service._try_open_codex_desktop_thread_fast("https://example.test") is False

    monkeypatch.setattr(ui_navigation_service, "_activate_codex_appx", lambda _args: False)
    assert ui_navigation_service._try_open_codex_desktop_thread_fast("codex://new?path=/tmp/demo") is False

    monkeypatch.setattr(
        ui_navigation_service,
        "_activate_codex_appx",
        lambda _args: (_ for _ in ()).throw(OSError("activation failed")),
    )
    assert ui_navigation_service._try_open_codex_desktop_thread_fast("codex://new?path=/tmp/demo") is False


def test_failed_hresult_and_guid_from_string():
    guid = ui_navigation_service._guid_from_string("45BA127D-10A8-46EA-8AB7-56EA9078943C")

    assert guid.Data1 == 0x45BA127D
    assert guid.Data2 == 0x10A8
    assert guid.Data3 == 0x46EA
    assert ui_navigation_service._failed_hresult(-1) is True
    assert ui_navigation_service._failed_hresult(0) is False


def test_open_codex_desktop_thread_uses_fast_appx_path(monkeypatch):
    captured = {}

    def fake_activate(arguments):
        captured["arguments"] = arguments
        return True

    monkeypatch.setattr(ui_navigation_service.os, "name", "nt", raising=False)
    monkeypatch.setattr(
        ui_navigation_service,
        "_codex_window_snapshot",
        lambda: (_ for _ in ()).throw(AssertionError("clipboard fallback should not run")),
    )
    monkeypatch.setattr(ui_navigation_service, "_activate_codex_appx", fake_activate)
    monkeypatch.setattr(
        ui_navigation_service,
        "_schedule_codex_prompt_paste",
        lambda *_args: (_ for _ in ()).throw(AssertionError("paste should not be scheduled")),
    )
    monkeypatch.setattr(
        ui_navigation_service,
        "spawn_silent_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback should not run")),
    )

    assert ui_navigation_service.open_codex_desktop_thread("codex://new?path=/tmp/demo&prompt=hello")
    assert captured == {
        "arguments": "codex://new?path=%2Ftmp%2Fdemo&prompt=hello",
    }


def test_open_codex_desktop_thread_falls_back_to_local_launcher(monkeypatch, tmp_path):
    launcher = tmp_path / "open-codex-project.ps1"
    launcher.write_text("param([string]$ProjectPath)\nWrite-Output 'trusted'\n", encoding="utf-8")
    captured = {}

    def fake_spawn(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(ui_navigation_service, "CODEX_LOCAL_LAUNCHER", launcher)
    monkeypatch.setattr(ui_navigation_service, "_try_open_codex_desktop_thread_fast", lambda _url: False)
    monkeypatch.setattr(ui_navigation_service, "_powershell_executable", lambda: "powershell.exe")
    monkeypatch.setattr(ui_navigation_service, "spawn_silent_process", fake_spawn)
    _seal_codex_launcher(monkeypatch, launcher)

    assert ui_navigation_service.open_codex_desktop_thread("codex://new?path=/tmp/demo")
    assert captured["args"][:3] == [
        "powershell.exe",
        "-NoProfile",
        "-EncodedCommand",
    ]
    assert "-ExecutionPolicy" not in captured["args"]
    assert "-File" not in captured["args"]
    source, wrapper, encoded_values = _decoded_trusted_launcher_command(captured["args"])
    assert source == launcher.read_bytes()
    assert base64.b64decode(encoded_values[-1]).decode("utf-8") == "codex://new?path=/tmp/demo"
    assert "$__codexSource = $__codexSource.Insert(" in wrapper
    assert len(subprocess.list2cmdline(captured["args"])) < ui_navigation_service._WINDOWS_MAX_COMMAND_LINE_CHARACTERS
    assert captured["kwargs"] == {}


def test_open_codex_desktop_thread_rejects_tampered_launcher_without_spawning(monkeypatch, tmp_path):
    launcher = tmp_path / "open-codex-project.ps1"
    launcher.write_text("param([string]$ProjectPath)\nWrite-Output 'allow'\n", encoding="utf-8")
    _seal_codex_launcher(monkeypatch, launcher)
    launcher.write_text("Write-Output 'block'\n", encoding="utf-8")
    before = ui_navigation_service._CodexWindowSnapshot(frozenset(), None)
    spawned = []
    scheduled = []

    monkeypatch.setattr(ui_navigation_service, "_codex_window_snapshot", lambda: before)
    monkeypatch.setattr(ui_navigation_service, "spawn_silent_process", lambda args: spawned.append(args))
    monkeypatch.setattr(
        ui_navigation_service,
        "_schedule_codex_prompt_paste",
        lambda prompt, snapshot: scheduled.append((prompt, snapshot)),
    )

    assert not ui_navigation_service.open_codex_desktop_thread(
        "codex://new?path=/tmp/demo&prompt=hello",
        launcher=launcher,
    )
    assert spawned == []
    assert scheduled == []


def test_open_codex_desktop_thread_uses_verified_source_after_launcher_replacement(monkeypatch, tmp_path):
    launcher = tmp_path / "open-codex-project.ps1"
    trusted_source = b"param([string]$ProjectPath)\nWrite-Output 'trusted'\n"
    launcher.write_bytes(trusted_source)
    trusted_fingerprint = fingerprint_file(launcher)
    _seal_codex_launcher(monkeypatch, launcher)
    captured = {}

    def fake_spawn(args, **kwargs):
        launcher.write_text("param([string]$ProjectPath)\nWrite-Output 'replacement'\n", encoding="utf-8")
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(ui_navigation_service, "_try_open_codex_desktop_thread_fast", lambda _url: False)
    monkeypatch.setattr(ui_navigation_service, "_powershell_executable", lambda: "powershell.exe")
    monkeypatch.setattr(ui_navigation_service, "spawn_silent_process", fake_spawn)

    assert ui_navigation_service.open_codex_desktop_thread("codex://new?path=/tmp/demo", launcher=launcher)

    source, wrapper, encoded_values = _decoded_trusted_launcher_command(captured["args"])
    assert source == trusted_source
    assert source != launcher.read_bytes()
    assert str(launcher) not in captured["args"]
    assert "codex://new?path=/tmp/demo" not in captured["args"]
    assert "-File" not in captured["args"]
    assert "-ExecutionPolicy" not in captured["args"]
    assert str(len(trusted_source)) in wrapper
    assert trusted_fingerprint.sha256 in wrapper
    assert fingerprint_file(launcher).sha256 not in wrapper
    assert base64.b64decode(encoded_values[-1]).decode("utf-8") == "codex://new?path=/tmp/demo"


@pytest.mark.skipif(ui_navigation_service.os.name != "nt", reason="requires Windows PowerShell")
def test_trusted_launcher_command_preserves_script_context_and_argument(monkeypatch, tmp_path):
    launcher = tmp_path / "open-codex-project.ps1"
    output_path = tmp_path / "launcher-context.txt"
    launcher.write_text(
        "param([string]$ProjectPath)\n"
        "[System.IO.File]::WriteAllText($ProjectPath, \"$PSScriptRoot|$PSCommandPath\")\n",
        encoding="utf-8",
    )
    source = launcher.read_bytes()
    fingerprint = fingerprint_file(launcher)
    monkeypatch.setattr(ui_navigation_service, "CODEX_LOCAL_LAUNCHER_FINGERPRINT", fingerprint)
    command = ui_navigation_service._build_trusted_codex_launcher_command(
        ui_navigation_service._powershell_executable(),
        launcher,
        source,
        str(output_path),
    )

    completed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)

    assert completed.returncode == 0, completed.stderr
    assert output_path.read_text(encoding="utf-8") == f"{launcher.parent}|{launcher}"


def test_open_codex_desktop_thread_strips_prompt_from_launcher_fallback(monkeypatch, tmp_path):
    launcher = tmp_path / "open-codex-project.ps1"
    launcher.write_text("param([string]$ProjectPath)\nWrite-Output 'trusted'\n", encoding="utf-8")
    captured = {}
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({1}), 1)

    def fake_spawn(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(ui_navigation_service, "_codex_window_snapshot", lambda: before)
    monkeypatch.setattr(ui_navigation_service, "CODEX_LOCAL_LAUNCHER", launcher)
    monkeypatch.setattr(ui_navigation_service, "_try_open_codex_desktop_thread_fast", lambda _url: False)
    monkeypatch.setattr(
        ui_navigation_service,
        "_schedule_codex_prompt_paste",
        lambda prompt, snapshot: captured.setdefault("paste", (prompt, snapshot)),
    )
    monkeypatch.setattr(ui_navigation_service, "_powershell_executable", lambda: "powershell.exe")
    monkeypatch.setattr(ui_navigation_service, "spawn_silent_process", fake_spawn)
    _seal_codex_launcher(monkeypatch, launcher)

    assert ui_navigation_service.open_codex_desktop_thread("codex://new?path=/tmp/demo&prompt=hello")
    assert captured["paste"] == ("hello", before)
    _source, _wrapper, encoded_values = _decoded_trusted_launcher_command(captured["args"])
    assert base64.b64decode(encoded_values[-1]).decode("utf-8") == "codex://new?path=%2Ftmp%2Fdemo"
    assert "prompt" not in base64.b64decode(encoded_values[-1]).decode("utf-8")


def test_paste_codex_prompt_waits_for_new_window_before_ctrl_v(monkeypatch):
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({1}), 1)
    calls = []
    snapshot = ui_navigation_service._WindowsClipboardSnapshot(text="original")

    monkeypatch.setattr(ui_navigation_service, "_capture_windows_clipboard_snapshot", lambda: snapshot)
    monkeypatch.setattr(
        ui_navigation_service,
        "_restore_windows_clipboard_snapshot",
        lambda value: calls.append(("restore", value)) or True,
    )
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
    monkeypatch.setattr(ui_navigation_service, "_foreground_window_handle", lambda: 2)
    monkeypatch.setattr(ui_navigation_service, "_is_codex_window_handle", lambda hwnd: hwnd == 2)
    monkeypatch.setattr(ui_navigation_service, "_send_ctrl_v", lambda: calls.append(("paste",)) or True)

    ui_navigation_service._paste_codex_prompt_when_target_ready("hello", before)

    assert calls == [
        ("copy", "hello"),
        ("wait", before),
        ("focus", 2),
        ("paste",),
        ("restore", snapshot),
    ]


def test_paste_codex_prompt_does_not_ctrl_v_when_target_identity_changes(monkeypatch):
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({1}), 1)
    snapshot = ui_navigation_service._WindowsClipboardSnapshot(text="original")
    calls = []

    monkeypatch.setattr(ui_navigation_service, "_capture_windows_clipboard_snapshot", lambda: snapshot)
    monkeypatch.setattr(ui_navigation_service, "_restore_windows_clipboard_snapshot", lambda value: calls.append(("restore", value)) or True)
    monkeypatch.setattr(ui_navigation_service, "_copy_codex_prompt_to_clipboard", lambda _prompt: True)
    monkeypatch.setattr(ui_navigation_service, "_clipboard_sequence_number", lambda: 1)
    monkeypatch.setattr(ui_navigation_service, "_wait_for_codex_paste_target", lambda _snapshot: 2)
    monkeypatch.setattr(ui_navigation_service, "_focus_window", lambda _hwnd: True)
    monkeypatch.setattr(ui_navigation_service.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(ui_navigation_service, "_foreground_window_handle", lambda: 2)
    monkeypatch.setattr(ui_navigation_service, "_is_codex_window_handle", lambda _hwnd: False)
    monkeypatch.setattr(ui_navigation_service, "_send_ctrl_v", lambda: calls.append(("paste",)) or True)

    ui_navigation_service._paste_codex_prompt_when_target_ready("hello", before)

    assert ("paste",) not in calls
    assert calls == [("restore", snapshot)]


def test_paste_codex_prompt_does_not_ctrl_v_after_clipboard_changes(monkeypatch):
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({1}), 1)
    snapshot = ui_navigation_service._WindowsClipboardSnapshot(text="original")
    calls = []
    clipboard_sequences = iter((1, 2, 2))

    monkeypatch.setattr(ui_navigation_service, "_capture_windows_clipboard_snapshot", lambda: snapshot)
    monkeypatch.setattr(ui_navigation_service, "_restore_windows_clipboard_snapshot", lambda value: calls.append(("restore", value)) or True)
    monkeypatch.setattr(ui_navigation_service, "_copy_codex_prompt_to_clipboard", lambda _prompt: True)
    monkeypatch.setattr(ui_navigation_service, "_clipboard_sequence_number", lambda: next(clipboard_sequences))
    monkeypatch.setattr(ui_navigation_service, "_wait_for_codex_paste_target", lambda _snapshot: 2)
    monkeypatch.setattr(ui_navigation_service, "_focus_window", lambda _hwnd: True)
    monkeypatch.setattr(ui_navigation_service.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(ui_navigation_service, "_foreground_window_handle", lambda: 2)
    monkeypatch.setattr(ui_navigation_service, "_is_codex_window_handle", lambda _hwnd: True)
    monkeypatch.setattr(ui_navigation_service, "_send_ctrl_v", lambda: calls.append(("paste",)) or True)

    ui_navigation_service._paste_codex_prompt_when_target_ready("hello", before)

    assert ("paste",) not in calls
    assert calls == []


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


def test_select_codex_target_reuses_single_verified_window_without_foreground_change(monkeypatch):
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({10}), 99)
    after = ui_navigation_service._CodexWindowSnapshot(frozenset({10}), 99)

    monkeypatch.setattr(ui_navigation_service, "_codex_window_snapshot", lambda: after)

    assert ui_navigation_service._select_codex_paste_target(before) is None
    assert ui_navigation_service._select_codex_paste_target(before, allow_reused_foreground=True) == 10


def test_select_codex_target_rejects_ambiguous_reused_windows_without_foreground_change(monkeypatch):
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({10, 11}), 99)
    after = ui_navigation_service._CodexWindowSnapshot(frozenset({10, 11}), 99)

    monkeypatch.setattr(ui_navigation_service, "_codex_window_snapshot", lambda: after)

    assert ui_navigation_service._select_codex_paste_target(before, allow_reused_foreground=True) is None


def test_select_codex_target_rejects_reused_foreground_when_it_was_already_foreground(monkeypatch):
    before = ui_navigation_service._CodexWindowSnapshot(frozenset({10}), 10)
    after = ui_navigation_service._CodexWindowSnapshot(frozenset({10}), 10)

    monkeypatch.setattr(ui_navigation_service, "_codex_window_snapshot", lambda: after)

    assert ui_navigation_service._select_codex_paste_target(before, allow_reused_foreground=True) is None


def test_open_codex_desktop_thread_uses_local_launcher(monkeypatch, tmp_path):
    launcher = tmp_path / "open-codex-project.ps1"
    launcher.write_text("param([string]$ProjectPath)\nWrite-Output 'trusted'\n", encoding="utf-8")
    captured = {}

    def fake_spawn(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(ui_navigation_service, "spawn_silent_process", fake_spawn)
    _seal_codex_launcher(monkeypatch, launcher)

    assert ui_navigation_service.open_codex_desktop_thread("codex://new?path=/tmp/demo", launcher=launcher)
    source, _wrapper, encoded_values = _decoded_trusted_launcher_command(captured["args"])
    assert source == launcher.read_bytes()
    assert base64.b64decode(encoded_values[-1]).decode("utf-8") == "codex://new?path=/tmp/demo"
    assert captured["kwargs"] == {}


def test_open_codex_desktop_thread_rejects_missing_launcher_without_spawning(monkeypatch, tmp_path):
    spawned = []
    scheduled = []
    missing_launcher = tmp_path / "missing.ps1"
    before = ui_navigation_service._CodexWindowSnapshot(frozenset(), None)

    monkeypatch.setattr(ui_navigation_service, "CODEX_LOCAL_LAUNCHER", missing_launcher)
    monkeypatch.setattr(ui_navigation_service, "_try_open_codex_desktop_thread_fast", lambda _url: False)
    monkeypatch.setattr(ui_navigation_service, "_codex_window_snapshot", lambda: before)
    monkeypatch.setattr(ui_navigation_service, "spawn_silent_process", lambda args: spawned.append(args))
    monkeypatch.setattr(
        ui_navigation_service,
        "_schedule_codex_prompt_paste",
        lambda prompt, snapshot: scheduled.append((prompt, snapshot)),
    )

    assert not ui_navigation_service.open_codex_desktop_thread(
        "codex://new?path=/tmp/demo&prompt=hello",
    )
    assert spawned == []
    assert scheduled == []


def test_open_codex_desktop_thread_returns_false_on_spawn_error(monkeypatch, tmp_path):
    launcher = tmp_path / "open-codex-project.ps1"
    launcher.write_text("param([string]$ProjectPath)\nWrite-Output 'trusted'\n", encoding="utf-8")

    def fake_spawn(_args, **_kwargs):
        raise OSError("blocked")

    monkeypatch.setattr(ui_navigation_service, "spawn_silent_process", fake_spawn)
    _seal_codex_launcher(monkeypatch, launcher)

    assert not ui_navigation_service.open_codex_desktop_thread("codex://new?path=/tmp/demo", launcher=launcher)


def test_open_codex_desktop_thread_keeps_non_codex_url_on_launcher_fallback(monkeypatch, tmp_path):
    launcher = tmp_path / "open-codex-project.ps1"
    launcher.write_text("param([string]$ProjectPath)\nWrite-Output 'trusted'\n", encoding="utf-8")
    captured = {}

    def fake_spawn(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    monkeypatch.setattr(ui_navigation_service, "spawn_silent_process", fake_spawn)
    monkeypatch.setattr(
        ui_navigation_service,
        "_schedule_codex_prompt_paste",
        lambda *_args: (_ for _ in ()).throw(AssertionError("paste should not be scheduled")),
    )
    _seal_codex_launcher(monkeypatch, launcher)

    assert ui_navigation_service.open_codex_desktop_thread("https://example.test?prompt=hello", launcher=launcher)
    _source, _wrapper, encoded_values = _decoded_trusted_launcher_command(captured["args"])
    assert base64.b64decode(encoded_values[-1]).decode("utf-8") == "https://example.test?prompt=hello"
    assert captured["kwargs"] == {}
