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

from PyQt6.QtCore import QCoreApplication, QRect, QSize, Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.event_bus import event_bus
from core.quote_snapshot import build_finance_quote_payload, coerce_number, is_a_share_code
from ui.status_registry import format_status_summary
from ui.theme_tokens import build_ui_tokens


class ToolbarFlowLayout(QLayout):
    """轻量级流式布局，让工具条在窄宽度下自动换行。"""

    def __init__(self, parent=None, *, h_spacing: int = 4, v_spacing: int = 4):
        super().__init__(parent)
        self._items = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, max(0, width), 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        effective_rect = rect.adjusted(left, top, -right, -bottom)
        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0

        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width()
            if line_height > 0 and next_x > effective_rect.right() + 1:
                x = effective_rect.x()
                y += line_height + self._v_spacing
                next_x = x + hint.width()
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(x, y, hint.width(), hint.height()))

            x = next_x + self._h_spacing
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + bottom


class BaseStockTab(QWidget):
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
        model = current_model or self._resolve_active_quote_model()
        if not model or not hasattr(model, "row_data"):
            return []

        codes = []
        for row_dict in getattr(model, "row_data", []) or []:
            code = self._normalize_quote_code(row_dict.get("代码", ""))
            if not code:
                continue
            if code.isdigit():
                code = code.zfill(6)
            codes.append(code)
        return list(dict.fromkeys(codes))

    def _collect_quote_refresh_codes(self, current_model=None, force=False) -> list[str]:
        model = current_model or self._resolve_active_quote_model()
        codes = self._collect_table_codes(model)
        if force or not model:
            return codes

        target_codes = []
        for row_dict in getattr(model, "row_data", []) or []:
            code = self._normalize_quote_code(row_dict.get("代码", ""))
            if not code:
                continue
            if code.isdigit():
                code = code.zfill(6)

            price_blank = self._is_blank_quote_value(
                row_dict.get("现价", row_dict.get("市价"))
            )
            pct_blank = self._is_blank_quote_value(row_dict.get("涨幅%"), zero_is_blank=False)
            if price_blank or pct_blank:
                target_codes.append(code)
        return list(dict.fromkeys(target_codes))

    def _collect_missing_finance_codes(self, current_model=None) -> list[str]:
        model = current_model or self._resolve_active_quote_model()
        if not model or not hasattr(model, "row_data"):
            return []

        try:
            from core.global_store import global_store

            snapshot = global_store.get_latest_quotes() or {}
        except (AttributeError, RuntimeError, TypeError, ValueError):
            snapshot = {}

        missing: list[str] = []
        for row_dict in getattr(model, "row_data", []) or []:
            code = self._normalize_quote_code(row_dict.get("代码", ""))
            if not is_a_share_code(code):
                continue

            snapshot_entry = snapshot.get(code) or {}
            row_zbg = coerce_number(row_dict.get("_zongguben", 0))
            snapshot_zbg = coerce_number(snapshot_entry.get("_zongguben") or snapshot_entry.get("zongguben"))
            if row_zbg <= 0 and snapshot_zbg <= 0:
                missing.append(code)

        return list(dict.fromkeys(missing))

    def refresh_table_quotes_and_market_caps(self, current_model=None, force_quotes=False, quote_task_id=None):
        if current_model is not None:
            self._active_model_ref = current_model

        model = current_model or self._resolve_active_quote_model()
        if not model or not hasattr(model, "row_data"):
            return

        codes = self._collect_table_codes(model)
        if not codes:
            return

        try:
            from core.global_store import global_store
            snapshot = global_store.get_latest_quotes() or {}
        except (AttributeError, RuntimeError, TypeError, ValueError):
            snapshot = {}

        quote_subset = {
            code: dict(snapshot[code])
            for code in codes
            if code in snapshot
        }
        if quote_subset:
            self._apply_quote_snapshot(quote_subset)

        self.async_update_market_caps()

        if not self.data_provider:
            return

        target_codes = self._collect_quote_refresh_codes(model, force=force_quotes)
        if not target_codes:
            return

        from core.task_manager import task_manager

        task_id = str(quote_task_id or f"{self.__class__.__name__.lower()}_quotes")
        if task_manager.is_active_task(task_id):
            return

        def _bg_task():
            return self.data_provider.fetch_realtime_quotes_batch(target_codes)

        def _on_success(quotes):
            if quotes:
                published = self._publish_quote_payload(
                    quotes,
                    source=f"{self.__class__.__name__}.quotes",
                )
                self._apply_quote_snapshot(published or quotes)

        def _on_error(error_message: str):
            if error_message:
                import logging
                logging.getLogger(__name__).debug(
                    f"[{self.__class__.__name__}] 表格补价失败: {error_message}"
                )

        task_manager.run_in_background(
            _bg_task,
            on_success=_on_success,
            on_error=_on_error,
            task_id=task_id,
        )

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
            button_width = max(button.minimumWidth(), content_width + icon_width + 36)
            target_width = max(target_width, button_width)

        if target_width <= 0:
            return

        for button in candidates:
            button.setMinimumWidth(target_width)

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

    # ================================================================
    # UI 结构辅助：统一工具条 + 摘要条 + 列预设
    # ================================================================
    def build_tab_toolbar(self, title: str, subtitle_label: QLabel | None,
                          filter_widgets: list[QWidget] | None,
                          action_widgets: list[QWidget] | None) -> QWidget:
        """统一工具条结构：标题区 + 流式筛选区 + 流式操作区。"""
        tokens = build_ui_tokens()
        toolbar = QWidget()
        toolbar.setObjectName("tabToolbar")
        toolbar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        tb_layout = QVBoxLayout(toolbar)
        tb_layout.setContentsMargins(
            tokens["shell"]["toolbar_padding_x"],
            tokens["shell"]["toolbar_padding_y"],
            tokens["shell"]["toolbar_padding_x"],
            tokens["shell"]["toolbar_padding_y"],
        )
        tb_layout.setSpacing(0)

        title_row = QWidget(toolbar)
        title_row.setObjectName("tabToolbarHeader")
        title_row_layout = QHBoxLayout(title_row)
        title_row_layout.setContentsMargins(0, 0, 0, 0)
        title_row_layout.setSpacing(tokens["shell"]["toolbar_section_gap"])

        left_wrap = QFrame()
        left_wrap.setObjectName("tabToolbarTitleWrap")
        left_wrap.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        left_layout = QHBoxLayout(left_wrap)
        left_layout.setContentsMargins(
            max(8, tokens["shell"]["toolbar_padding_x"] - 4),
            6,
            max(8, tokens["shell"]["toolbar_padding_x"] - 4),
            6,
        )
        left_layout.setSpacing(tokens["shell"]["toolbar_group_gap"] + 2)

        lbl_title = QLabel(title)
        lbl_title.setObjectName("tabTitle")
        left_layout.addWidget(lbl_title, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if subtitle_label is not None:
            subtitle_label.setObjectName("tabStatusLabel")
            subtitle_label.setProperty("toolbarRole", "status")
            subtitle_label.setWordWrap(False)
            subtitle_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
            left_layout.addWidget(subtitle_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        title_row_layout.addWidget(left_wrap, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        if filter_widgets:
            filter_wrap = QWidget()
            filter_wrap.setObjectName("tabToolbarFilters")
            filter_layout = QHBoxLayout(filter_wrap)
            filter_layout.setContentsMargins(0, 0, 0, 0)
            filter_layout.setSpacing(tokens["shell"]["toolbar_group_gap"])
            filter_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            for w in filter_widgets:
                if w is None:
                    continue
                self._prepare_toolbar_widget(w)
                filter_layout.addWidget(w)
            title_row_layout.addWidget(filter_wrap, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        title_row_layout.addStretch(1)

        if action_widgets:
            action_wrap = QWidget()
            action_wrap.setObjectName("tabToolbarActions")
            action_layout = QHBoxLayout(action_wrap)
            action_layout.setContentsMargins(0, 0, 0, 0)
            action_layout.setSpacing(tokens["shell"]["toolbar_group_gap"])
            action_layout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            for w in action_widgets:
                if w is None:
                    continue
                self._prepare_toolbar_widget(w)
                action_layout.addWidget(w)
            self._equalize_toolbar_action_widths(action_widgets)
            title_row_layout.addWidget(action_wrap, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        tb_layout.addWidget(title_row)

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
        if current_model:
            self._active_model_ref = current_model

        model = self._resolve_active_quote_model()

        # 1. 尝试从 Redux Store 读取市场快照，实现秒刷 (无感知切图)
        if model and hasattr(model, 'update_quotes'):
            from core.global_store import global_store
            snapshot = global_store.get_latest_quotes()
            if snapshot:
                if self.isVisible():
                    model.update_quotes(snapshot)
                else:
                    self._deferred_quote_refresh = True

        # 2. 为了防止多次绑定导致的连环触发，先断开(忽略不存在的情况)
        try:
            event_bus.sig_rt_quotes.disconnect(self._on_rt_quotes_direct)
        except (TypeError, RuntimeError):
            # Why: 信号从未连接过时 disconnect 报 TypeError，是正常情况
            pass

        event_bus.sig_rt_quotes.connect(self._on_rt_quotes_direct)

    def _on_rt_quotes_direct(self, quotes: dict):
        """v4 直达信号：实时行情广播，不再需要 if-elif 路由"""
        if not self.isVisible():
            self._deferred_quote_refresh = True
            return

        self._apply_quote_snapshot(quotes)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._deferred_quote_refresh:
            return
        self._deferred_quote_refresh = False
        try:
            from core.global_store import global_store

            self._apply_quote_snapshot(global_store.get_latest_quotes())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass



    def async_update_market_caps(self):
        """异步补齐缺失股本，并通过全局实时行情信号回灌动态市值。"""
        app = QCoreApplication.instance()
        owner_window = self.window()
        if app is None or app.closingDown():
            return
        if owner_window and getattr(owner_window, "_is_closing", False):
            return

        model = self._resolve_active_quote_model()
        if not model or not hasattr(model, "row_data"):
            return

        try:
            from core.global_store import global_store

            latest_quotes = global_store.get_latest_quotes() or {}
        except (AttributeError, RuntimeError, TypeError, ValueError):
            latest_quotes = {}

        if latest_quotes:
            self._apply_quote_snapshot(latest_quotes)

        codes_need_cap = self._collect_missing_finance_codes(model)
        if not codes_need_cap:
            after_cap_hook = getattr(self, "_after_market_caps_updated", None)
            if callable(after_cap_hook):
                try:
                    after_cap_hook()
                except (AttributeError, RuntimeError, TypeError):
                    pass
            return

        def _bg_cap():
            app_obj = QCoreApplication.instance()
            if app_obj is None or app_obj.closingDown():
                return {}

            from vcp.engine import VCPEngine

            try:
                return VCPEngine.batch_get_finance_info(codes_need_cap)
            except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
                import logging

                logging.getLogger(__name__).error(f"[市值统一刷新] 获取股本失败: {exc}")
                return {}

        def _on_cap(finance_data):
            app_obj = QCoreApplication.instance()
            owner = self.window()
            if app_obj is None or app_obj.closingDown():
                return
            if owner and getattr(owner, "_is_closing", False):
                return
            if not finance_data:
                return

            payload = build_finance_quote_payload(finance_data)
            if payload:
                published = self._publish_quote_payload(
                    payload,
                    source=f"{self.__class__.__name__}.finance",
                )
                self._apply_quote_snapshot(published or payload)

            after_cap_hook = getattr(self, "_after_market_caps_updated", None)
            if callable(after_cap_hook):
                try:
                    after_cap_hook()
                except (AttributeError, RuntimeError, TypeError):
                    pass

        from core.task_manager import task_manager

        task_manager.run_in_background(
            _bg_cap,
            task_id=f"caps_{self.__class__.__name__}",
            on_success=_on_cap,
        )
