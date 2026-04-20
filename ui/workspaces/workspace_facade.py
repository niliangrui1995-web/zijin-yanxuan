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
        workspace = self._workspace
        for attr_name in ("tab_na_daily", "tab_foreign_block"):
            tab = getattr(workspace, attr_name, None)
            auto_refresh = getattr(tab, "_auto_refresh_realtime", None)
            if callable(auto_refresh):
                auto_refresh(force=True)

        self.schedule_watchlist_special_quotes(task_manager)

    def auto_start_rt_monitor(self) -> bool:
        rt_tab = getattr(self._workspace, "tab_rt", None)
        toggle_monitor = getattr(rt_tab, "_toggle_rt_monitor", None)
        if not callable(toggle_monitor):
            return False
        toggle_monitor(auto=True)
        return True

    def collect_watchlist_radar_data(self) -> tuple[dict, dict, dict, dict, dict, dict | None]:
        return self._watchlist_radar_service.collect_radar_data()
