# -*- coding: utf-8 -*-
from collections.abc import Mapping

from PyQt6.QtCore import QObject, pyqtSignal

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

    def merge_quotes(self, data: Mapping | None):
        if not isinstance(data, Mapping):
            return
        merge_quote_snapshot_inplace(self.state["quotes"], dict(data))

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
