# -*- coding: utf-8 -*-
from __future__ import annotations

from ui.workspaces.tab_capabilities import F5OffMarketQuoteUniverseCapability, QuoteUniverseCapability
from ui.workspaces.tab_registry import TabPostF5Policy, TabQuotePolicy


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
            return ()

        keys = []
        for spec in specs:
            key = str(spec.get("key", "")).strip()
            if not key:
                continue
            policy = str(spec.get("quote_policy") or "").strip()
            if policy == TabQuotePolicy.A_SHARE_REALTIME.value:
                keys.append(key)
        return tuple(keys)

    def _eligible_realtime_tab_keys(self) -> tuple[str, ...]:
        keys = self._realtime_tab_keys()
        workspace = self._workspace
        status_reader = getattr(workspace, "background_preload_status", None)
        current_tab_key = getattr(workspace, "current_tab_key", None)
        if not callable(status_reader) or not callable(current_tab_key):
            return keys
        try:
            status = status_reader()
            visible_key = str(current_tab_key() or "").strip()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return keys
        if not isinstance(status, dict):
            return keys
        if status.get("enabled") is True and status.get("finished") is not True:
            return tuple(key for key in keys if key == visible_key)
        return keys

    def _f5_off_market_tab_keys(self) -> tuple[str, ...]:
        keys = []
        for spec in self._tab_specs():
            key = str(spec.get("key", "")).strip()
            if not key:
                continue
            quote_policy = str(spec.get("quote_policy") or "").strip()
            post_f5_policy = str(spec.get("post_f5_policy") or "").strip()
            if (
                quote_policy == TabQuotePolicy.A_SHARE_REALTIME.value
                or post_f5_policy == TabPostF5Policy.DATA_REFRESH.value
            ):
                keys.append(key)
        return tuple(keys)

    def collect_realtime_quote_codes(self) -> set[str]:
        workspace = self._workspace
        codes: set[str] = set()
        get_loaded_tab = getattr(workspace, "get_loaded_tab", None)
        if not callable(get_loaded_tab):
            return codes
        for key in self._eligible_realtime_tab_keys():
            tab = get_loaded_tab(key)
            if isinstance(tab, QuoteUniverseCapability):
                codes.update(tab.get_realtime_quote_codes())

        return codes

    def collect_f5_off_market_quote_codes(self) -> set[str]:
        workspace = self._workspace
        get_loaded_tab = getattr(workspace, "get_loaded_tab", None)
        if not callable(get_loaded_tab):
            return set()

        codes: set[str] = set()
        for key in self._f5_off_market_tab_keys():
            tab = get_loaded_tab(key)
            if not isinstance(tab, F5OffMarketQuoteUniverseCapability):
                continue
            for code in tab.get_f5_off_market_quote_codes() or set():
                normalized = str(code or "").strip()
                if len(normalized) == 6 and normalized.isdigit():
                    codes.add(normalized)
        return codes
