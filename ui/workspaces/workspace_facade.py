# -*- coding: utf-8 -*-
from __future__ import annotations

from ui.workspaces.quote_universe_service import QuoteUniverseService
from ui.workspaces.watchlist_radar_service import WatchlistRadarService


class WorkspaceFacade:
    """ClassicWorkspace 的跨 Tab 聚合与编排门面。"""

    def __init__(self, workspace):
        self._workspace = workspace
        self._quote_universe_service = QuoteUniverseService(workspace)
        self._watchlist_radar_service = WatchlistRadarService(workspace)

    def get_realtime_quote_codes(self) -> set[str]:
        return self._quote_universe_service.collect_realtime_quote_codes()

    def refresh_watchlist_names(self, code2name: dict[str, str]) -> bool:
        return self._watchlist_radar_service.refresh_watchlist_names(code2name)

    def schedule_watchlist_special_quotes(self, task_manager) -> None:
        self._watchlist_radar_service.prime_watchlist_state()

    def run_post_online_refresh(self, task_manager) -> None:
        get_tab = getattr(self._workspace, "get_tab", None)
        for key in ("na_daily", "foreign_block"):
            tab = get_tab(key) if callable(get_tab) else None
            refresh = getattr(tab, "run_post_online_refresh", None)
            if callable(refresh):
                refresh()

        self.schedule_watchlist_special_quotes(task_manager)

    def auto_start_rt_monitor(self) -> bool:
        get_tab = getattr(self._workspace, "get_tab", None)
        rt_tab = get_tab("rt_monitor") if callable(get_tab) else None
        toggle_monitor = getattr(rt_tab, "toggle_rt_monitor", None)
        is_running = getattr(rt_tab, "is_rt_running", None)
        if not callable(toggle_monitor):
            return False
        if callable(is_running) and is_running():
            return False
        toggle_monitor(auto=True)
        return True

    def collect_watchlist_radar_data(self) -> tuple[dict, dict, dict, dict, dict, dict | None]:
        return self._watchlist_radar_service.collect_radar_data()
