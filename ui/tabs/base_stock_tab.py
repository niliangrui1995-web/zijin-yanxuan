# -*- coding: utf-8 -*-
"""BaseStockTab — 所有股票列表 Tab 的公共基类

提取各 Tab 中重复的通用逻辑：
- 涨跌着色
- 历史缓存回填
- 右键菜单构建
- 通达信跳转
- 代码复制
"""

import logging
import os
import re
import subprocess
import time
import webbrowser

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from core.event_bus import event_bus
from ui.status_registry import format_status_summary, format_workspace_status
from ui.tabs.base_stock_refresh import (
    async_update_market_caps as run_async_market_caps,
)
from ui.tabs.base_stock_refresh import (
    collect_missing_finance_codes as collect_refresh_missing_finance_codes,
)
from ui.tabs.base_stock_refresh import (
    collect_quote_refresh_codes as collect_refresh_quote_codes,
)
from ui.tabs.base_stock_refresh import (
    collect_table_codes as collect_refresh_table_codes,
)
from ui.tabs.base_stock_refresh import (
    on_rt_quotes_direct as apply_rt_quotes_direct,
)
from ui.tabs.base_stock_refresh import (
    prime_local_quote_snapshot as warm_local_quote_snapshot,
)
from ui.tabs.base_stock_refresh import (
    refresh_table_from_latest_snapshot as refresh_quotes_from_latest_snapshot,
)
from ui.tabs.base_stock_refresh import (
    refresh_table_quotes_and_market_caps as refresh_quotes_and_market_caps,
)
from ui.tabs.base_stock_refresh import (
    replay_deferred_quotes,
)
from ui.tabs.base_stock_refresh import (
    subscribe_global_quotes as subscribe_quote_stream,
)
from ui.theme_tokens import build_ui_tokens


