# -*- coding: utf-8 -*-
"""UI-facing external navigation entrypoints."""

from __future__ import annotations

import base64
import gzip
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

from infra.navigation import ExternalTerminalNavigator
from infra.storage.file_integrity import FileFingerprint, FileIntegrityError, read_verified_file_bytes
from infra.tasks.process_runner import spawn_silent_process

CODEX_LOCAL_LAUNCHER = Path.home() / ".codex" / "local-tools" / "open-codex-project.ps1"
# Launcher upgrades must be reviewed and resealed; mismatches fail closed.
CODEX_LOCAL_LAUNCHER_FINGERPRINT = FileFingerprint(
    size_bytes=11420,
    sha256="8cff3a69709f0c14be9391c268c83b134831be68cd65681b0df0db6874c31b88",
)
CODEX_APP_USER_MODEL_ID = "OpenAI.Codex_2p2nqsd0c76g0!App"
_APP_ACTIVATION_MANAGER_CLSID = "45BA127D-10A8-46EA-8AB7-56EA9078943C"
_IAPPLICATION_ACTIVATION_MANAGER_IID = "2E941141-7F97-4756-BA1D-9DECDE894A3D"
_CLSCTX_ALL = 0x17
_COINIT_APARTMENTTHREADED = 0x2
_RPC_E_CHANGED_MODE = -2147417850
_GMEM_MOVEABLE = 0x0002
_CF_UNICODETEXT = 13
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_KEYEVENTF_KEYUP = 0x0002
_SW_RESTORE = 9
_VK_CONTROL = 0x11
_VK_V = 0x56
_CODEX_TARGET_WINDOW_TIMEOUT_SECONDS = 6.0
_CODEX_TARGET_WINDOW_POLL_SECONDS = 0.12
_CODEX_TARGET_WINDOW_READY_DELAY_SECONDS = 0.45
_CODEX_REUSED_WINDOW_READY_SECONDS = 1.2
_WINDOWS_MAX_COMMAND_LINE_CHARACTERS = 32767
_TRUSTED_LAUNCHER_WRAPPER_TEMPLATE = """$__codexCompressed = [System.Convert]::FromBase64String('%(compressed_source)s')
$__codexMemory = [System.IO.MemoryStream]::new($__codexCompressed, $false)
$__codexGzip = [System.IO.Compression.GZipStream]::new(
    $__codexMemory, [System.IO.Compression.CompressionMode]::Decompress, $false)
$__codexSourceMemory = [System.IO.MemoryStream]::new()
try {
    $__codexGzip.CopyTo($__codexSourceMemory)
    $__codexSourceBytes = $__codexSourceMemory.ToArray()
}
finally {
    $__codexGzip.Dispose()
    $__codexMemory.Dispose()
    $__codexSourceMemory.Dispose()
}
if ($__codexSourceBytes.Length -ne %(fingerprint_size)d) { exit 1 }
$__codexHasher = [System.Security.Cryptography.SHA256]::Create()
try {
    $__codexActualHash = -join ($__codexHasher.ComputeHash($__codexSourceBytes) | ForEach-Object { $_.ToString('x2') })
}
finally {
    $__codexHasher.Dispose()
}
if ($__codexActualHash -cne '%(fingerprint_sha256)s') { exit 1 }
$__codexSource = [System.Text.Encoding]::UTF8.GetString($__codexSourceBytes)
$__codexSource = $__codexSource.Insert(
%(param_block_position)d, [System.Text.Encoding]::UTF8.GetString(
[System.Convert]::FromBase64String('%(context)s')))
$__codexThreadUrl = [System.Text.Encoding]::UTF8.GetString(
[System.Convert]::FromBase64String('%(thread_url)s'))
& ([System.Management.Automation.ScriptBlock]::Create($__codexSource)) $__codexThreadUrl"""


@dataclass(frozen=True)
class _CodexThreadRequest:
    path: str | None
    prompt: str | None


@dataclass(frozen=True)
class _CodexWindowSnapshot:
    handles: frozenset[int]
    foreground_handle: int | None


@dataclass(frozen=True)
class _WindowsClipboardSnapshot:
    text: str | None


def _powershell_executable() -> str:
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(powershell if powershell.is_file() else "powershell.exe")


def _read_trusted_codex_launcher(launcher_path: Path) -> bytes | None:
    try:
        return read_verified_file_bytes(
            launcher_path,
            expected_size_bytes=CODEX_LOCAL_LAUNCHER_FINGERPRINT.size_bytes,
            expected_sha256=CODEX_LOCAL_LAUNCHER_FINGERPRINT.sha256,
        )
    except FileIntegrityError:
        return None


