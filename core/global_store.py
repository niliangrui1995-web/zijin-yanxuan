# -*- coding: utf-8 -*-
from PyQt6.QtCore import QObject, pyqtSignal

from core.event_bus import event_bus
from core.quote_snapshot import get_missing_a_share_finance_codes, merge_quote_snapshot_inplace


class GlobalStore(QObject):
    """Process-wide lightweight snapshot store."""

    sig_state_changed = pyqtSignal(str, object)

    def __init__(self):
        super().__init__()
        self.state = {
            "quotes": {},
            "watchlist": [],
        }
        self._bind_events()

    def _bind_events(self):
        event_bus.sig_rt_quotes.connect(self._on_rt_quotes)

    def _on_rt_quotes(self, data: dict):
        if isinstance(data, dict):
            merge_quote_snapshot_inplace(self.state["quotes"], data)

    def get_latest_quotes(self) -> dict:
        return self.state["quotes"]

    def get_missing_a_share_finance_codes(self, codes) -> list[str]:
        return get_missing_a_share_finance_codes(codes, self.state["quotes"])

    def reset_quotes(self):
        self.state["quotes"].clear()

    def reset_runtime_state(self):
        self.reset_quotes()
        self.state["watchlist"] = []


global_store = GlobalStore()
