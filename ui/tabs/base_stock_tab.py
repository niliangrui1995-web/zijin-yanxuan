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
from collections.abc import Mapping
from contextlib import suppress

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from app.services.ui_config_service import app_config
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_navigation_service import ExternalTerminalNavigator
from app.services.ui_quote_service import read_provider_health
from app.services.ui_task_lifecycle_service import shutdown_task_lifecycle_for_owner
from ui.status_registry import format_status_summary, format_workspace_status, parse_status_summary
from ui.tabs.base_stock_refresh import (
    _latest_quote_snapshot as latest_quote_snapshot,
)
from ui.tabs.base_stock_refresh import (
    _should_prime_f5_local_snapshot,
    cancel_workspace_background_snapshot,
    replay_deferred_quotes,
    workspace_background_snapshot_cancellation_settled,
    workspace_background_snapshot_complete,
)
from ui.tabs.base_stock_refresh import (
    async_update_market_caps as run_async_market_caps,
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
    prime_workspace_background_snapshot as warm_workspace_background_snapshot,
)
from ui.tabs.base_stock_refresh import (
    refresh_table_from_latest_snapshot as refresh_quotes_from_latest_snapshot,
)
from ui.tabs.base_stock_refresh import (
    refresh_table_quotes_and_market_caps as refresh_quotes_and_market_caps,
)
from ui.tabs.base_stock_refresh import (
    refresh_workspace_preloaded_snapshot as refresh_preloaded_quote_snapshot,
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
from ui.workspaces.tab_registry import is_interactive_tab_load_reason


def _compact_status_text(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)] + "…"