def _skip_powershell_leading_whitespace(script_source: str) -> int:
    position = 1 if script_source.startswith("\ufeff") else 0
    while position < len(script_source) and script_source[position].isspace():
        position += 1
    return position


def _powershell_param_block_start(script_source: str) -> int:
    position = _skip_powershell_leading_whitespace(script_source)
    if script_source[position : position + 5].lower() != "param":
        raise ValueError("trusted launcher must begin with a PowerShell param block")
    position += 5
    if position < len(script_source) and (script_source[position].isalnum() or script_source[position] == "_"):
        raise ValueError("trusted launcher has an invalid PowerShell param block")
    while position < len(script_source) and script_source[position].isspace():
        position += 1
    if position >= len(script_source) or script_source[position] != "(":
        raise ValueError("trusted launcher has no PowerShell param block")
    return position


def _skip_powershell_single_quoted_text(script_source: str, position: int) -> int:
    position += 1
    while position < len(script_source):
        if script_source[position] != "'":
            position += 1
            continue
        if position + 1 < len(script_source) and script_source[position + 1] == "'":
            position += 2
            continue
        return position + 1
    return position


def _skip_powershell_double_quoted_text(script_source: str, position: int) -> int:
    position += 1
    while position < len(script_source):
        character = script_source[position]
        if character == "`":
            position += 2
            continue
        if character == '"':
            return position + 1
        position += 1
    return position


def _skip_powershell_quoted_text(script_source: str, position: int) -> int:
    if script_source[position] == "'":
        return _skip_powershell_single_quoted_text(script_source, position)
    return _skip_powershell_double_quoted_text(script_source, position)


def _skip_powershell_comment(script_source: str, position: int) -> int:
    line_end = script_source.find("\n", position)
    return len(script_source) if line_end < 0 else line_end


def _scan_powershell_param_block(script_source: str, position: int) -> int:
    depth = 0
    while position < len(script_source):
        character = script_source[position]
        if character in ("'", '"'):
            position = _skip_powershell_quoted_text(script_source, position)
            continue
        if character == "#":
            position = _skip_powershell_comment(script_source, position)
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return position
        position += 1
    raise ValueError("trusted launcher has an unclosed PowerShell param block")


def _powershell_param_block_end(script_source: str) -> int:
    return _scan_powershell_param_block(script_source, _powershell_param_block_start(script_source))


def _powershell_base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _powershell_utf8_assignment(name: str, value: str) -> str:
    encoded_value = _powershell_base64(value.encode("utf-8"))
    return f"{name} = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{encoded_value}'))"


def _trusted_launcher_context(launcher_path: Path) -> str:
    return "\n".join(
        ("", _powershell_utf8_assignment("$PSScriptRoot", str(launcher_path.parent)), _powershell_utf8_assignment("$PSCommandPath", str(launcher_path)))
    )


def _powershell_param_block_position(script_source: str) -> int:
    param_block_end = _powershell_param_block_end(script_source)
    return len(script_source[: param_block_end + 1].encode("utf-16-le")) // 2


def _trusted_launcher_wrapper(
    launcher_source: bytes,
    param_block_position: int,
    context: str,
    thread_url: str,
) -> str:
    return _TRUSTED_LAUNCHER_WRAPPER_TEMPLATE % {
        "compressed_source": _powershell_base64(gzip.compress(launcher_source, mtime=0)),
        "fingerprint_size": CODEX_LOCAL_LAUNCHER_FINGERPRINT.size_bytes,
        "fingerprint_sha256": CODEX_LOCAL_LAUNCHER_FINGERPRINT.sha256,
        "param_block_position": param_block_position,
        "context": _powershell_base64(context.encode("utf-8")),
        "thread_url": _powershell_base64(thread_url.encode("utf-8")),
    }


def _powershell_encoded_command(powershell: str, wrapper: str) -> list[str]:
    return [
        powershell,
        "-NoProfile",
        "-EncodedCommand",
        _powershell_base64(wrapper.encode("utf-16-le")),
    ]


def _validate_windows_command_length(command: list[str]) -> None:
    if len(subprocess.list2cmdline(command)) >= _WINDOWS_MAX_COMMAND_LINE_CHARACTERS:
        raise ValueError("trusted launcher command exceeds the Windows command-line limit")


