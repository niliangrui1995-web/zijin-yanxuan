# -*- coding: utf-8 -*-
from __future__ import annotations

from ui.workspaces.tab_capabilities import QuoteUniverseCapability


class QuoteUniverseService:
    """汇总工作区内需要订阅实时行情的 A 股代码集合。"""

    def __init__(self, workspace):
        self._workspace = workspace

    def collect_realtime_quote_codes(self) -> set[str]:
        workspace = self._workspace
        codes: set[str] = set()
        get_tab = getattr(workspace, "get_tab", None)
        tab_keys = (
            "scan",
            "rt_monitor",
            "watchlist",
            "foreign_block",
            "na_daily",
            "earnings",
            "lhb",
        )
        for key in tab_keys:
            tab = get_tab(key) if callable(get_tab) else None
            if isinstance(tab, QuoteUniverseCapability):
                codes.update(tab.get_realtime_quote_codes())

        return codes