def _workspace_current_widget_match(container, owner, checked: set[int]) -> bool | None:
    if container is None or id(container) in checked:
        return None
    checked.add(id(container))
    try:
        workspace = getattr(container, "_workspace", None)
        candidates = (
            container,
            getattr(container, "tabs", None),
            workspace,
            getattr(workspace, "tabs", None) if workspace is not None else None,
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            current_widget = getattr(candidate, "currentWidget", None)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False
        if callable(current_widget):
            try:
                return current_widget() is owner
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return False
    return None


def _is_direct_workspace_tab(owner) -> bool:
    checked: set[int] = set()
    try:
        parent_getter = getattr(owner, "parent", None)
        parent = parent_getter() if callable(parent_getter) else None
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    while parent is not None:
        result = _workspace_current_widget_match(parent, owner, checked)
        if result is not None:
            return result
        try:
            parent_getter = getattr(parent, "parent", None)
            parent = parent_getter() if callable(parent_getter) else None
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False

    try:
        window_getter = getattr(owner, "window", None)
        window = window_getter() if callable(window_getter) else None
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    result = _workspace_current_widget_match(window, owner, checked)
    return True if result is None else result


def _show_kline_from_proxy_index(owner, index, signal_hub, *, require_code: bool = False):
    if not index.isValid():
        return
    source_idx = owner.proxy_model.mapToSource(index)
    row = source_idx.row()
    if row >= len(owner.model.row_data):
        return

    code = owner.model.row_data[row].get("代码", "")
    if require_code and not code:
        return
    code_list = []
    clicked_visual_row = index.row()
    for visual_row in range(owner.proxy_model.rowCount()):
        source_idx = owner.proxy_model.mapToSource(owner.proxy_model.index(visual_row, 0))
        if source_idx.row() < len(owner.model.row_data):
            row_data = dict(owner.model.row_data[source_idx.row()] or {})
            row_data.setdefault("代码", row_data.get("代码", ""))
            row_data.setdefault("名称", row_data.get("名称", ""))
            code_list.append(row_data)

    current_idx = clicked_visual_row if 0 <= clicked_visual_row < len(code_list) else 0
    signal_hub.sig_show_kline_with_list.emit(code, code_list, current_idx)


def _show_stock_context_menu_from_proxy_index(owner, pos):
    index = owner.table.indexAt(pos)
    if not index.isValid():
        return
    source_idx = owner.proxy_model.mapToSource(index)
    row = source_idx.row()
    if row >= len(owner.model.row_data):
        return
    row_data = owner.model.row_data[row]
    code = row_data.get("代码", "")
    if not code:
        return
    from ui.components.stock_context_menu import build_stock_context_menu

    build_stock_context_menu(owner, code, row_data.get("名称", ""), vcp_data=row_data)


class ToolbarStatusChipBar(QWidget):
    """Compact semantic status chips for dense table toolbars."""

    MAX_SEGMENTS = 5

    def __init__(self, source_label: QLabel, parent=None):
        super().__init__(parent)
        self._source_label = source_label
        self.setObjectName("tabStatusChipBar")
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._layout = layout

        self._primary = QLabel("")
        self._primary.setObjectName("tabStatusPrimaryChip")
        self._primary.setWordWrap(False)
        self._primary.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._primary, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._chips: list[QLabel] = []
        for _ in range(self.MAX_SEGMENTS):
            chip = QLabel("")
            chip.setObjectName("tabStatusChip")
            chip.setWordWrap(False)
            chip.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._chips.append(chip)

        self.set_status_text(source_label.text())

    def set_status_text(self, text: str) -> None:
        summary = parse_status_summary(text)
        primary = str(summary.get("primary") or "").strip()
        segments = list(summary.get("segments") or [])
        full_text = str(text or "").strip()

        self._primary.setText(_compact_status_text(primary, 14))
        self._primary.setToolTip(full_text or primary)
        self._primary.setVisible(bool(primary))

        visible_segments = segments[: self.MAX_SEGMENTS]
        overflow_count = max(0, len(segments) - self.MAX_SEGMENTS)
        for idx, chip in enumerate(self._chips):
            if idx >= len(visible_segments):
                chip.setVisible(False)
                continue

            segment = visible_segments[idx]
            label = str(segment.get("label") or "").strip()
            value = str(segment.get("value") or "").strip()
            raw = str(segment.get("raw") or "").strip()
            if overflow_count and idx == self.MAX_SEGMENTS - 1:
                chip_text = f"+{overflow_count + 1}"
            elif label:
                chip_text = f"{label} {_compact_status_text(value, 12)}"
            else:
                chip_text = _compact_status_text(value, 14)

            chip.setText(chip_text)
            chip.setToolTip(raw or full_text)
            chip.setVisible(True)


class _ProviderHealthMixin:
    def _read_provider_status(self) -> dict:
        return read_provider_health(self.data_provider).as_dict()


class _WorkspaceBackgroundSnapshotMixin:
    def prime_workspace_background_snapshot(self) -> bool:
        return warm_workspace_background_snapshot(self)

    def is_workspace_background_snapshot_complete(self) -> bool:
        return workspace_background_snapshot_complete(self)

    def _cancel_workspace_background_snapshot_preload(self) -> None:
        cancel_workspace_background_snapshot(self)

    def _workspace_background_snapshot_preload_settled(self) -> bool:
        return workspace_background_snapshot_cancellation_settled(self)


def mark_runtime_network_activity(owner) -> None:
    owner._runtime_network_triggered = True


class BaseStockTab(_WorkspaceBackgroundSnapshotMixin, _ProviderHealthMixin, QWidget):
    """股票列表 Tab 基类 - 提供通用方法"""
    _TABLE_ATTR_CANDIDATES = ("table_sp", "table_scan", "table_rt", "na_daily_table", "asian_table", "table")

    def __init__(self, data_provider=None, parent=None):
        super().__init__(parent)
        self.data_provider = data_provider
        self._deferred_quote_refresh = False
        self._workspace_active = False
        self._hidden_quote_projection_primed = False
        self._missing_quote_publisher_warned = False
        self._header_state_savers = []
        self._quote_terminal_launcher = ExternalTerminalNavigator(self)
        self._runtime_cleanup_done = False
        self._runtime_network_triggered = False
        event_bus.sig_app_closing.connect(self._flush_header_persistence)

    def _is_current_workspace_tab(self) -> bool:
        return _is_direct_workspace_tab(self)

    def set_workspace_active(self, active: bool) -> None:
        """Receive the workspace's logical-active lifecycle independently of QWidget visibility."""
        self._workspace_active = bool(active)

    def accepts_hidden_quote_projection(self) -> bool:
        """Whether quote data may update this tab's model while its page is hidden."""
        return bool(getattr(self, "_hidden_quote_projection_enabled", False))

    @staticmethod
    def _accepts_hidden_quote_projection(owner) -> bool:
        checker = getattr(owner, "accepts_hidden_quote_projection", None)
        if callable(checker):
            try:
                return bool(checker())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return False
        return bool(getattr(owner, "_hidden_quote_projection_enabled", False))

    def _should_start_interactive_runtime_on_show(self) -> bool:
        is_current = self._is_current_workspace_tab()
        reason = str(getattr(self, "_workspace_load_reason", "") or "").strip()
        noninteractive_loaded = bool(getattr(self, "_workspace_noninteractive_loaded", False))

        if noninteractive_loaded:
            if not is_current:
                return False
            if reason and not is_interactive_tab_load_reason(reason):
                return False
            setattr(self, "_workspace_noninteractive_loaded", False)
            return True

        if reason and not is_interactive_tab_load_reason(reason):
            return False
        return is_current

    _should_start_runtime_on_show = _should_start_interactive_runtime_on_show

    def _resolve_active_quote_model(self):
        return resolve_active_quote_model(self)

    def _apply_quote_snapshot(
        self,
        quotes: Mapping[str, Mapping[str, object]] | None,
        *,
        record_flash: bool = True,
    ):
        return apply_quote_snapshot(self, quotes, record_flash=record_flash)

    def _publish_quote_payload(self, payload, *, source: str, require_valid: bool = False) -> dict:
        return publish_quote_payload(self, payload, source=source, require_valid=require_valid)

    @staticmethod
    def _is_blank_quote_value(value, zero_is_blank=True) -> bool:
        text = "" if value is None else str(value).strip()
        if text in {"", "--"}:
            return True
        return bool(zero_is_blank and text in {"0", "0.0", "0.00"})

    def _collect_table_codes(self, current_model=None) -> list[str]:
        return collect_refresh_table_codes(self, current_model)

    def _collect_quote_refresh_codes(self, current_model=None, force=False) -> list[str]:
        return collect_refresh_quote_codes(self, current_model, force=force)

    def refresh_table_quotes_and_market_caps(
        self,
        current_model=None,
        force_quotes=False,
        quote_task_id=None,
        *,
        async_local: bool = False,
    ):
        refresh_quotes_and_market_caps(
            self,
            current_model=current_model,
            force_quotes=force_quotes,
            quote_task_id=quote_task_id,
            async_local=async_local,
        )

    def prime_local_quote_snapshot(self, current_model=None):
        return warm_local_quote_snapshot(self, current_model=current_model)

    def refresh_table_from_latest_snapshot(
        self,
        current_model=None,
        *,
        async_local: bool = True,
        prime_local: bool = True,
    ):
        refresh_quotes_from_latest_snapshot(
            self,
            current_model=current_model,
            async_local=async_local,
            prime_local=prime_local,
        )

    def _prime_visible_local_quote_snapshot(self, current_model=None) -> bool:
        if getattr(self, "_runtime_cleanup_done", False):
            return False
        if getattr(self, "_workspace_noninteractive_loaded", False):
            return False
        reason = str(getattr(self, "_workspace_load_reason", "") or "").strip()
        if reason and not is_interactive_tab_load_reason(reason):
            return False
        accepts_hidden_projection = BaseStockTab._accepts_hidden_quote_projection(self)
        if accepts_hidden_projection and bool(getattr(self, "_hidden_quote_projection_primed", False)):
            return False
        visible = self.isVisible()
        if not visible and not _should_prime_f5_local_snapshot(self, current_model):
            return False
        if visible and getattr(self, "_workspace_background_snapshot_ready", False):
            refresh_preloaded_quote_snapshot(self, current_model=current_model)
            return True
        self.refresh_table_from_latest_snapshot(current_model=current_model, async_local=True)
        if accepts_hidden_projection:
            self._hidden_quote_projection_primed = True
        return True

    def prime_hidden_quote_projection(self, current_model=None) -> bool:
        """Refresh an opt-in hidden model before it is revealed again."""
        try:
            runtime_cleanup_done = bool(getattr(self, "_runtime_cleanup_done", False))
        except RuntimeError:
            return False
        if runtime_cleanup_done or not BaseStockTab._accepts_hidden_quote_projection(self):
            return False
        try:
            self.refresh_table_from_latest_snapshot(
                current_model=current_model,
                async_local=True,
                prime_local=False,
            )
        except TypeError:
            # Preserve compatibility with lightweight tab doubles and legacy
            # subclasses while production Watchlist uses the no-IO path.
            self.refresh_table_from_latest_snapshot(current_model=current_model, async_local=True)
        self._hidden_quote_projection_primed = True
        return True

    def prepare_workspace_reveal(self) -> bool:
        """Apply the latest hidden quote projection before this page is shown."""
        return self.prime_hidden_quote_projection()

    def _apply_quote_store_snapshot(self, current_model=None, *, record_flash: bool | None = None):
        if current_model is not None:
            self._active_model_ref = current_model

        model = current_model or self._resolve_active_quote_model()
        if not model or not hasattr(model, "row_data"):
            return

        codes = self._collect_table_codes(model)
        if not codes:
            return

        snapshot = latest_quote_snapshot()

        quote_subset = {code: dict(snapshot[code]) for code in codes if code in snapshot}
        if quote_subset:
            if record_flash is None:
                record_flash = True
            if record_flash and BaseStockTab._accepts_hidden_quote_projection(self):
                try:
                    record_flash = bool(self._workspace_active and self.isVisible())
                except RuntimeError:
                    record_flash = False
            # ``record_flash=True`` is the long-standing default.  Keep that
            # call shape compatible with existing tab overrides, while the
            # explicit silent projection path opts into the extended contract.
            if record_flash:
                self._apply_quote_snapshot(quote_subset)
            else:
                self._apply_quote_snapshot(quote_subset, record_flash=False)

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

    def get_f5_off_market_quote_codes(self, current_model=None) -> set[str]:
        codes: set[str] = set()
        for row in self.get_row_data(current_model=current_model):
            code = str(row.get("代码", "")).strip()
            if len(code) == 6 and code.isdigit():
                codes.add(code)
        return codes

    def iter_tables(self) -> list:
        tables = []
        for attr_name in self._TABLE_ATTR_CANDIDATES:
            table = getattr(self, attr_name, None)
            if table is not None and hasattr(table, "model") and table not in tables:
                tables.append(table)
        return tables

    def prepare_workspace_preload_reveal(self) -> None:
        for table in self.iter_tables():
            prepare = getattr(table, "prepare_background_preload_reveal", None)
            if callable(prepare):
                prepare()

    def prepare_workspace_preload_repaint_guard(self, *, load_reason: str) -> None:
        """Arm the hidden-staged page's bounded post-reveal paint-tail guard."""
        if not bool(getattr(self, "_workspace_preload_staged", False)):
            return
        for table in self.iter_tables():
            prepare = getattr(table, "prepare_workspace_preload_repaint_guard", None)
            if callable(prepare):
                prepare(load_reason=load_reason)

    def sync_workspace_viewport_background(self) -> None:
        for table in self.iter_tables():
            sync = getattr(table, "sync_viewport_base_background", None)
            if callable(sync):
                sync()

    def get_primary_table(self):
        tables = self.iter_tables()
        return tables[0] if tables else None

    def select_primary_row(self, index: int) -> bool:
        table = self.get_primary_table()
        if table is None or int(index) < 0:
            return False
        try:
            table.selectRow(int(index))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _find_code_column(model) -> int:
        if model is None:
            return -1
        try:
            column_count = int(model.columnCount())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return -1

        for column in range(column_count):
            try:
                header_text = str(
                    model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or ""
                ).strip()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                header_text = ""
            if header_text == "代码":
                return column
        return -1

    def select_code_row(self, code: str) -> bool:
        code_text = str(code or "").strip()
        if not code_text:
            return False

        for table in self.iter_tables():
            try:
                model = table.model()
            except (AttributeError, RuntimeError, TypeError):
                continue
            code_column = self._find_code_column(model)
            if code_column < 0:
                continue

            try:
                row_count = int(model.rowCount())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue

            for row in range(row_count):
                try:
                    index = model.index(row, code_column)
                    row_code = str(model.data(index, Qt.ItemDataRole.DisplayRole) or "").strip()
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    continue
                if row_code != code_text:
                    continue

                try:
                    table.clearSelection()
                    table.setCurrentIndex(index)
                    table.selectRow(row)
                    table.scrollTo(index)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    return False
                return True

        return False

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
            widget.setCursor(Qt.CursorShape.PointingHandCursor)
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

    @staticmethod
    def _toolbar_action_label(widget: QWidget) -> str:
        text = ""
        if hasattr(widget, "text"):
            try:
                text = str(widget.text() or "").strip()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                text = ""
        if not text:
            text = str(widget.toolTip() or "").strip()
        if not text:
            text = str(widget.accessibleName() or "").strip()
        return text or "操作"

    def _build_toolbar_overflow_button(self, widgets: list[QWidget]) -> QToolButton | None:
        overflow_widgets = [widget for widget in widgets if widget is not None]
        if len(overflow_widgets) <= 1:
            return None

        menu = QMenu(self)
        action_pairs = []
        for widget in overflow_widgets:
            label = self._toolbar_action_label(widget)
            action = QAction(label, menu)
            action.setEnabled(widget.isEnabled())
            action.triggered.connect(lambda _checked=False, target=widget: target.click())
            menu.addAction(action)
            action_pairs.append((action, widget))
            widget.setVisible(False)

        def _refresh_menu_actions() -> None:
            for action, widget in action_pairs:
                action.setText(self._toolbar_action_label(widget))
                action.setEnabled(widget.isEnabled())

        menu.aboutToShow.connect(_refresh_menu_actions)

        button = QToolButton()
        button.setText("更多")
        button.setToolTip("更多操作")
        button.setAccessibleName("更多操作")
        button.setProperty("class", "toolbarGhost")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setMenu(menu)
        button.setMinimumWidth(56)
        return button

    def _split_toolbar_actions(self, action_widgets: list[QWidget] | None) -> list[QWidget]:
        widgets = [widget for widget in (action_widgets or []) if widget is not None]
        if len(widgets) <= 2:
            return widgets

        visible: list[QWidget] = []
        overflow: list[QWidget] = []
        visible_buttons = 0
        for widget in widgets:
            explicit_overflow = bool(widget.property("toolbarOverflow"))
            is_input = isinstance(widget, QLineEdit)
            is_primary = str(widget.objectName() or "") == "primaryButton"
            if explicit_overflow:
                overflow.append(widget)
                continue
            if is_input or is_primary:
                visible.append(widget)
                if not is_input:
                    visible_buttons += 1
                continue
            if visible_buttons < 2:
                visible.append(widget)
                visible_buttons += 1
            else:
                overflow.append(widget)

        overflow_button = self._build_toolbar_overflow_button(overflow)
        if overflow_button is not None:
            visible.append(overflow_button)
        elif len(overflow) == 1:
            overflow[0].setVisible(True)
            visible.append(overflow[0])
        return visible

    def apply_table_column_preset(
        self,
        table,
        widths: list[int] | tuple[int, ...],
        *,
        stretch_last: bool = True,
        min_width: int = 56,
    ) -> None:
        header = table.horizontalHeader()
        try:
            column_count = int(table.model().columnCount())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            column_count = len(widths or [])

        header.setMinimumSectionSize(max(36, int(min_width)))
        header.setStretchLastSection(bool(stretch_last))
        for col_idx, width in enumerate(widths or []):
            if col_idx >= column_count:
                break
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            table.setColumnWidth(col_idx, max(min_width, int(width)))

    def _build_toolbar_flow_group(
        self,
        object_name: str,
        widgets: list[QWidget] | None,
        *,
        h_spacing: int | None = None,
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

    def set_proxy_filter_text(self, proxy_model, text: str, *, debounce_ms: int = 90):
        if proxy_model is None:
            return

        if debounce_ms <= 0 or not self.isVisible():
            proxy_model.setFilterText(text)
            return

        timers = getattr(self, "_proxy_filter_timers", None)
        if timers is None:
            timers = {}
            self._proxy_filter_timers = timers

        pending = getattr(self, "_proxy_filter_pending", None)
        if pending is None:
            pending = {}
            self._proxy_filter_pending = pending

        key = id(proxy_model)
        pending[key] = text
        timer = timers.get(key)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)

            def _flush_filter(proxy=proxy_model, proxy_key=key):
                value = getattr(self, "_proxy_filter_pending", {}).pop(proxy_key, "")
                proxy.setFilterText(value)

            timer.timeout.connect(_flush_filter)
            timers[key] = timer
        timer.start(max(0, int(debounce_ms)))

    @staticmethod
    def _bind_toolbar_status_label(source_label: QLabel, chip_bar: ToolbarStatusChipBar) -> None:
        original_set_text = source_label.setText

        def _set_text(text):
            original_set_text(text)
            chip_bar.set_status_text(source_label.text())

        source_label.setText = _set_text
        chip_bar.set_status_text(source_label.text())

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
    def build_tab_toolbar(
        self,
        title: str,
        subtitle_label: QLabel | None,
        filter_widgets: list[QWidget] | None,
        action_widgets: list[QWidget] | None,
    ) -> QWidget:
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
            subtitle_label.setParent(left_wrap)
            subtitle_label.setVisible(False)
            status_chips = ToolbarStatusChipBar(subtitle_label, left_wrap)
            self._bind_toolbar_status_label(subtitle_label, status_chips)
            left_layout.addWidget(status_chips, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

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

        action_widgets = self._split_toolbar_actions(action_widgets)
        action_wrap = self._build_toolbar_flow_group(
            "tabToolbarActions",
            action_widgets,
            h_spacing=max(6, tokens["shell"]["toolbar_group_gap"] + 2),
        )
        if action_widgets:
            self._equalize_toolbar_action_widths(action_widgets)
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

    def _cleanup_runtime_state(self):
        if getattr(self, "_runtime_cleanup_done", False):
            return
        self._runtime_cleanup_done = True
        shutdown_task_lifecycle_for_owner(self, timeout_ms=750)
        self._flush_header_persistence()

        for timer in getattr(self, "_header_save_timers", []) or []:
            with suppress(AttributeError, RuntimeError, TypeError, ValueError):
                timer.stop()

        proxy_filter_timers = getattr(self, "_proxy_filter_timers", {}) or {}
        if hasattr(proxy_filter_timers, "values"):
            proxy_filter_timers = list(proxy_filter_timers.values())
        for timer in proxy_filter_timers:
            with suppress(AttributeError, RuntimeError, TypeError, ValueError):
                timer.stop()

        with suppress(TypeError, RuntimeError):
            event_bus.sig_app_closing.disconnect(self._flush_header_persistence)

        if getattr(self, "_quote_signal_connected", False):
            with suppress(TypeError, RuntimeError):
                event_bus.sig_rt_quotes.disconnect(self._on_rt_quotes_direct)
            self._quote_signal_connected = False

    def closeEvent(self, event):
        self._cleanup_runtime_state()
        super().closeEvent(event)

    def deleteLater(self):
        self._cleanup_runtime_state()
        super().deleteLater()

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

    def _on_rt_quotes_direct(self, quotes: Mapping[str, Mapping[str, object]]):
        """v4 直达信号：实时行情广播，不再需要 if-elif 路由"""
        apply_rt_quotes_direct(self, quotes)

    def showEvent(self, event):
        super().showEvent(event)
        self._should_start_interactive_runtime_on_show()
        if not getattr(self, "_workspace_background_snapshot_ready", False):
            replay_deferred_quotes(self)
        self._prime_visible_local_quote_snapshot()

    def async_update_market_caps(self):
        """异步补齐缺失股本，并通过共享批次去重后回灌动态市值。"""
        run_async_market_caps(self)