def _build_trusted_codex_launcher_command(
    powershell: str,
    launcher_path: Path,
    launcher_source: bytes,
    thread_url: str,
) -> list[str]:
    # The child receives this in-memory, sealed source rather than reopening launcher_path.
    source = launcher_source.decode("utf-8")
    wrapper = _trusted_launcher_wrapper(
        launcher_source,
        _powershell_param_block_position(source),
        _trusted_launcher_context(launcher_path),
        thread_url,
    )
    command = _powershell_encoded_command(powershell, wrapper)
    _validate_windows_command_length(command)
    return command


def _parse_codex_thread_url(thread_url: str) -> _CodexThreadRequest | None:
    parsed = urlparse(str(thread_url or ""))
    if parsed.scheme.lower() != "codex":
        return None

    query = parse_qs(parsed.query)
    path = (query.get("path") or [None])[0]
    prompt = (query.get("prompt") or [None])[0]
    return _CodexThreadRequest(path=path or None, prompt=prompt or None)


def _codex_thread_url_without_prompt(thread_url: str) -> str:
    parsed = urlparse(str(thread_url or ""))
    if parsed.scheme.lower() != "codex":
        return thread_url

    if "prompt" not in parse_qs(parsed.query):
        return thread_url

    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "prompt"
    ]
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(query_items),
            parsed.fragment,
        )
    )


def _codex_activation_arguments(request: _CodexThreadRequest) -> str:
    query_items = []
    if request.path:
        query_items.append(("path", request.path))
    if request.prompt:
        query_items.append(("prompt", request.prompt))
    query = urlencode(query_items)
    return f"codex://new?{query}" if query else "codex://new"


def _guid_from_string(value: str):
    import ctypes
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    parsed = uuid.UUID(value)
    return _GUID(
        parsed.time_low,
        parsed.time_mid,
        parsed.time_hi_version,
        (ctypes.c_ubyte * 8).from_buffer_copy(parsed.bytes[8:]),
    )


def _failed_hresult(value: int) -> bool:
    import ctypes

    return ctypes.c_long(value).value < 0


def _activate_codex_appx(arguments: str) -> bool:
    import ctypes
    from ctypes import wintypes

    ole32 = ctypes.OleDLL("ole32")
    init_result = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    should_uninitialize = init_result >= 0
    if _failed_hresult(init_result) and ctypes.c_long(init_result).value != _RPC_E_CHANGED_MODE:
        return False

    app_manager = ctypes.c_void_p()
    try:
        clsid = _guid_from_string(_APP_ACTIVATION_MANAGER_CLSID)
        iid = _guid_from_string(_IAPPLICATION_ACTIVATION_MANAGER_IID)
        create_result = ole32.CoCreateInstance(
            ctypes.byref(clsid),
            None,
            _CLSCTX_ALL,
            ctypes.byref(iid),
            ctypes.byref(app_manager),
        )
        if _failed_hresult(create_result) or not app_manager.value:
            return False

        vtable = ctypes.cast(app_manager, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        activate_application = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )(vtable[3])
        process_id = wintypes.DWORD()
        activate_result = activate_application(
            app_manager,
            CODEX_APP_USER_MODEL_ID,
            arguments,
            0,
            ctypes.byref(process_id),
        )
        return not _failed_hresult(activate_result)
    finally:
        if app_manager.value:
            vtable = ctypes.cast(app_manager, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            release = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtable[2])
            release(app_manager)
        if should_uninitialize:
            ole32.CoUninitialize()


