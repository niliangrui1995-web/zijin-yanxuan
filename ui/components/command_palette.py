# -*- coding: utf-8 -*-
"""轻量命令面板。"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from ui.theme_tokens import build_ui_tokens


class CommandPaletteDialog(QDialog):
    def __init__(self, commands: list[dict] | None = None, parent=None):
        super().__init__(parent)
        self._commands: list[dict] = []
        self._dynamic_provider: Callable[[str], list[dict]] | None = None

        self.setObjectName("commandPaletteDialog")
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.resize(640, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.search_box = QLineEdit(self)
        self.search_box.setPlaceholderText("搜索页面、动作或股票代码/名称，例如：全局同步 / 关注池 / 600519")
        self.search_box.setAccessibleName("命令面板搜索")
        self.search_box.textChanged.connect(self._refresh_items)
        self.search_box.returnPressed.connect(self._trigger_current)
        layout.addWidget(self.search_box)

        self.hint_label = QLabel("Enter 执行｜Esc 关闭｜输入代码可直接打开 K 线", self)
        self.hint_label.setObjectName("commandPaletteHint")
        layout.addWidget(self.hint_label)

        self.list_widget = QListWidget(self)
        self.list_widget.setObjectName("commandPaletteList")
        self.list_widget.itemActivated.connect(self._execute_item)
        self.list_widget.itemDoubleClicked.connect(self._execute_item)
        layout.addWidget(self.list_widget, 1)

        self._apply_theme()
        self.set_commands(commands or [])

    def set_dynamic_provider(self, provider: Callable[[str], list[dict]] | None) -> None:
        self._dynamic_provider = provider
        self._refresh_items()

    def set_commands(self, commands: list[dict]) -> None:
        normalized = []
        for item in commands or []:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            normalized.append(
                {
                    "title": title,
                    "subtitle": str(item.get("subtitle") or "").strip(),
                    "keywords": [
                        str(keyword or "").strip().lower()
                        for keyword in item.get("keywords") or []
                        if str(keyword or "").strip()
                    ],
                    "shortcut": str(item.get("shortcut") or "").strip(),
                    "handler": item.get("handler"),
                }
            )
        self._commands = normalized
        self._refresh_items()

    def _apply_theme(self) -> None:
        tokens = build_ui_tokens()
        theme = tokens["theme"]
        surface = tokens["surface"]
        border = tokens["border"]
        text = tokens["text"]
        self.setStyleSheet(
            f"""
            QDialog#commandPaletteDialog {{
                background: {surface["overlay"]};
                border: 1px solid {border["strong"]};
                border-radius: {tokens["radius"]["xl"]}px;
            }}
            QLineEdit {{
                background: {surface["input"]};
                color: {text["primary"]};
                border: 1px solid {border["default"]};
                border-radius: {tokens["radius"]["lg"]}px;
                min-height: {tokens["control"]["toolbar_button_height"]}px;
                padding: 0 {tokens["space"]["lg"]}px;
            }}
            QLineEdit:focus {{
                border: 1px solid {border["focus"]};
            }}
            QLabel#commandPaletteHint {{
                color: {text["muted"]};
                font-size: {tokens["font"]["size_sm"]}px;
            }}
            QListWidget#commandPaletteList {{
                background: {surface["input"]};
                color: {text["primary"]};
                border: 1px solid {border["default"]};
                border-radius: {tokens["radius"]["lg"]}px;
                padding: {tokens["space"]["xs"]}px;
                outline: none;
            }}
            QListWidget#commandPaletteList::item {{
                border-radius: {tokens["radius"]["md"]}px;
                padding: {tokens["space"]["sm"]}px {tokens["space"]["md"]}px;
                margin: 2px 0;
                min-height: {max(32, tokens["control"]["button_height"])}px;
            }}
            QListWidget#commandPaletteList::item:selected {{
                background: {theme["SELECTION_BG"]};
                color: {text["primary"]};
            }}
            QListWidget#commandPaletteList::item:hover {{
                background: {surface["hover"]};
            }}
            """
        )

    def _matches_query(self, command: dict, query: str) -> bool:
        if not query:
            return True
        haystacks = [
            command.get("title", "").lower(),
            command.get("subtitle", "").lower(),
            *command.get("keywords", []),
        ]
        return any(query in haystack for haystack in haystacks if haystack)

    def _refresh_items(self) -> None:
        query = self.search_box.text().strip().lower()
        self.list_widget.clear()

        commands = list(self._commands)
        if query and callable(self._dynamic_provider):
            commands.extend(self._dynamic_provider(query) or [])

        seen_keys: set[tuple[str, str, str]] = set()
        for command in commands:
            dedupe_key = (
                str(command.get("title") or "").strip(),
                str(command.get("subtitle") or "").strip(),
                str(command.get("shortcut") or "").strip(),
            )
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            if not self._matches_query(command, query):
                continue
            title = command["title"]
            subtitle = command.get("subtitle", "")
            shortcut = command.get("shortcut", "")
            right_text = f"    {shortcut}" if shortcut else ""
            display_text = title
            if subtitle:
                display_text = f"{title}\n{subtitle}"
            item = QListWidgetItem(f"{display_text}{right_text}")
            item.setData(Qt.ItemDataRole.UserRole, command)
            tooltip_parts = [title]
            if subtitle:
                tooltip_parts.append(subtitle)
            if shortcut:
                tooltip_parts.append(shortcut)
            item.setToolTip("｜".join(tooltip_parts))
            self.list_widget.addItem(item)

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _trigger_current(self) -> None:
        item = self.list_widget.currentItem()
        if item is not None:
            self._execute_item(item)

    def _execute_item(self, item: QListWidgetItem) -> None:
        command = item.data(Qt.ItemDataRole.UserRole) or {}
        handler = command.get("handler")
        self.accept()
        if callable(handler):
            handler()

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            parent_rect = parent.geometry()
            geo = self.frameGeometry()
            geo.moveCenter(parent_rect.center())
            self.move(geo.topLeft())
        self.search_box.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.search_box.selectAll()
