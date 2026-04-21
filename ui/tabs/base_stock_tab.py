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

from core.app_config import app_config
from core.domain_events import domain_events as event_bus
from infra.navigation import ExternalTerminalNavigator
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
from ui.tabs.tab_quote_bridge import (
    apply_quote_snapshot,
    publish_quote_payload,
    resolve_active_quote_model,
)
from ui.tabs.table_view_state_binding import bind_table_view_state
from ui.theme_tokens import build_ui_tokens


class BaseStockTab(QWidget):
    """股票列表 Tab 基类 - 提供通用方法"""

    def __init__(self, data_provider=None, parent=None):
        super().__init__(parent)
        self.data_provider = data_provider
        self._deferred_quote_refresh = False
        self._missing_quote_publisher_warned = False
        self._header_state_savers = []
        self._quote_terminal_launcher = ExternalTerminalNavigator(self)
        event_bus.sig_app_closing.connect(self._flush_header_persistence)

    def _resolve_active_quote_model(self):
        return resolve_active_quote_model(self)

    def _apply_quote_snapshot(self, quotes: dict | None):
        apply_quote_snapshot(self, quotes)

    def _publish_quote_payload(self, payload, *, source: str, require_valid: bool = False) -> dict:
        return publish_quote_payload(self, payload, source=source, require_valid=require_valid)

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

    def get_row_data(self, current_model=None) -> list[dict]:
        model = current_model or self._resolve_active_quote_model()
        row_data = getattr(model, "row_data", None) or []
        return [row for row in row_data if isinstance(row, dict)]

    def get_realtime_quote_codes(self, current_model=None) -> set[str]:
        codes: set[str] = set()
        for row in self.get_row_data(current_model=current_model):
            code = str(row.get("代码", "")).strip()
            if len(code) == 6 and code.isdigit():
                codes.add(code)
        return codes

    @staticmethod
    def _prepare_toolbar_widget(widget: QWidget | None):
        if widget is None:
            return
        widget.setProperty("inToolbar", True)
        if isinstance(widget, QLabel) and widget.property("toolbarRole") is None:
            widget.setProperty("toolbarRole", "meta")
        if isinstance(widget, QToolButton) and widget.property("class") is None:
            widget.setProperty("class", "toolbarGhost")
        if isinstance(widget, QLineEdit):
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if widget.minimumWidth() == widget.maximumWidth() and widget.maximumWidth() > 0:
                preferred_width = widget.maximumWidth()
                widget.setMinimumWidth(max(150, preferred_width - 20))
                widget.setMaximumWidth(max(260, preferred_width + 80))
            if widget.minimumWidth() < 150:
                widget.setMinimumWidth(150)
        elif isinstance(widget, (QPushButton, QToolButton)):
            widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

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
            stretch = 1 if isinstance(widget, QLineEdit) else 0
            group_layout.addWidget(widget, stretch, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

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
            subtitle_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            left_layout.addWidget(subtitle_label, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        tb_layout.addWidget(left_wrap, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        for widget in filter_widgets or []:
            self._install_search_escape_behavior(widget)

        filter_wrap = self._build_toolbar_flow_group(
            "tabToolbarFilters",
            filter_widgets,
            h_spacing=max(6, tokens["shell"]["toolbar_group_gap"] + 2),
        )
        if filter_wrap is not None:
            filter_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            tb_layout.addWidget(filter_wrap, 1, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        else:
            tb_layout.addStretch(1)

        if action_widgets:
            self._equalize_toolbar_action_widths(action_widgets)
        action_wrap = self._build_toolbar_flow_group(
            "tabToolbarActions",
            action_widgets,
            h_spacing=max(6, tokens["shell"]["toolbar_group_gap"] + 2),
        )
        if action_wrap is not None:
            action_wrap.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            tb_layout.addWidget(action_wrap, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        return toolbar

    @staticmethod
    def _normalize_quote_code(code: str) -> str:
        return ExternalTerminalNavigator._normalize_quote_code(code)

    @classmethod
    def _detect_quote_prefix(cls, code: str) -> str:
        return ExternalTerminalNavigator._detect_quote_prefix(code)

    def launch_tdx(self, code: str):
        """跳转通达信并输入股票代码（后台线程执行，不阻塞 UI）"""
        self._quote_terminal_launcher.launch_tdx(code)

    def launch_eastmoney(self, code: str):
        """跳转东方财富并输入股票代码（后台线程执行，不阻塞 UI）"""
        self._quote_terminal_launcher.launch_eastmoney(code)

    def _flush_header_persistence(self):
        for saver in getattr(self, "_header_state_savers", []) or []:
            try:
                saver()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logging.getLogger(__name__).debug(f"表格状态关闭落盘失败: {exc}")

    def _settings_section(self):
        return app_config.section(
            f"tabs/{self.__class__.__name__}",
            legacy_scope=self.__class__.__name__,
        )

    def bind_header_persistence(self, table, settings_key: str = "header_state") -> bool:
        """通用：绑定表格列宽/列顺序/排序状态自动保存，并恢复上次保存的视图状态"""
        settings = self._settings_section()
        return bind_table_view_state(
            self,
            table,
            settings,
            self._header_state_savers,
            settings_key=settings_key,
        )

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