def _copy_text_to_windows_clipboard(text: str) -> bool:
    if os.name != "nt":
        return False

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.restype = wintypes.HGLOBAL
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HGLOBAL]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.CloseClipboard.restype = wintypes.BOOL

        payload = (text + "\0").encode("utf-16le")
        handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(payload))
        if not handle:
            return False
        locked = kernel32.GlobalLock(handle)
        if not locked:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(locked, payload, len(payload))
        kernel32.GlobalUnlock(handle)

        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(handle)
            return False
        try:
            user32.EmptyClipboard()
            if not user32.SetClipboardData(_CF_UNICODETEXT, handle):
                kernel32.GlobalFree(handle)
                return False
            handle = None
            return True
        finally:
            user32.CloseClipboard()
            if handle:
                kernel32.GlobalFree(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _capture_windows_clipboard_snapshot() -> _WindowsClipboardSnapshot | None:
    if os.name != "nt":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.CountClipboardFormats.restype = ctypes.c_int
        user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        if not user32.OpenClipboard(None):
            return None
        try:
            format_count = int(user32.CountClipboardFormats())
            if format_count == 0:
                return _WindowsClipboardSnapshot(text=None)
            if format_count != 1 or not user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
                return None
            handle = user32.GetClipboardData(_CF_UNICODETEXT)
            if not handle:
                return None
            locked = kernel32.GlobalLock(handle)
            if not locked:
                return None
            try:
                return _WindowsClipboardSnapshot(text=ctypes.wstring_at(locked))
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _clear_windows_clipboard() -> bool:
    if os.name != "nt":
        return False

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.restype = wintypes.BOOL
        if not user32.OpenClipboard(None):
            return False
        try:
            return bool(user32.EmptyClipboard())
        finally:
            user32.CloseClipboard()
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _restore_windows_clipboard_snapshot(snapshot: _WindowsClipboardSnapshot) -> bool:
    if snapshot.text is None:
        return _clear_windows_clipboard()
    return _copy_text_to_windows_clipboard(snapshot.text)


def _clipboard_sequence_number() -> int | None:
    if os.name != "nt":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
        return int(user32.GetClipboardSequenceNumber())
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _copy_codex_prompt_to_clipboard(prompt: str | None) -> bool:
    text = str(prompt or "").strip()
    if not text:
        return False
    return _copy_text_to_windows_clipboard(text)


def _is_codex_window_title(title: str) -> bool:
    text = str(title or "").strip()
    return text == "Codex" or text.endswith(" - Codex")


def _codex_window_executable_path(hwnd: int) -> str | None:
    if os.name != "nt" or not hwnd:
        return None

    kernel32 = None
    process_handle = None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        process_id = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id)) or not process_id.value:
            return None
        process_handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value)
        if not process_handle:
            return None
        capacity = 32768
        buffer = ctypes.create_unicode_buffer(capacity)
        path_length = wintypes.DWORD(capacity)
        if not kernel32.QueryFullProcessImageNameW(process_handle, 0, buffer, ctypes.byref(path_length)):
            return None
        return buffer.value or None
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    finally:
        if kernel32 is not None and process_handle:
            kernel32.CloseHandle(process_handle)


def _is_codex_window(hwnd: int, title: str) -> bool:
    text = str(title or "").strip()
    if text != "ChatGPT" and not text.endswith(" - ChatGPT") and not _is_codex_window_title(text):
        return False

    executable_path = _codex_window_executable_path(hwnd)
    if not executable_path:
        return False
    normalized_path = executable_path.replace("/", "\\").casefold()
    executable_name = normalized_path.rsplit("\\", 1)[-1]
    return (
        executable_name in {"chatgpt.exe", "codex.exe"}
        and "\\windowsapps\\openai.codex_" in normalized_path
    )


def _is_codex_window_handle(hwnd: int) -> bool:
    if os.name != "nt" or not hwnd:
        return False

    try:
        import win32gui

        return _is_codex_window(int(hwnd), win32gui.GetWindowText(hwnd))
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return False


def _list_codex_window_handles() -> frozenset[int]:
    if os.name != "nt":
        return frozenset()

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int

        handles: set[int] = set()

        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if _is_codex_window(int(hwnd), buffer.value):
                handles.add(int(hwnd))
            return True

        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(callback)
        user32.EnumWindows(enum_proc, 0)
        return frozenset(handles)
    except (AttributeError, OSError, TypeError, ValueError):
        return frozenset()


def _foreground_window_handle() -> int | None:
    if os.name != "nt":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = int(user32.GetForegroundWindow() or 0)
        return hwnd or None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _codex_window_snapshot() -> _CodexWindowSnapshot:
    return _CodexWindowSnapshot(
        handles=_list_codex_window_handles(),
        foreground_handle=_foreground_window_handle(),
    )


def _select_codex_paste_target(
    before: _CodexWindowSnapshot,
    *,
    allow_reused_foreground: bool = False,
) -> int | None:
    current = _codex_window_snapshot()
    new_handles = current.handles - before.handles
    if not new_handles:
        if not before.handles and current.foreground_handle in current.handles:
            return current.foreground_handle
        if (
            allow_reused_foreground
            and current.foreground_handle in current.handles
            and current.foreground_handle != before.foreground_handle
        ):
            return current.foreground_handle
        if (
            allow_reused_foreground
            and len(current.handles) == 1
            and before.foreground_handle not in current.handles
        ):
            return next(iter(current.handles))
        return None

    if current.foreground_handle in new_handles:
        return current.foreground_handle
    return sorted(new_handles)[-1]


