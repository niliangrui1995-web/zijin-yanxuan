# -*- coding: utf-8 -*-
from __future__ import annotations

from ui.workspaces.tab_capabilities import QuoteUniverseCapability

INFO_SOURCE_GROUP = "情报源"
NON_A_SHARE_REALTIME_TAB_KEYS = frozenset({"asian_market"})
DEFAULT_REALTIME_TAB_KEYS = (
    "scan",
    "rt_monitor",
    "watchlist",
    "stock_candidates",
    "foreign_block",
    "na_daily",
    "ai_industry_chain",
    "earnings",
    "lhb",
)


class QuoteUniverseService:
    """汇总工作区内需要订阅实时行情的 A 股代码集合。"""

    def __init__(self, workspace):
        self._workspace = workspace

    def _tab_specs(self) -> list[dict]:
        workspace = self._workspace
        specs = getattr(workspace, "_tab_specs", None)
        if specs is not None:
            return list(specs)
        tab_specs = getattr(workspace, "tab_specs", None)
        return list(tab_specs() or []) if callable(tab_specs) else []

    def _realtime_tab_keys(self) -> tuple[str, ...]:
        specs = self._tab_specs()
        if not specs:
            return DEFAULT_REALTIME_TAB_KEYS

        keys = []
        for spec in specs:
            key = str(spec.get("key", "")).strip()
            group = str(spec.get("group", "")).strip()
            if key and group != INFO_SOURCE_GROUP and key not in NON_A_SHARE_REALTIME_TAB_KEYS:
                keys.append(key)
        return tuple(keys)

    def collect_realtime_quote_codes(self) -> set[str]:
        workspace = self._workspace
        codes: set[str] = set()
        get_loaded_tab = getattr(workspace, "get_loaded_tab", None)
        get_tab = getattr(workspace, "get_tab", None)
        for key in self._realtime_tab_keys():
            if callable(get_loaded_tab):
                tab = get_loaded_tab(key)
            else:
                tab = get_tab(key) if callable(get_tab) else None
            if isinstance(tab, QuoteUniverseCapability):
                codes.update(tab.get_realtime_quote_codes())

        return codes
