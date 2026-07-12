# -*- coding: utf-8 -*-
"""Public host hooks consumed by application bootstrap adapters."""

from __future__ import annotations

from typing import Any, cast


class MainWindowHostPortMixin:
    """Expose stable host methods while keeping Qt implementation details local."""

    def call_in_ui(self, callback) -> None:
        cast(Any, self)._call_in_ui(callback)

    def is_closing(self) -> bool:
        return bool(getattr(self, "_is_closing", False))

    def current_workspace(self):
        return getattr(self, "_workspace", None)

    def get_realtime_quote_codes(self) -> set[str]:
        workspace = self.current_workspace()
        supplier = getattr(workspace, "get_realtime_quote_codes", None)
        return set(cast(Any, supplier)() or ()) if callable(supplier) else set()

    def refresh_code_count_label_from_provider(self) -> int:
        return int(cast(Any, self)._refresh_code_count_label_from_provider())

    def set_titlebar_sync_state(self, state: str, detail: str = "", freshness: str = "") -> None:
        cast(Any, self)._set_titlebar_sync_state(state, detail, freshness)

    def update_network_ui(self, online: bool, detail: str = "") -> None:
        cast(Any, self)._update_network_ui(online, detail)

    def on_smart_startup_online_done(self) -> None:
        cast(Any, self)._on_smart_startup_online_done()


__all__ = ["MainWindowHostPortMixin"]