def _wait_for_codex_paste_target(before: _CodexWindowSnapshot) -> int | None:
    started_at = time.monotonic()
    deadline = started_at + _CODEX_TARGET_WINDOW_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        allow_reused_foreground = time.monotonic() - started_at >= _CODEX_REUSED_WINDOW_READY_SECONDS
        hwnd = _select_codex_paste_target(before, allow_reused_foreground=allow_reused_foreground)
        if hwnd is not None:
            return hwnd
        time.sleep(_CODEX_TARGET_WINDOW_POLL_SECONDS)
    return None


def _focus_window(hwnd: int) -> bool:
    if os.name != "nt" or not hwnd:
        return False

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.ShowWindow(hwnd, _SW_RESTORE)
        return bool(user32.SetForegroundWindow(hwnd))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _send_ctrl_v() -> bool:
    if os.name != "nt":
        return False

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, wintypes.ULONG]
        user32.keybd_event(_VK_CONTROL, 0, 0, 0)
        user32.keybd_event(_VK_V, 0, 0, 0)
        user32.keybd_event(_VK_V, 0, _KEYEVENTF_KEYUP, 0)
        user32.keybd_event(_VK_CONTROL, 0, _KEYEVENTF_KEYUP, 0)
        return True
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _paste_codex_prompt_when_target_ready(prompt: str, before: _CodexWindowSnapshot) -> None:
    clipboard_snapshot = _capture_windows_clipboard_snapshot()
    if clipboard_snapshot is None:
        return

    prompt_clipboard_sequence = None
    try:
        if not _copy_codex_prompt_to_clipboard(prompt):
            return
        prompt_clipboard_sequence = _clipboard_sequence_number()

        hwnd = _wait_for_codex_paste_target(before)
        if hwnd is None:
            return

        if not _focus_window(hwnd):
            return

        time.sleep(_CODEX_TARGET_WINDOW_READY_DELAY_SECONDS)
        if _foreground_window_handle() != hwnd or not _is_codex_window_handle(hwnd):
            return
        if (
            prompt_clipboard_sequence is None
            or _clipboard_sequence_number() != prompt_clipboard_sequence
        ):
            return
        _send_ctrl_v()
    finally:
        current_clipboard_sequence = _clipboard_sequence_number()
        if (
            prompt_clipboard_sequence is not None
            and current_clipboard_sequence == prompt_clipboard_sequence
        ):
            _restore_windows_clipboard_snapshot(clipboard_snapshot)


def _schedule_codex_prompt_paste(prompt: str | None, before: _CodexWindowSnapshot) -> None:
    text = str(prompt or "").strip()
    if not text:
        return

    threading.Thread(
        target=_paste_codex_prompt_when_target_ready,
        args=(text, before),
        name="CodexPromptPaste",
        daemon=True,
    ).start()


def _try_open_codex_desktop_thread_fast(thread_url: str) -> bool:
    if os.name != "nt":
        return False

    request = _parse_codex_thread_url(thread_url)
    if request is None:
        return False

    try:
        if not _activate_codex_appx(_codex_activation_arguments(request)):
            return False
    except (AttributeError, OSError, TypeError, ValueError):
        return False

    return True


def open_codex_desktop_thread(thread_url: str, *, launcher: str | Path | None = None) -> bool:
    if launcher is None and _try_open_codex_desktop_thread_fast(thread_url):
        return True

    request = _parse_codex_thread_url(thread_url)
    before = _codex_window_snapshot() if request is not None else None
    if request is not None:
        thread_url = _codex_thread_url_without_prompt(thread_url)

    launcher_path = Path(launcher) if launcher is not None else CODEX_LOCAL_LAUNCHER
    launcher_source = _read_trusted_codex_launcher(launcher_path)
    if launcher_source is None:
        return False

    try:
        spawn_silent_process(
            _build_trusted_codex_launcher_command(
                _powershell_executable(),
                launcher_path,
                launcher_source,
                thread_url,
            ),
        )
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    if request is not None and before is not None:
        _schedule_codex_prompt_paste(request.prompt, before)
    return True


__all__ = [
    "CODEX_APP_USER_MODEL_ID",
    "CODEX_LOCAL_LAUNCHER",
    "ExternalTerminalNavigator",
    "open_codex_desktop_thread",
]
