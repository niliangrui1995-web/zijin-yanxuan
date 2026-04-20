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
        extract_codes = self._extract_a_share_codes

        scan_model = getattr(getattr(workspace, "tab_scan", None), "source_model", None)
        if scan_model is not None:
            codes.update(extract_codes(getattr(scan_model, "row_data", None)))

        rt_model = getattr(getattr(workspace, "tab_rt", None), "source_model", None)
        if rt_model is not None:
            codes.update(extract_codes(getattr(rt_model, "row_data", None)))

        watchlist_model = getattr(getattr(workspace, "tab_watchlist", None), "model", None)
        if watchlist_model is not None:
            codes.update(extract_codes(getattr(watchlist_model, "row_data", None)))

        foreign_block = getattr(workspace, "tab_foreign_block", None)
        foreign_model = getattr(foreign_block, "model", None)
        if foreign_model is not None:
            codes.update(extract_codes(getattr(foreign_model, "row_data", None)))
        elif foreign_block is not None:
            for code in getattr(foreign_block, "_block_trade_codes", []) or []:
                code_text = str(code or "").strip()
                if len(code_text) == 6 and code_text.isdigit():
                    codes.add(code_text)

        na_daily_model = getattr(getattr(workspace, "tab_na_daily", None), "model", None)
        if na_daily_model is not None:
            codes.update(extract_codes(getattr(na_daily_model, "row_data", None)))

        earnings_model = getattr(getattr(workspace, "tab_earnings", None), "model", None)
        if earnings_model is not None:
            codes.update(extract_codes(getattr(earnings_model, "row_data", None)))

        lhb_model = getattr(getattr(workspace, "tab_lhb", None), "model", None)
        if lhb_model is not None:
            codes.update(extract_codes(getattr(lhb_model, "row_data", None)))

        return codes
