# -*- coding: utf-8 -*-
from __future__ import annotations

from ui.workspaces.tab_capabilities import CodeRowSelectionCapability


class WorkspaceNavigationService:
    """Handles grouped tab navigation and cross-tab stock selection."""

    def __init__(self, workspace):
        self._workspace = workspace

    def _tab_specs(self) -> list[dict]:
        specs = getattr(self._workspace, "_tab_specs", None)
        if specs is not None:
            return list(specs)
        tab_specs = getattr(self._workspace, "tab_specs", None)
        return list(tab_specs() or []) if callable(tab_specs) else []

    def tab_indices_by_group(self) -> dict[str, list[int]]:
        result: dict[str, list[int]] = {}
        specs = self._tab_specs()
        for index, spec in enumerate(specs):
            group = str(spec.get("group", "")).strip()
            result.setdefault(group, []).append(index)
        for group, indices in result.items():
            result[group] = sorted(
                indices,
                key=lambda idx: (
                    int(specs[idx].get("group_order", idx) or idx),
                    idx,
                ),
            )
        return result

    def _key_for_index(self, tab_index: int) -> str:
        specs = self._tab_specs()
        if 0 <= int(tab_index) < len(specs):
            return str(specs[int(tab_index)].get("key") or "").strip()
        return ""

    def select_code_row(self, code: str, preferred_tab_index: int | None = None) -> bool:
        code_text = str(code or "").strip()
        if not code_text:
            return False

        tab_widget = getattr(self._workspace, "tabs", None)
        if tab_widget is None:
            return False

        current_index = tab_widget.currentIndex()
        candidate_indices: list[int] = []

        if 0 <= current_index < tab_widget.count():
            candidate_indices.append(current_index)

        if isinstance(preferred_tab_index, int) and 0 <= preferred_tab_index < tab_widget.count():
            if preferred_tab_index not in candidate_indices:
                candidate_indices.append(preferred_tab_index)

        for tab_index in range(tab_widget.count()):
            if tab_index not in candidate_indices:
                candidate_indices.append(tab_index)

        get_loaded_tab = getattr(self._workspace, "get_loaded_tab", None)
        for tab_index in candidate_indices:
            key = self._key_for_index(tab_index)
            tab = get_loaded_tab(key) if key and callable(get_loaded_tab) else None
            tab = tab or tab_widget.widget(tab_index)
            if tab_index == preferred_tab_index:
                get_tab = getattr(self._workspace, "get_tab", None)
                if key and callable(get_tab):
                    tab = get_tab(key) or tab
            if tab is None:
                continue
            if isinstance(tab, CodeRowSelectionCapability) and tab.select_code_row(code_text):
                if tab_index != current_index:
                    activate_tab = getattr(self._workspace, "activate_tab", None)
                    if not callable(activate_tab) or not activate_tab(tab_index, reason="stock_signal_source"):
                        tab_widget.setCurrentIndex(tab_index)
                return True

        return False