class BaseStockTab(QWidget):
    DEFAULT_TOOLBAR_HINT = "双击查看K线｜右键更多操作｜Enter 打开｜Esc 退出搜索"
    """股票列表 Tab 基类 — 提供通用方法"""

    def __init__(self, data_provider=None, parent=None):
        super().__init__(parent)
        self.data_provider = data_provider
        self._deferred_quote_refresh = False
        self._missing_quote_publisher_warned = False

    def _resolve_active_quote_model(self):
        return getattr(self, '_active_model_ref', None) \
             or getattr(self, 'source_model', None) \
             or getattr(self, 'model', None)

    def _apply_quote_snapshot(self, quotes: dict | None):
        model = self._resolve_active_quote_model()
        if model and hasattr(model, 'update_quotes') and quotes:
            model.update_quotes(quotes)

    def _resolve_quote_publisher(self):
        publisher = getattr(self, "_quote_publisher", None)
        if publisher is not None:
            return publisher
        owner_window = self.window()
        return getattr(owner_window, "central_quotes_svc", None)

    def _publish_quote_payload(self, payload, *, source: str, require_valid: bool = False) -> dict:
        normalized = dict(payload or {})
        if not normalized:
            return {}

        publisher = self._resolve_quote_publisher()
        if publisher is None or not hasattr(publisher, "publish_external_quotes"):
            if not self._missing_quote_publisher_warned:
                self._missing_quote_publisher_warned = True
                logging.getLogger(__name__).warning(
                    f"[{self.__class__.__name__}] 未找到 central_quotes_svc，已跳过外部报价广播"
                )
            return {}

        self._missing_quote_publisher_warned = False
        return publisher.publish_external_quotes(
            normalized,
            source=source,
            require_valid=require_valid,
        ) or {}

    @staticmethod
    def _is_blank_quote_value(value, zero_is_blank=True) -> bool:
        text = "" if value is None else str(value).strip()
        if text in {"", "--"}:
            return True
        if zero_is_blank and text in {"0", "0.0", "0.00"}:
            return True
        return False

    def _collect_table_codes(self, current_model=None) -> list[str]:
        return collect_refresh_table_codes(self, current_model)

    def _collect_quote_refresh_codes(self, current_model=None, force=False) -> list[str]:
        return collect_refresh_quote_codes(self, current_model, force=force)

    def _collect_missing_finance_codes(self, current_model=None) -> list[str]:
        return collect_refresh_missing_finance_codes(self, current_model)

    def refresh_table_quotes_and_market_caps(self, current_model=None, force_quotes=False, quote_task_id=None):
        refresh_quotes_and_market_caps(
            self,
            current_model=current_model,
            force_quotes=force_quotes,
            quote_task_id=quote_task_id,
        )

    def prime_local_quote_snapshot(self, current_model=None):
        return warm_local_quote_snapshot(self, current_model=current_model)

    def refresh_table_from_latest_snapshot(self, current_model=None):
        refresh_quotes_from_latest_snapshot(self, current_model=current_model)

    @staticmethod
    def _prepare_toolbar_widget(widget: QWidget | None):
        if widget is None:
            return
        widget.setProperty("inToolbar", True)
        if isinstance(widget, QLabel) and widget.property("toolbarRole") is None:
            widget.setProperty("toolbarRole", "meta")
        if isinstance(widget, QToolButton) and widget.property("class") is None:
            widget.setProperty("class", "toolbarGhost")

    @staticmethod
    def _install_search_escape_behavior(widget: QWidget | None):
        if not isinstance(widget, QLineEdit):
            return
        if widget.property("_toolbarEscapeHookInstalled"):
            return

        widget.setProperty("_toolbarEscapeHookInstalled", True)
        widget.setClearButtonEnabled(True)
        original_keypress = widget.keyPressEvent

        def _wrapped_keypress(event):
            if event.key() == Qt.Key.Key_Escape:
                if widget.text():
                    widget.clear()
                widget.clearFocus()
                event.accept()
                return
            original_keypress(event)

        widget.keyPressEvent = _wrapped_keypress

    @staticmethod
    def _toolbar_button_texts(button: QPushButton) -> list[str]:
        hints = button.property("toolbarWidthHints")
        texts: list[str] = []

        if isinstance(hints, (list, tuple, set)):
            texts.extend(str(item).strip() for item in hints if str(item).strip())
        elif isinstance(hints, str):
            texts.extend(part.strip() for part in hints.split("|") if part.strip())

        current_text = str(button.text() or "").strip()
        if current_text and current_text not in texts:
            texts.append(current_text)
        return texts

    @classmethod
    def _equalize_toolbar_action_widths(cls, action_widgets: list[QWidget] | None):
        if not action_widgets:
            return

        candidates: list[QPushButton] = []
        for widget in action_widgets:
            if not isinstance(widget, QPushButton):
                continue
            if widget.property("toolbarWidthPolicy") == "content":
                continue
            candidates.append(widget)

        if len(candidates) < 2:
            return

        target_width = 0
        for button in candidates:
            texts = cls._toolbar_button_texts(button)
            if not texts:
                continue
            metrics = button.fontMetrics()
            content_width = max(metrics.horizontalAdvance(text) for text in texts)
            icon_width = 18 if not button.icon().isNull() else 0
            button_width = max(button.minimumWidth(), content_width + icon_width + 28)
            target_width = max(target_width, button_width)

        if target_width <= 0:
            return

        for button in candidates:
            button.setMinimumWidth(target_width)

    def _build_toolbar_flow_group(
        self,
        object_name: str,
        widgets: list[QWidget] | None,
        *,
        h_spacing: int | None = None,
        v_spacing: int | None = None,
    ) -> QWidget | None:
        valid_widgets = [widget for widget in (widgets or []) if widget is not None]
        if not valid_widgets:
            return None

        tokens = build_ui_tokens()
        group_host = QWidget()
        group_host.setObjectName(object_name)
        group_layout = QHBoxLayout(group_host)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(tokens["shell"]["toolbar_group_gap"] if h_spacing is None else h_spacing)

        for widget in valid_widgets:
            self._prepare_toolbar_widget(widget)
            group_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        return group_host

    @staticmethod
    def _status_metric(label: str, value, suffix: str = "") -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        return f"{label}{text}{suffix}"

    @classmethod
    def format_status_summary(cls, primary: str, *segments: str) -> str:
        return format_status_summary(primary, *segments)

    @classmethod
    def format_workspace_status(
        cls,
        primary: str,
        *,
        result: str = "",
        freshness: str = "",
        current_filter: str = "",
        next_step: str = "",
        extra_segments: tuple[str, ...] | list[str] | None = None,
    ) -> str:
        return format_workspace_status(
            primary,
            result=result,
            freshness=freshness,
            current_filter=current_filter,
            next_step=next_step,
            extra_segments=extra_segments,
        )

    # ================================================================
    # UI 结构辅助：统一工具条 + 摘要条 + 列预设
    # ================================================================
    def build_tab_toolbar(self, title: str, subtitle_label: QLabel | None,
                          filter_widgets: list[QWidget] | None,
                          action_widgets: list[QWidget] | None) -> QWidget:
        """统一工具条结构：标题区 + 筛选区 + 操作区，全部压缩到单行。"""
        tokens = build_ui_tokens()
        toolbar = QWidget()
        toolbar.setObjectName("tabToolbar")
        toolbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(
            tokens["shell"]["toolbar_padding_x"],
            tokens["shell"]["toolbar_padding_y"],
            tokens["shell"]["toolbar_padding_x"],
            tokens["shell"]["toolbar_padding_y"],
        )
        tb_layout.setSpacing(tokens["shell"]["toolbar_section_gap"])

        left_wrap = QFrame()
        left_wrap.setObjectName("tabToolbarTitleWrap")
        left_wrap.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        left_layout = QHBoxLayout(left_wrap)
        left_layout.setContentsMargins(
            max(6, tokens["shell"]["toolbar_padding_x"] - 2),
            0,
            max(6, tokens["shell"]["toolbar_padding_x"] - 2),
            0,
        )
        left_layout.setSpacing(tokens["shell"]["toolbar_group_gap"] + 1)
        left_wrap.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        left_wrap.setMinimumHeight(tokens["control"]["toolbar_button_height"] + 1)

        lbl_title = QLabel(title)
        lbl_title.setObjectName("tabTitle")
        left_layout.addWidget(lbl_title, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if subtitle_label is not None:
            subtitle_label.setObjectName("tabStatusLabel")
            subtitle_label.setProperty("toolbarRole", "status")
            subtitle_label.setWordWrap(False)
            subtitle_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
            left_layout.addWidget(subtitle_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        tb_layout.addWidget(left_wrap, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        for widget in filter_widgets or []:
            self._install_search_escape_behavior(widget)

        filter_wrap = self._build_toolbar_flow_group("tabToolbarFilters", filter_widgets)
        if filter_wrap is not None:
            filter_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            tb_layout.addWidget(filter_wrap, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        else:
            tb_layout.addStretch(1)

        toolbar_hint = str(getattr(self, "toolbar_hint_text", self.DEFAULT_TOOLBAR_HINT) or "").strip()
        if toolbar_hint:
            hint_label = QLabel(toolbar_hint)
            hint_label.setObjectName("tabToolbarHint")
            hint_label.setProperty("toolbarRole", "meta")
            hint_label.setToolTip(toolbar_hint)
            hint_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
            tb_layout.addWidget(hint_label, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if action_widgets:
            self._equalize_toolbar_action_widths(action_widgets)
        action_wrap = self._build_toolbar_flow_group("tabToolbarActions", action_widgets)
        if action_wrap is not None:
            action_wrap.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            tb_layout.addWidget(action_wrap, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        return toolbar
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
        except (webbrowser.Error, OSError, RuntimeError) as e:
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
        except (OSError, RuntimeError, ValueError) as e:
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
        except (AttributeError, ImportError, OSError, RuntimeError, subprocess.SubprocessError, TypeError, ValueError) as e:
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
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as e:
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
            except (AttributeError, RuntimeError, TypeError, ValueError) as e:
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
            except (AttributeError, RuntimeError, TypeError, ValueError) as _e:
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
        subscribe_quote_stream(self, current_model)

    def _on_rt_quotes_direct(self, quotes: dict):
        """v4 直达信号：实时行情广播，不再需要 if-elif 路由"""
        apply_rt_quotes_direct(self, quotes)

    def showEvent(self, event):
        super().showEvent(event)
        replay_deferred_quotes(self)



    def async_update_market_caps(self):
        """异步补齐缺失股本，并通过共享批次去重后回灌动态市值。"""
        run_async_market_caps(self)
