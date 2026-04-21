# -*- coding: utf-8 -*-
from __future__ import annotations


class QuoteUniverseService:
    """汇总工作区内需要订阅实时行情的 A 股代码集合。"""

    def __init__(self, workspace):
        self._workspace = workspace

    @staticmethod
    def _extract_a_share_codes(model_data) -> set[str]:
        codes: set[str] = set()
        for row in model_data or []:
            code = str((row or {}).get("代码", "")).strip()
            if len(code) == 6 and code.isdigit():
                codes.add(code)
        return codes

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
            collect_codes = getattr(tab, "get_realtime_quote_codes", None)
            if callable(collect_codes):
                codes.update(collect_codes())

        return codes
