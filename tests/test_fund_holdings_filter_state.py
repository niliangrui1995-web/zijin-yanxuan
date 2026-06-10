from ui.tabs.fund_holdings_filter_state import (
    build_current_filter_summary,
    extract_capital_attribute_filter_options,
    extract_subject_filter_options,
    format_change_filter_button_text,
    format_quarter_filter_button_text,
    normalize_settings_values,
    quarter_scope_loaded,
    resolve_quarter_query_scope,
)


def test_fund_holdings_filter_state_formats_button_text():
    options = ("新进", "增持", "减持", "退出")

    assert format_change_filter_button_text([], options) == ("变化：全部", "全部变化")
    assert format_change_filter_button_text({"减持", "新进"}, options) == ("变化：新进 / 减持", "新进、减持")
    assert format_change_filter_button_text({"退出", "增持", "新进"}, options) == (
        "变化：3项",
        "新进、增持、退出",
    )

    assert format_quarter_filter_button_text(True, {"2025Q4"}) == ("季度：最新", "仅显示各主体最新季度")
    assert format_quarter_filter_button_text(False, set()) == ("季度：全部", "显示全部季度")
    assert format_quarter_filter_button_text(False, {"2025Q3", "2025Q4"}) == (
        "季度：2025Q4 / 2025Q3",
        "2025Q4、2025Q3",
    )
    assert format_quarter_filter_button_text(False, {"2025Q2", "2025Q3", "2025Q4"}) == (
        "季度：3项",
        "2025Q4、2025Q3、2025Q2",
    )


def test_fund_holdings_filter_state_normalizes_settings_values():
    assert normalize_settings_values(None) == []
    assert normalize_settings_values("  QFII  ") == ["QFII"]
    assert normalize_settings_values([" QFII ", "", "瑞远"]) == ["QFII", "瑞远"]
    assert normalize_settings_values(202604) == ["202604"]


def test_fund_holdings_filter_state_extracts_filter_options_from_rows():
    rows = [
        {"主体": " QFII ", "_capital_attribute_value": "client"},
        {"主体": "瑞远", "_capital_attribute_value": "self_owned"},
        {"主体": "QFII", "_capital_attribute_value": "client"},
        {"主体": "", "_capital_attribute_value": "unknown"},
    ]

    assert extract_subject_filter_options(rows) == ["QFII", "瑞远"]
    assert extract_capital_attribute_filter_options(
        rows,
        ("unmarked", "self_owned", "client"),
    ) == ["self_owned", "client"]


def test_fund_holdings_filter_state_resolves_quarter_scope():
    assert resolve_quarter_query_scope(
        True,
        {"2025Q4"},
        latest_scope="latest",
        all_scope="all",
        selected_scope="selected",
    ) == ("latest", set())
    assert resolve_quarter_query_scope(
        False,
        {"2025Q4"},
        latest_scope="latest",
        all_scope="all",
        selected_scope="selected",
    ) == ("selected", {"2025Q4"})
    assert resolve_quarter_query_scope(
        False,
        set(),
        latest_scope="latest",
        all_scope="all",
        selected_scope="selected",
    ) == ("all", set())

    assert quarter_scope_loaded(
        "selected",
        {"2025Q3"},
        loaded_scope="all",
        loaded_keys=set(),
        latest_scope="latest",
        all_scope="all",
        selected_scope="selected",
    )
    assert quarter_scope_loaded(
        "selected",
        {"2025Q3"},
        loaded_scope="selected",
        loaded_keys={"2025Q3", "2025Q4"},
        latest_scope="latest",
        all_scope="all",
        selected_scope="selected",
    )
    assert not quarter_scope_loaded(
        "selected",
        {"2025Q2"},
        loaded_scope="selected",
        loaded_keys={"2025Q3", "2025Q4"},
        latest_scope="latest",
        all_scope="all",
        selected_scope="selected",
    )


def test_fund_holdings_filter_state_builds_current_filter_summary():
    assert (
        build_current_filter_summary(
            subject_names={"MORGAN STANLEY"},
            capital_attributes={"unmarked"},
            capital_label=lambda value: {"unmarked": "--"}.get(value, value),
            latest_only=False,
            selected_quarters={"2025Q3", "2025Q4"},
            change_types={"增持"},
            search_text="  算力  ",
        )
        == "MORGAN STANLEY｜--｜2025Q4 / 2025Q3｜增持｜算力"
    )
    assert (
        build_current_filter_summary(
            subject_names=set(),
            capital_attributes=set(),
            capital_label=lambda value: value,
            latest_only=True,
            selected_quarters=set(),
            change_types=set(),
            search_text="",
        )
        == "最新季度"
    )
    assert (
        build_current_filter_summary(
            subject_names=set(),
            capital_attributes=set(),
            capital_label=lambda value: value,
            latest_only=False,
            selected_quarters=set(),
            change_types=set(),
            search_text="",
        )
        == "全部"
    )
