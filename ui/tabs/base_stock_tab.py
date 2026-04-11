# -*- coding: utf-8 -*-
"""BaseStockTab — 所有股票列表 Tab 的公共基类

提取各 Tab 中重复的通用逻辑：
- 涨跌着色
- 历史缓存回填
- 右键菜单构建
- 通达信跳转
- 代码复制
"""

import os
import re
import subprocess
import time
import webbrowser

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QWidget

from core.event_bus import event_bus


class BaseStockTab(QWidget):
    """股票列表 Tab 基类 — 提供通用方法"""

    def __init__(self, data_provider=None, parent=None):
        super().__init__(parent)
        self.data_provider = data_provider

    def _launch_tdx(self, code: str):
        """跳转通达信并输入股票代码（后台线程执行，不阻塞 UI）"""
        import threading
        threading.Thread(target=self._launch_tdx_impl, args=(code,), daemon=True).start()

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

    def _open_quote_web_fallback(self, code: str, reason: str = ""):
        bare = self._normalize_quote_code(code)
        if not bare:
            return
        prefix = self._detect_quote_prefix(bare)
        url = f"https://quote.eastmoney.com/{prefix}{bare}.html"
        try:
            webbrowser.open(url)
            suffix = f" ({reason})" if reason else ""
            event_bus.sig_system_log.emit("warn", f"[跳转兜底] 已改为打开网页行情: {bare}{suffix}")
        except Exception as e:
            event_bus.sig_system_log.emit("error", f"[跳转兜底] 打开网页行情失败: {e}")

    @staticmethod
    def _activate_window(user32, hwnd):
        import win32gui

        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        else:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW
        try:
            win32gui.BringWindowToTop(hwnd)
        except Exception:
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
            except Exception:
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
            except Exception:
                continue
        return False

    def _type_quote_code(self, bare: str, app_name: str) -> bool:
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
        except Exception as e:
            event_bus.sig_system_log.emit("warn", f"[{app_name}] 快捷输入失败: {e}")
            return False

    def _launch_tdx_impl(self, code: str):
        """实际跳转逻辑 —— 在后台 daemon 线程中执行"""
        try:
            import ctypes
            tdx_vipdoc = getattr(self.data_provider, 'tdx_vipdoc', '')
            tdx_path = tdx_vipdoc.replace("vipdoc", "tdxw.exe") if tdx_vipdoc else ""
            if not tdx_path or not os.path.exists(tdx_path):
                event_bus.sig_system_log.emit("warn", f"[TDX] 未找到通达信: {tdx_path}")
                self._open_quote_web_fallback(code, "未找到通达信")
                return

            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
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
                        
                        if ('华泰网上' in title or '华泰证券' in title or '通达信' in title or 
                            '网上股票交易' in title or class_name == 'TdxW_MainFrame_Class'):
                            found_hwnd = hwnd
                            return False
                    return True
                user32.EnumWindows(EnumWindowsProc(callback), 0)
                return found_hwnd

            hwnd = find_tdx_window()
            if not hwnd:
                subprocess.Popen([tdx_path])
                # 在后台线程中 sleep 不影响 UI
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
        except Exception as e:
            event_bus.sig_system_log.emit("error", f"[TDX] 跳转失败: {e}")
            self._open_quote_web_fallback(code, "通达信跳转异常")

    def _launch_eastmoney(self, code: str):
        """跳转东方财富并输入股票代码（后台线程执行，不阻塞 UI）"""
        import threading
        threading.Thread(target=self._launch_eastmoney_impl, args=(code,), daemon=True).start()

    def _launch_eastmoney_impl(self, code: str):
        """实际跳转逻辑 —— 在后台 daemon 线程中执行"""
        try:
            import ctypes
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
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
                        
                        if '东方财富' in title:
                            found_hwnd = hwnd
                            return False
                    return True
                user32.EnumWindows(EnumWindowsProc(callback), 0)
                return found_hwnd

            hwnd = find_em_window()
            if not hwnd:
                event_bus.sig_system_log.emit("warn", "[东方财富] 未检测到运行中的东方财富终端，已切换网页行情")
                self._open_quote_web_fallback(code, "东方财富终端未运行")
                return

            if not self._input_quote_code(user32, hwnd, code, "东方财富"):
                self._open_quote_web_fallback(code, "东方财富输入代码失败")
        except Exception as e:
            event_bus.sig_system_log.emit("error", f"[东方财富] 跳转失败: {e}")
            self._open_quote_web_fallback(code, "东方财富跳转异常")

    def bind_header_persistence(self, table, settings_key: str = "header_state"):
        """通用：绑定表格列宽调整后自动保存（带防抖），并恢复上次保存的宽度"""
        from PyQt6.QtCore import QSettings, QTimer
        
        # 使用当前类的名字作为配置的分类，确保不冲突
        settings = QSettings("VCPHunter", self.__class__.__name__)
        header = table.horizontalHeader()
        
        # 1. 如果有保存的配置，则立刻恢复
        if settings.contains(settings_key):
            try:
                header.restoreState(settings.value(settings_key))
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"恢复列宽配置异常 {settings_key}: {e}")

        # 2. 创建防抖定时器，防止拖拉列宽时高频疯狂写盘
        if not hasattr(self, "_header_save_timers"):
            self._header_save_timers = []
            
        throttle_timer = QTimer(self)
        throttle_timer.setSingleShot(True)
        throttle_timer.setInterval(800) # 停止拖拽 800ms 后保存
        self._header_save_timers.append(throttle_timer)
        
        def _save_state():
            try:
                settings.setValue(settings_key, header.saveState())
                settings.sync()
            except Exception as _e:
                # Why: 保存列宽配置是低优先级操作，失败不影响业务
                import logging
                logging.getLogger(__name__).debug(f"列宽配置保存失败: {_e}")
                
        throttle_timer.timeout.connect(_save_state)
        
        # 宽度拖拽改变 或 列被拖拽移动 时触发重置定时器
        header.sectionResized.connect(lambda: throttle_timer.start())
        header.sectionMoved.connect(lambda: throttle_timer.start())

    # ================================================================
    # 统一行情与市值基础封装 (大一统机制)
    # ================================================================

    def subscribe_global_quotes(self, current_model=None):
        """订阅中央行情站信号，自动刷新子类持有的 Model 或者通过 current_model 手动传入"""
        if current_model:
            self._active_model_ref = current_model
            
        model = getattr(self, '_active_model_ref', None) \
             or getattr(self, 'source_model', None) \
             or getattr(self, 'model', None)
             
        # 1. 尝试从 Redux Store 读取市场快照，实现秒刷 (无感知切图)
        if model and hasattr(model, 'update_quotes'):
            from core.global_store import global_store
            snapshot = global_store.get_latest_quotes()
            if snapshot:
                model.update_quotes(snapshot)
        
        # 2. 为了防止多次绑定导致的连环触发，先断开(忽略不存在的情况)
        try:
            event_bus.sig_rt_quotes.disconnect(self._on_rt_quotes_direct)
        except (TypeError, RuntimeError):
            # Why: 信号从未连接过时 disconnect 报 TypeError，是正常情况
            pass
            
        event_bus.sig_rt_quotes.connect(self._on_rt_quotes_direct)

    def _on_rt_quotes_direct(self, quotes: dict):
        """v4 直达信号：实时行情广播，不再需要 if-elif 路由"""
        # 获取有效的 model (通常在子类中赋值给了 source_model 或者是 self.model)
        model = getattr(self, '_active_model_ref', None) \
             or getattr(self, 'source_model', None) \
             or getattr(self, 'model', None)

        if model and hasattr(model, 'update_quotes'):
            model.update_quotes(quotes)



    def async_update_market_caps(self):
        """异步统一更新所在表格里的股票市值 (消除 weekend 或 null 的干扰)"""
        app = QCoreApplication.instance()
        owner_window = self.window()
        if app is None or app.closingDown():
            return
        if owner_window and getattr(owner_window, "_is_closing", False):
            return

        model = getattr(self, '_active_model_ref', None) \
             or getattr(self, 'source_model', None) \
             or getattr(self, 'model', None)
             
        if not model or not hasattr(model, 'row_data'): 
            return

        # 提取需要市值的代码
        codes_need_cap = []
        for r_dict in model.row_data:
            c = r_dict.get("代码")
            if c: 
                codes_need_cap.append(str(c))
                
        if not codes_need_cap:
            return

        def _bg_cap():
            app_obj = QCoreApplication.instance()
            if app_obj is None or app_obj.closingDown():
                return {}
            from vcp.engine import VCPEngine
            try:
                # 获取总股本 (zongguben) 即可，市值交由表格引擎底层根据实时现价计算
                finance_data = VCPEngine.batch_get_finance_info(codes_need_cap)
                return finance_data
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"[市值统一刷新] 获取股本失败: {e}")
                return {}

        def _on_cap(finance_data):
            app_obj = QCoreApplication.instance()
            owner = self.window()
            if app_obj is None or app_obj.closingDown():
                return
            if owner and getattr(owner, "_is_closing", False):
                return
            if not model or not finance_data: return
            
            for row, d in enumerate(model.row_data):
                c = d.get("代码")
                info = finance_data.get(c)
                if info:
                    zbg = info.get('zongguben', 0)
                    if zbg > 0:
                        # 注入到底层数据模型，以供 update_quotes 时动态计算
                        d['_zongguben'] = zbg
                        
                        # 如果当前“现价”已经有了数据，立刻算一次市值刷新
                        price_str = str(d.get("现价", "--")).replace(',', '')
                        if price_str not in ("--", ""):
                            try:
                                rt_close = float(price_str)
                                cap = zbg * rt_close
                                model.set_cell_value(row, "市值", f"{cap / 1e8:.0f}亿")
                            except (ValueError, TypeError) as _e:
                                import logging
                                logging.getLogger(__name__).debug(f"市值计算价格解析异常({price_str}): {_e}")

        from core.task_manager import task_manager
        task_manager.run_in_background(_bg_cap, task_id=f"caps_{self.__class__.__name__}", on_success=_on_cap)
