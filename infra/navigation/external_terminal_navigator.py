# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import time
import webbrowser

from domains.runtime import domain_events as event_bus
from infra.tasks import ProcessSubprocessError, spawn_process


class ExternalTerminalNavigator:
    """Infrastructure service for quote terminal navigation and code input."""

    def __init__(self, owner):
        self._owner = owner

    def launch_tdx(self, code: str) -> None:
        import threading

        threading.Thread(target=self._launch_tdx_impl, args=(code,), daemon=True).start()

    def launch_eastmoney(self, code: str) -> None:
        import threading

        threading.Thread(target=self._launch_eastmoney_impl, args=(code,), daemon=True).start()

    @staticmethod
    def _normalize_quote_code(code: str) -> str:
        raw = str(code or "").strip().lower()
        match = re.search(r"(\d{6})", raw)
        if match:
            return match.group(1)

        raw = raw.replace(".", "").replace("-", "")
        for prefix in ("sh", "sz", "bj"):
            raw = raw.replace(prefix, "")
        return "".join(ch for ch in raw if ch.isalnum())

    @classmethod
    def _detect_quote_prefix(cls, code: str) -> str:
        bare = cls._normalize_quote_code(code)
        if bare.startswith(("4", "8")) or bare.startswith("92"):
            return "BJ"
        if bare.startswith(("5", "6", "9")):
            return "SH"
        return "SZ"

    def _open_quote_web_fallback(self, code: str, reason: str = "") -> None:
        bare = self._normalize_quote_code(code)
        if not bare:
            return
        prefix = self._detect_quote_prefix(bare)
        url = f"https://quote.eastmoney.com/{prefix}{bare}.html"
        try:
            webbrowser.open(url)
            suffix = f" ({reason})" if reason else ""
            event_bus.sig_system_log.emit("warn", f"[跳转兜底] 已改为打开网页行情: {bare}{suffix}")
        except (webbrowser.Error, OSError, RuntimeError) as exc:
            event_bus.sig_system_log.emit("error", f"[跳转兜底] 打开网页行情失败: {exc}")

    @staticmethod
    def _wintypes_module(ctypes_module):
        wintypes_module = getattr(ctypes_module, "wintypes", None)
        if wintypes_module is None:
            import ctypes.wintypes as wintypes_module

            setattr(ctypes_module, "wintypes", wintypes_module)
        return wintypes_module

    @classmethod
    def _enum_windows_proc_type(cls, ctypes_module):
        wintypes_module = cls._wintypes_module(ctypes_module)
        callback_factory = getattr(ctypes_module, "WINFUNCTYPE", ctypes_module.CFUNCTYPE)
        return callback_factory(ctypes_module.c_bool, wintypes_module.HWND, wintypes_module.LPARAM)

    @classmethod
    def _null_hwnd(cls, ctypes_module):
        return cls._wintypes_module(ctypes_module).HWND(0)

    @staticmethod
    def _window_process_id(hwnd) -> int | None:
        try:
            import pywintypes
            import win32process
        except ImportError:
            return None

        try:
            return int(win32process.GetWindowThreadProcessId(hwnd)[1])
        except (OSError, RuntimeError, TypeError, ValueError, pywintypes.error):
            return None

    @staticmethod
    def _process_integrity_level(process_id: int) -> int | None:
        try:
            import pywintypes
            import win32api
            import win32con
            import win32security
        except ImportError:
            return None

        process_handle = None
        token_handle = None
        try:
            process_handle = win32api.OpenProcess(0x1000, False, int(process_id))
            token_handle = win32security.OpenProcessToken(process_handle, win32con.TOKEN_QUERY)
            integrity_info = win32security.GetTokenInformation(token_handle, win32security.TokenIntegrityLevel)
            sid = integrity_info[0] if isinstance(integrity_info, tuple) else integrity_info
            return int(sid.GetSubAuthority(sid.GetSubAuthorityCount() - 1))
        except (OSError, RuntimeError, TypeError, ValueError, pywintypes.error):
            return None
        finally:
            for handle in (token_handle, process_handle):
                try:
                    if handle is not None:
                        handle.Close()
                except (AttributeError, OSError, pywintypes.error):
                    pass

    @classmethod
    def _can_send_input_to_window(cls, hwnd) -> bool:
        target_process_id = cls._window_process_id(hwnd)
        if target_process_id is None:
            return False
        current_level = cls._process_integrity_level(os.getpid())
        target_level = cls._process_integrity_level(target_process_id)
        if current_level is None or target_level is None:
            return False
        return current_level >= target_level

    @staticmethod
    def _activate_window(user32, hwnd) -> bool:
        import win32api
        import win32gui

        win32_errors = (OSError, RuntimeError, TypeError, ValueError)
        try:
            import pywintypes

            win32_errors += (pywintypes.error,)
        except ImportError:
            pass

        attached_threads = []
        try:
            current_thread = win32api.GetCurrentThreadId()
            foreground_hwnd = user32.GetForegroundWindow()
            thread_ids = []
            if foreground_hwnd:
                thread_ids.append(user32.GetWindowThreadProcessId(foreground_hwnd, None))
            thread_ids.append(user32.GetWindowThreadProcessId(hwnd, None))

            for thread_id in dict.fromkeys(thread_ids):
                if thread_id and thread_id != current_thread:
                    try:
                        if user32.AttachThreadInput(current_thread, thread_id, True):
                            attached_threads.append(thread_id)
                    except win32_errors:
                        continue

            user32.ShowWindow(hwnd, 9 if user32.IsIconic(hwnd) else 5)
            try:
                win32gui.BringWindowToTop(hwnd)
            except win32_errors:
                pass
            user32.SetForegroundWindow(hwnd)
            try:
                user32.SetFocus(hwnd)
            except win32_errors:
                pass
            time.sleep(0.3)
            return bool(user32.GetForegroundWindow() == hwnd)
        except win32_errors:
            return False
        finally:
            for thread_id in reversed(attached_threads):
                try:
                    user32.AttachThreadInput(current_thread, thread_id, False)
                except win32_errors:
                    pass

    @staticmethod
    def _find_input_controls(hwnd):
        import win32gui

        candidate_keywords = (
            "Edit",
            "RichEdit",
            "RICHEDIT",
            "ComboBox",
            "WindowsForms10.EDIT",
            "ThunderRT6TextBox",
        )
        candidates = []

        def callback(child_hwnd, _):
            try:
                if not win32gui.IsWindowVisible(child_hwnd) or not win32gui.IsWindowEnabled(child_hwnd):
                    return True
                class_name = win32gui.GetClassName(child_hwnd)
                if not any(keyword in class_name for keyword in candidate_keywords):
                    return True
                left, top, right, bottom = win32gui.GetWindowRect(child_hwnd)
                width = right - left
                height = bottom - top
                if width < 60 or height < 18:
                    return True
                candidates.append((child_hwnd, class_name, top, width))
            except (OSError, RuntimeError):
                return True
            return True

        win32gui.EnumChildWindows(hwnd, callback, None)
        candidates.sort(key=lambda item: (item[2], -item[3]))
        return candidates

    def _try_fill_input_control(self, hwnd, bare: str, app_name: str) -> bool:
        import win32con
        import win32gui

        for child_hwnd, class_name, _, _ in self._find_input_controls(hwnd):
            try:
                win32gui.SendMessage(child_hwnd, win32con.WM_SETTEXT, 0, bare)
                current_value = win32gui.GetWindowText(child_hwnd).strip()
                if current_value and current_value != bare:
                    continue
                win32gui.PostMessage(child_hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
                win32gui.PostMessage(child_hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
                win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
                win32gui.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
                event_bus.sig_system_log.emit("info", f"[{app_name}] 已写入输入框: {bare} ({class_name})")
                return True
            except (OSError, RuntimeError, TypeError):
                continue
        return False

    @staticmethod
    def _is_expected_window_foreground(expected_hwnd) -> bool:
        if expected_hwnd is None:
            return True
        import win32gui

        return bool(win32gui.GetForegroundWindow() == expected_hwnd)

    @staticmethod
    def _type_quote_code(bare: str, app_name: str, *, expected_hwnd=None) -> bool:
        if not ExternalTerminalNavigator._is_expected_window_foreground(expected_hwnd):
            event_bus.sig_system_log.emit("warn", f"[{app_name}] 目标窗口未处于前台，已取消快捷输入")
            return False

        import pyautogui

        pyautogui.press("esc", presses=2, interval=0.05)
        time.sleep(0.08)
        if not ExternalTerminalNavigator._is_expected_window_foreground(expected_hwnd):
            event_bus.sig_system_log.emit("warn", f"[{app_name}] 目标窗口未处于前台，已取消快捷输入")
            return False
        pyautogui.write(bare, interval=0.04)
        time.sleep(0.08)
        if not ExternalTerminalNavigator._is_expected_window_foreground(expected_hwnd):
            event_bus.sig_system_log.emit("warn", f"[{app_name}] 目标窗口未处于前台，已取消快捷输入")
            return False
        pyautogui.press("enter")
        event_bus.sig_system_log.emit("info", f"[{app_name}] 已使用窗口级快捷输入: {bare}")
        return True

    def _input_quote_code(self, user32, hwnd, code: str, app_name: str) -> bool:
        bare = self._normalize_quote_code(code)
        if not bare:
            event_bus.sig_system_log.emit("warn", f"[{app_name}] 股票代码为空，跳转取消")
            return False

        if not self._can_send_input_to_window(hwnd):
            event_bus.sig_system_log.emit(
                "warn",
                f"[{app_name}] 无法确认输入权限，或目标程序权限高于紫金研选；请以相同权限运行两个程序",
            )
            return False
        if not self._activate_window(user32, hwnd):
            event_bus.sig_system_log.emit("warn", f"[{app_name}] 未能激活目标窗口，已取消快捷输入")
            return False
        if self._try_fill_input_control(hwnd, bare, app_name):
            return True

        try:
            return self._type_quote_code(bare, app_name, expected_hwnd=hwnd)
        except (OSError, RuntimeError, ValueError) as exc:
            event_bus.sig_system_log.emit("warn", f"[{app_name}] 快捷输入失败: {exc}")
            return False

    def _launch_tdx_impl(self, code: str) -> None:
        hwnd = None
        try:
            import ctypes

            data_provider = getattr(self._owner, "data_provider", None)
            tdx_vipdoc = getattr(data_provider, "tdx_vipdoc", "")
            tdx_path = tdx_vipdoc.replace("vipdoc", "tdxw.exe") if tdx_vipdoc else ""
            if not tdx_path or not os.path.exists(tdx_path):
                event_bus.sig_system_log.emit("warn", f"[TDX] 未找到通达信: {tdx_path}")
                self._open_quote_web_fallback(code, "未找到通达信")
                return

            enum_windows_proc = self._enum_windows_proc_type(ctypes)
            user32 = ctypes.windll.user32

            def find_tdx_window():
                found_hwnd = self._null_hwnd(ctypes)

                def callback(hwnd, _):
                    nonlocal found_hwnd
                    if not user32.IsWindowVisible(hwnd):
                        return True
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        title = buf.value

                        class_buf = ctypes.create_unicode_buffer(256)
                        user32.GetClassNameW(hwnd, class_buf, 256)
                        class_name = class_buf.value

                        if (
                            "华泰网上" in title
                            or "华泰证券" in title
                            or "通达信" in title
                            or "网上股票交易" in title
                            or class_name == "TdxW_MainFrame_Class"
                        ):
                            found_hwnd = hwnd
                            return False
                    return True

                user32.EnumWindows(enum_windows_proc(callback), 0)
                return found_hwnd

            hwnd = find_tdx_window()
            if not hwnd:
                spawn_process([tdx_path])
                for _ in range(12):
                    time.sleep(0.5)
                    hwnd = find_tdx_window()
                    if hwnd:
                        break

            if hwnd:
                if not self._input_quote_code(user32, hwnd, code, "TDX"):
                    event_bus.sig_system_log.emit(
                        "warn",
                        "[TDX] 已定位通达信，但未能安全输入代码；未打开网页行情",
                    )
            else:
                event_bus.sig_system_log.emit("warn", "[TDX] 启动后仍未检测到通达信窗口，已切换网页兜底")
                self._open_quote_web_fallback(code, "通达信窗口未就绪")
        except (
            AttributeError,
            ImportError,
            OSError,
            RuntimeError,
            ProcessSubprocessError,
            TypeError,
            ValueError,
        ) as exc:
            event_bus.sig_system_log.emit("error", f"[TDX] 跳转失败: {exc}")
            if hwnd:
                event_bus.sig_system_log.emit("warn", "[TDX] 已定位通达信，但未打开网页行情")
            else:
                self._open_quote_web_fallback(code, "通达信跳转异常")

    def _launch_eastmoney_impl(self, code: str) -> None:
        hwnd = None
        try:
            import ctypes

            enum_windows_proc = self._enum_windows_proc_type(ctypes)
            user32 = ctypes.windll.user32

            def find_em_window():
                native_hwnd = self._null_hwnd(ctypes)
                fallback_hwnd = self._null_hwnd(ctypes)

                def callback(hwnd, _):
                    nonlocal fallback_hwnd, native_hwnd
                    if not user32.IsWindowVisible(hwnd):
                        return True
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        title = buf.value

                        class_buf = ctypes.create_unicode_buffer(256)
                        user32.GetClassNameW(hwnd, class_buf, 256)
                        class_name = class_buf.value

                        if "东方财富" not in title or class_name.startswith("Chrome_WidgetWin_"):
                            return True
                        if class_name.startswith("Afx:"):
                            native_hwnd = hwnd
                            return False
                        if not fallback_hwnd:
                            fallback_hwnd = hwnd
                    return True

                user32.EnumWindows(enum_windows_proc(callback), 0)
                return native_hwnd or fallback_hwnd

            hwnd = find_em_window()
            if not hwnd:
                event_bus.sig_system_log.emit("warn", "[东方财富] 未检测到运行中的东方财富终端，已切换网页行情")
                self._open_quote_web_fallback(code, "东方财富终端未运行")
                return

            if not self._input_quote_code(user32, hwnd, code, "东方财富"):
                event_bus.sig_system_log.emit(
                    "warn",
                    "[东方财富] 已定位东方财富终端，但未能安全输入代码；未打开网页行情",
                )
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            event_bus.sig_system_log.emit("error", f"[东方财富] 跳转失败: {exc}")
            if hwnd:
                event_bus.sig_system_log.emit("warn", "[东方财富] 已定位东方财富终端，但未打开网页行情")
            else:
                self._open_quote_web_fallback(code, "东方财富跳转异常")
