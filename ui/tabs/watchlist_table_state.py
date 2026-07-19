"""Watchlist-specific lazy table-state container.

The Watchlist normally receives its SQLite snapshot before the delayed loading
state is revealed.  Building the animated empty/loading card eagerly therefore
adds widgets and style work to every first open even when the card is never
shown.  This adapter preserves ``TableStateWrapper``'s public behavior while
creating that overlay only when a state view is actually requested.
"""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QStackedLayout, QTableView, QWidget

from ui.components.table_controls import TableStateOverlay, TableStateWrapper


class LazyWatchlistTableStateWrapper(TableStateWrapper):
    """Render the table immediately and defer the optional state overlay."""

    def __init__(self, table: QTableView, empty_title: str = "暂无数据", loading_title: str = "加载中..."):
        QWidget.__init__(self, table.parent())
        self._table = table
        self._empty_title = empty_title
        self._loading_title = loading_title
        self._state_animation = None
        self._overlay: TableStateOverlay | None = None

        stack = QStackedLayout(self)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.addWidget(table)
        self._stack = stack

    def _ensure_overlay(self) -> TableStateOverlay:
        overlay = self._overlay
        if overlay is None:
            overlay = TableStateOverlay(self)
            self._stack.addWidget(overlay)
            self._overlay = overlay
        return overlay

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API naming
        if self._overlay is None:
            return self._table.sizeHint()
        return super().sizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API naming
        if self._overlay is None:
            return self._table.minimumSizeHint()
        return super().minimumSizeHint()

    def show_empty(self, title: str | None = None, subtitle: str = ""):
        self._show_state("empty", title or self._empty_title, subtitle)

    def show_loading(self, title: str | None = None, subtitle: str = ""):
        self._show_state("loading", title or self._loading_title, subtitle)

    def show_offline(self, title: str = "离线模式", subtitle: str = ""):
        self._show_state("offline", title, subtitle)

    def show_error(
        self,
        title: str = "加载失败",
        subtitle: str = "",
        *,
        meta: str = "",
        action_text: str = "",
        action_callback=None,
    ):
        self._show_state(
            "error",
            title,
            subtitle,
            meta=meta,
            action_text=action_text,
            action_callback=action_callback,
        )

    def show_cached(
        self,
        title: str = "显示缓存数据",
        subtitle: str = "",
        *,
        meta: str = "",
        action_text: str = "",
        action_callback=None,
    ):
        self._show_state(
            "cached",
            title,
            subtitle,
            meta=meta,
            action_text=action_text,
            action_callback=action_callback,
        )

    def show_success(self, title: str = "更新完成", subtitle: str = "", *, meta: str = ""):
        self._show_state("success", title, subtitle, meta=meta)

    def show_info(self, title: str = "状态更新", subtitle: str = "", *, meta: str = ""):
        self._show_state("info", title, subtitle, meta=meta)

    def _show_state(
        self,
        mode: str,
        title: str,
        subtitle: str,
        *,
        meta: str = "",
        action_text: str = "",
        action_callback=None,
    ) -> None:
        overlay = self._ensure_overlay()
        overlay.set_state(
            mode,
            title,
            subtitle,
            meta=meta,
            action_text=action_text,
            action_callback=action_callback,
        )
        self._set_current_widget(overlay)
