from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

from app.services import ui_navigation_service as module


class _Fn:
    def __init__(self, result=1, side_effect=None):
        self.result = result
        self.side_effect = side_effect
        self.calls = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.calls.append(args)
        if self.side_effect:
            return self.side_effect(*args)
        return self.result


def test_activate_appx_rejects_com_init_and_create_failures(monkeypatch):
    class Ole:
        def __init__(self, init=0, create=0, assign=False):
            self.CoInitializeEx = _Fn(init)
            self.CoUninitialize = _Fn()

            def create_instance(*args):
                if assign:
                    args[-1]._obj.value = 123
                return create

            self.CoCreateInstance = _Fn(side_effect=create_instance)

    monkeypatch.setattr(module, "_guid_from_string", lambda _value: ctypes.c_int())
    monkeypatch.setattr(ctypes, "OleDLL", lambda _name: Ole(init=-1))
    monkeypatch.setattr(module, "_failed_hresult", lambda value: value < 0)
    assert module._activate_codex_appx("args") is False

    ole = Ole(init=0, create=-1)
    monkeypatch.setattr(ctypes, "OleDLL", lambda _name: ole)
    assert module._activate_codex_appx("args") is False
    assert ole.CoUninitialize.calls


def test_activate_appx_success_releases_manager(monkeypatch):
    released = []
    activated = []

    class Ole:
        CoInitializeEx = _Fn(0)
        CoUninitialize = _Fn()

        @staticmethod
        def CoCreateInstance(*args):
            args[-1]._obj.value = 123
            return 0

    monkeypatch.setattr(ctypes, "OleDLL", lambda _name: Ole())
    monkeypatch.setattr(module, "_guid_from_string", lambda _value: ctypes.c_int())
    monkeypatch.setattr(module, "_failed_hresult", lambda value: value < 0)
    monkeypatch.setattr(
        ctypes,
        "cast",
        lambda *_args: SimpleNamespace(contents=[None, None, "release", "activate"]),
    )

    def fake_winfunctype(*signature):
        del signature

        def bind(address):
            if address == "release":
                return lambda manager: released.append(manager.value)

            def activate(manager, app_id, arguments, options, process_id):
                activated.append((manager.value, app_id, arguments, options))
                process_id._obj.value = 77
                return 0

            return activate

        return bind

    monkeypatch.setattr(ctypes, "WINFUNCTYPE", fake_winfunctype)
    assert module._activate_codex_appx("--thread-id abc") is True
    assert activated[0][2] == "--thread-id abc"
    assert released == [123]


def _clipboard_dlls(*, alloc=11, lock=22, opened=1, set_data=33):
    kernel = SimpleNamespace(
        GlobalAlloc=_Fn(alloc),
        GlobalLock=_Fn(lock),
        GlobalUnlock=_Fn(1),
        GlobalFree=_Fn(0),
    )
    user = SimpleNamespace(
        OpenClipboard=_Fn(opened),
        EmptyClipboard=_Fn(1),
        SetClipboardData=_Fn(set_data),
        CloseClipboard=_Fn(1),
    )
    return user, kernel


@pytest.mark.parametrize(
    ("kwargs", "expected", "free_count", "closed"),
    [
        ({"alloc": 0}, False, 0, False),
        ({"lock": 0}, False, 1, False),
        ({"opened": 0}, False, 1, False),
        ({"set_data": 0}, False, 2, True),
        ({}, True, 0, True),
    ],
)
def test_windows_clipboard_resource_paths(monkeypatch, kwargs, expected, free_count, closed):
    user, kernel = _clipboard_dlls(**kwargs)
    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **_kwargs: user if name == "user32" else kernel)
    monkeypatch.setattr(ctypes, "memmove", lambda *_args: None)

    assert module._copy_text_to_windows_clipboard("hello") is expected
    assert len(kernel.GlobalFree.calls) == free_count
    assert bool(user.CloseClipboard.calls) is closed


def test_windows_clipboard_handles_api_exception(monkeypatch):
    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing")))
    assert module._copy_text_to_windows_clipboard("hello") is False


def test_windows_clipboard_snapshot_preserves_text_and_rejects_rich_content(monkeypatch):
    user, kernel = _clipboard_dlls()
    user.CountClipboardFormats = _Fn(1)
    user.IsClipboardFormatAvailable = _Fn(1)
    user.GetClipboardData = _Fn(11)
    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **_kwargs: user if name == "user32" else kernel)
    monkeypatch.setattr(ctypes, "wstring_at", lambda _pointer: "original")

    snapshot = module._capture_windows_clipboard_snapshot()

    assert snapshot == module._WindowsClipboardSnapshot(text="original")
    assert user.CloseClipboard.calls
    assert kernel.GlobalUnlock.calls == [(11,)]

    restored = []
    monkeypatch.setattr(module, "_copy_text_to_windows_clipboard", lambda text: restored.append(text) or True)
    monkeypatch.setattr(module, "_clear_windows_clipboard", lambda: restored.append("cleared") or True)

    assert module._restore_windows_clipboard_snapshot(snapshot) is True
    assert module._restore_windows_clipboard_snapshot(module._WindowsClipboardSnapshot(text=None)) is True
    assert restored == ["original", "cleared"]

    user.CountClipboardFormats = _Fn(2)
    assert module._capture_windows_clipboard_snapshot() is None


def test_focus_window_success_false_and_error(monkeypatch):
    monkeypatch.setattr(module.os, "name", "nt")
    user = SimpleNamespace(ShowWindow=_Fn(1), SetForegroundWindow=_Fn(1))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: user)
    assert module._focus_window(123) is True
    assert user.ShowWindow.calls[0][0] == 123
    user.SetForegroundWindow.result = 0
    assert module._focus_window(123) is False
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("bad")))
    assert module._focus_window(123) is False


def test_send_ctrl_v_success_and_error(monkeypatch):
    monkeypatch.setattr(module.os, "name", "nt")
    key_event = _Fn()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: SimpleNamespace(keybd_event=key_event))
    assert module._send_ctrl_v() is True
    assert [call[0] for call in key_event.calls] == [module._VK_CONTROL, module._VK_V, module._VK_V, module._VK_CONTROL]
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("bad")))
    assert module._send_ctrl_v() is False


def test_fast_open_activation_exception_and_success(monkeypatch):
    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(module, "_activate_codex_appx", lambda _args: (_ for _ in ()).throw(OSError("bad")))
    url = "codex://new?path=C%3A%5Cdemo&prompt=hello"
    assert module._try_open_codex_desktop_thread_fast(url) is False

    activated = []
    monkeypatch.setattr(module, "_activate_codex_appx", lambda args: activated.append(args) or True)
    monkeypatch.setattr(
        module,
        "_schedule_codex_prompt_paste",
        lambda *_args: (_ for _ in ()).throw(AssertionError("paste should not be scheduled")),
    )
    assert module._try_open_codex_desktop_thread_fast(url) is True
    assert activated == [url]
