# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import subprocess
import time
import webbrowser

from core.domain_events import domain_events as event_bus


class QuoteTerminalLauncher:
    """封装股票终端跳转与输入代码的 UI 外联逻辑。"""

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
    def _activate_window(user32, hwnd) -> None:
        import win32gui

        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        else:
            user32.ShowWindow(hwnd, 5)
        try:
            win32gui.BringWindowToTop(hwnd)
        except OSError:
            pass
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)

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
    def _type_quote_code(bare: str, app_name: str) -> bool:
        import pyautogui

        pyautogui.press("esc", presses=2, interval=0.05)
        time.sleep(0.08)
        pyautogui.write(bare, interval=0.04)
        time.sleep(0.08)
        pyautogui.press("enter")
        event_bus.sig_system_log.emit("info", f"[{app_name}] 已使用窗口级快捷输入: {bare}")
        return True

    def _input_quote_code(self, user32, hwnd, code: str, app_name: str) -> bool:
        bare = self._normalize_quote_code(code)
        if not bare:
            event_bus.sig_system_log.emit("warn", f"[{app_name}] 股票代码为空，跳转取消")
            return False

        self._activate_window(user32, hwnd)
        if self._try_fill_input_control(hwnd, bare, app_name):
            return True

        try:
            return self._type_quote_code(bare, app_name)
        except (OSError, RuntimeError, ValueError) as exc:
            event_bus.sig_system_log.emit("warn", f"[{app_name}] 快捷输入失败: {exc}")
            return False

    def _launch_tdx_impl(self, code: str) -> None:
        try:
            import ctypes

            data_provider = getattr(self._owner, "data_provider", None)
            tdx_vipdoc = getattr(data_provider, "tdx_vipdoc", "")
            tdx_path = tdx_vipdoc.replace("vipdoc", "tdxw.exe") if tdx_vipdoc else ""
            if not tdx_path or not os.path.exists(tdx_path):
                event_bus.sig_system_log.emit("warn", f"[TDX] 未找到通达信: {tdx_path}")
                self._open_quote_web_fallback(code, "未找到通达信")
                return

            enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
            user32 = ctypes.windll.user32

            def find_tdx_window():
                found_hwnd = ctypes.wintypes.HWND(0)

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
                subprocess.Popen([tdx_path])
                for _ in range(12):
                    time.sleep(0.5)
                    hwnd = find_tdx_window()
                    if hwnd:
                        break

            if hwnd:
                if not self._input_quote_code(user32, hwnd, code, "TDX"):
                    self._open_quote_web_fallback(code, "通达信输入代码失败")
            else:
                event_bus.sig_system_log.emit("warn", "[TDX] 启动后仍未检测到通达信窗口，已切换网页兜底")
                self._open_quote_web_fallback(code, "通达信窗口未就绪")
        except (
            AttributeError,
            ImportError,
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
            TypeError,
            ValueError,
        ) as exc:
            event_bus.sig_system_log.emit("error", f"[TDX] 跳转失败: {exc}")
            self._open_quote_web_fallback(code, "通达信跳转异常")

    def _launch_eastmoney_impl(self, code: str) -> None:
        try:
            import ctypes

            enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
            user32 = ctypes.windll.user32

            def find_em_window():
                found_hwnd = ctypes.wintypes.HWND(0)

                def callback(hwnd, _):
                    nonlocal found_hwnd
                    if not user32.IsWindowVisible(hwnd):
                        return True
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        title = buf.value

                        if "东方财富" in title:
                            found_hwnd = hwnd
                            return False
                    return True

                user32.EnumWindows(enum_windows_proc(callback), 0)
                return found_hwnd

            hwnd = find_em_window()
            if not hwnd:
                event_bus.sig_system_log.emit("warn", "[东方财富] 未检测到运行中的东方财富终端，已切换网页行情")
                self._open_quote_web_fallback(code, "东方财富终端未运行")
                return

            if not self._input_quote_code(user32, hwnd, code, "东方财富"):
                self._open_quote_web_fallback(code, "东方财富输入代码失败")
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            event_bus.sig_system_log.emit("error", f"[东方财富] 跳转失败: {exc}")
            self._open_quote_web_fallback(code, "东方财富跳转异常")
