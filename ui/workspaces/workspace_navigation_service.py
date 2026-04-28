# -*- coding: utf-8 -*-
from __future__ import annotations

from ui.workspaces.tab_capabilities import CodeRowSelectionCapability, PrimaryRowSelectionCapability


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

    def nav_groups(self) -> list[str]:
        groups: list[str] = []
        for spec in self._tab_specs():
            group = str(spec.get("group", "")).strip()
            if group and group not in groups:
                groups.append(group)
        return groups

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

    def select_scan_row(self, index: int) -> bool:
        get_tab = getattr(self._workspace, "get_tab", None)
        tab = get_tab("scan") if callable(get_tab) else None
        if not isinstance(tab, PrimaryRowSelectionCapability):
            return False
        return bool(tab.select_primary_row(index))

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

        for tab_index in candidate_indices:
            tab = tab_widget.widget(tab_index)
            if tab_index == preferred_tab_index:
                key = self._key_for_index(tab_index)
                get_tab = getattr(self._workspace, "get_tab", None)
                if key and callable(get_tab):
                    tab = get_tab(key) or tab
            if tab is None:
                continue
            if isinstance(tab, CodeRowSelectionCapability) and tab.select_code_row(code_text):
                if tab_index != current_index:
                    tab_widget.setCurrentIndex(tab_index)
                return True

        return False
