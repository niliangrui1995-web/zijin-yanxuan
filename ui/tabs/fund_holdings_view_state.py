# -*- coding: utf-8 -*-
"""Persistent view-state helpers for the fund holdings tab."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ui.tabs.fund_holdings_filter_state import normalize_settings_values
from ui.tabs.fund_holdings_subjects import shorten_subject_name

LATEST_QUARTER_MODE = "latest"
ALL_QUARTER_MODE = "all"
SELECTED_QUARTER_MODE = "selected"


@dataclass(frozen=True)
class FundHoldingsViewState:
    subject_names: set[str]
    capital_attributes: set[str]
    search_text: str
    quarter_mode: str
    quarter_values: set[str]
    change_types: set[str]
    sort_column: int
    sort_order: int


def quarter_mode_from_filter(latest_only: bool, selected_quarters: Iterable[str]) -> str:
    selected = {str(item).strip() for item in selected_quarters if str(item).strip()}
    if latest_only:
        return LATEST_QUARTER_MODE
    return SELECTED_QUARTER_MODE if selected else ALL_QUARTER_MODE


def initial_quarter_query_scope(state: FundHoldingsViewState) -> tuple[str, set[str]]:
    values = {str(item).strip() for item in state.quarter_values if str(item).strip()}
    if state.quarter_mode == ALL_QUARTER_MODE:
        return ALL_QUARTER_MODE, set()
    if state.quarter_mode == SELECTED_QUARTER_MODE and values:
        return SELECTED_QUARTER_MODE, values
    return LATEST_QUARTER_MODE, set()


def sort_order_to_int(order, default: int = 0) -> int:
    value = getattr(order, "value", order)
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _settings_value(settings, key: str, default=None):
    try:
        return settings.value(key, default)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return default


def _settings_int(settings, key: str, default: int) -> int:
    try:
        return int(_settings_value(settings, key, default) or default)
    except (TypeError, ValueError):
        return int(default)


def _read_subject_names(settings, key_for: Callable[[str], str]) -> set[str]:
    subject_names = set()
    for subject_name in normalize_settings_values(_settings_value(settings, key_for("subject_names"), [])):
        shortened_subject_name = shorten_subject_name(subject_name)
        if shortened_subject_name:
            subject_names.add(shortened_subject_name)
    if subject_names:
        return subject_names

    legacy_subject_name = str(_settings_value(settings, key_for("subject_name"), "") or "").strip()
    shortened_subject_name = shorten_subject_name(legacy_subject_name)
    return {shortened_subject_name} if shortened_subject_name else set()


def read_fund_holdings_view_state(
    settings,
    key_for: Callable[[str], str],
    *,
    default_sort_order: int = 0,
) -> FundHoldingsViewState:
    quarter_mode = str(_settings_value(settings, key_for("quarter_mode"), LATEST_QUARTER_MODE) or LATEST_QUARTER_MODE)
    quarter_mode = quarter_mode.strip().lower()
    if quarter_mode not in {LATEST_QUARTER_MODE, ALL_QUARTER_MODE, SELECTED_QUARTER_MODE}:
        quarter_mode = LATEST_QUARTER_MODE

    return FundHoldingsViewState(
        subject_names=_read_subject_names(settings, key_for),
        capital_attributes=set(normalize_settings_values(_settings_value(settings, key_for("capital_attributes"), []))),
        search_text=str(_settings_value(settings, key_for("search_text"), "") or ""),
        quarter_mode=quarter_mode,
        quarter_values=set(normalize_settings_values(_settings_value(settings, key_for("quarter_values"), []))),
        change_types=set(normalize_settings_values(_settings_value(settings, key_for("change_types"), []))),
        sort_column=_settings_int(settings, key_for("sort_column"), -1),
        sort_order=sort_order_to_int(
            _settings_value(settings, key_for("sort_order"), default_sort_order),
            default=default_sort_order,
        ),
    )


def write_fund_holdings_view_state(
    settings,
    key_for: Callable[[str], str],
    state: FundHoldingsViewState,
) -> None:
    subject_names = sorted(state.subject_names)
    settings.setValue(key_for("subject_names"), subject_names)
    settings.setValue(key_for("subject_name"), subject_names[0] if len(subject_names) == 1 else "")
    settings.setValue(key_for("capital_attributes"), sorted(state.capital_attributes))
    settings.setValue(key_for("search_text"), state.search_text)
    settings.setValue(key_for("quarter_mode"), state.quarter_mode)
    settings.setValue(key_for("quarter_values"), sorted(state.quarter_values, reverse=True))
    settings.setValue(key_for("change_types"), sorted(state.change_types))
    settings.setValue(key_for("sort_column"), int(state.sort_column))
    settings.setValue(key_for("sort_order"), int(state.sort_order))
    settings.sync()
