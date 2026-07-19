from ui.tabs.fund_holdings_view_state import (
    ALL_QUARTER_MODE,
    LATEST_QUARTER_MODE,
    SELECTED_QUARTER_MODE,
    FundHoldingsViewState,
    initial_quarter_query_scope,
    quarter_mode_from_filter,
    read_fund_holdings_view_state,
    write_fund_holdings_view_state,
)


class _FakeSettings:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.synced = False

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - mirrors QSettings API.
        self.values[key] = value

    def sync(self):
        self.synced = True


def _key(name: str) -> str:
    return f"fund/{name}"


def test_quarter_mode_from_filter_preserves_latest_all_and_selected_modes():
    assert quarter_mode_from_filter(True, {"2025Q4"}) == LATEST_QUARTER_MODE
    assert quarter_mode_from_filter(False, set()) == ALL_QUARTER_MODE
    assert quarter_mode_from_filter(False, {"2025Q4"}) == SELECTED_QUARTER_MODE


def test_initial_quarter_query_scope_uses_persisted_mode_without_double_load():
    base = dict(
        subject_names=set(),
        capital_attributes=set(),
        search_text="",
        change_types=set(),
        sort_column=-1,
        sort_order=0,
    )

    all_state = FundHoldingsViewState(quarter_mode=ALL_QUARTER_MODE, quarter_values=set(), **base)
    selected_state = FundHoldingsViewState(
        quarter_mode=SELECTED_QUARTER_MODE,
        quarter_values={"2025Q3", "2025Q4"},
        **base,
    )
    empty_selected_state = FundHoldingsViewState(
        quarter_mode=SELECTED_QUARTER_MODE,
        quarter_values=set(),
        **base,
    )

    assert initial_quarter_query_scope(all_state) == (ALL_QUARTER_MODE, set())
    assert initial_quarter_query_scope(selected_state) == (SELECTED_QUARTER_MODE, {"2025Q3", "2025Q4"})
    assert initial_quarter_query_scope(empty_selected_state) == (LATEST_QUARTER_MODE, set())


def test_write_fund_holdings_view_state_persists_legacy_single_subject_key():
    settings = _FakeSettings()
    state = FundHoldingsViewState(
        subject_names={"MORGAN STANLEY"},
        capital_attributes={"owned"},
        search_text="600000",
        quarter_mode=SELECTED_QUARTER_MODE,
        quarter_values={"2025Q3", "2025Q4"},
        change_types={"new", "flat"},
        sort_column=2,
        sort_order=1,
    )

    write_fund_holdings_view_state(settings, _key, state)

    assert settings.values["fund/subject_names"] == ["MORGAN STANLEY"]
    assert settings.values["fund/subject_name"] == "MORGAN STANLEY"
    assert settings.values["fund/quarter_values"] == ["2025Q4", "2025Q3"]
    assert settings.values["fund/sort_column"] == 2
    assert settings.values["fund/sort_order"] == 1
    assert settings.synced is True


def test_read_fund_holdings_view_state_normalizes_subjects_and_invalid_values():
    settings = _FakeSettings(
        {
            "fund/subject_names": ["MORGAN STANLEY & CO.INTERNATIONAL PLC"],
            "fund/capital_attributes": ["owned"],
            "fund/search_text": "000001",
            "fund/quarter_mode": "invalid",
            "fund/quarter_values": ["2025Q4"],
            "fund/change_types": ["new"],
            "fund/sort_column": "bad",
            "fund/sort_order": "bad",
        }
    )

    state = read_fund_holdings_view_state(settings, _key, default_sort_order=0)

    assert state.subject_names == {"MORGAN STANLEY"}
    assert state.capital_attributes == {"owned"}
    assert state.search_text == "000001"
    assert state.quarter_mode == LATEST_QUARTER_MODE
    assert state.quarter_values == {"2025Q4"}
    assert state.change_types == {"new"}
    assert state.sort_column == -1
    assert state.sort_order == 0


def test_read_fund_holdings_view_state_supports_legacy_subject_name():
    settings = _FakeSettings({"fund/subject_name": "J.P.Morgan Secur ities PLC"})

    state = read_fund_holdings_view_state(settings, _key)

    assert state.subject_names == {"J.P.Morgan"}
