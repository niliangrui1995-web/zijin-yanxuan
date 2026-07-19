# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ui.workspaces.classic_workspace import ClassicWorkspace, _tab_factory_for_definition
from ui.workspaces.quote_universe_service import QuoteUniverseService
from ui.workspaces.tab_registry import (
    INFO_SOURCE_GROUP,
    STATIC_LINEAGE_FIELDS,
    TAB_DEFINITIONS,
    TAB_DEFINITIONS_BY_KEY,
    TAB_PRELOAD_DEPENDENCIES,
    TabConstructorProfile,
    TabF5SnapshotPolicy,
    TabPostF5Policy,
    TabQuotePolicy,
    TabRuntimeDelayPolicy,
    TabWarmPolicy,
    create_tab_lineage_service,
    get_tab_definition,
    health_probe_tab_keys,
    lineage_exclusion_tab_definitions,
    lineage_tab_definitions,
    preload_dependencies_for,
    startup_tab_keys,
    widget_prewarm_tab_keys,
)
from ui.workspaces.workspace_facade import WorkspaceFacade
from ui.workspaces.workspace_table_service import WorkspaceTableService

EXPECTED_TAB_CONTRACT = (
    (
        "watchlist",
        "关注池",
        "主工作台",
        0,
        10,
        0,
        "ui.tabs.watchlist_tab",
        "WatchlistTab",
        "tab_watchlist",
        TabConstructorProfile.WATCHLIST,
    ),
    (
        "lhb",
        "龙虎榜",
        "主工作台",
        1,
        15,
        8,
        "ui.tabs.lhb_tab",
        "LhbTab",
        "tab_lhb",
        TabConstructorProfile.LHB,
    ),
    (
        "asian_market",
        "亚洲寡头",
        "主工作台",
        2,
        20,
        9,
        "ui.tabs.asian_market_tab",
        "AsianMarketTab",
        "tab_asian_market",
        TabConstructorProfile.DATA_PROVIDER_PARENT,
    ),
    (
        "na_daily",
        "北美战报",
        "主工作台",
        3,
        30,
        3,
        "ui.tabs.na_daily_tab",
        "NADailyTab",
        "tab_na_daily",
        TabConstructorProfile.DATA_PROVIDER_PARENT,
    ),
    (
        "stock_candidates",
        "综合候选",
        "主工作台",
        4,
        32,
        10,
        "ui.tabs.stock_candidate_tab",
        "StockCandidateTab",
        "tab_stock_candidates",
        TabConstructorProfile.DATA_PROVIDER_PARENT,
    ),
    (
        "ai_industry_chain",
        "AI产业链",
        INFO_SOURCE_GROUP,
        5,
        15,
        2,
        "ui.tabs.ai_industry_chain_tab",
        "AIIndustryChainTab",
        "tab_ai_industry_chain",
        TabConstructorProfile.DATA_PROVIDER_PARENT,
    ),
    (
        "scan",
        "VCP扫描",
        INFO_SOURCE_GROUP,
        6,
        10,
        4,
        "ui.tabs.scan_tab",
        "ScanTab",
        "tab_scan",
        TabConstructorProfile.SCAN,
    ),
    (
        "foreign_block",
        "大宗交易",
        INFO_SOURCE_GROUP,
        7,
        20,
        5,
        "ui.tabs.foreign_block_trade_tab",
        "ForeignBlockTradeTab",
        "tab_foreign_block",
        TabConstructorProfile.DATA_PROVIDER_PARENT,
    ),
    (
        "earnings",
        "业绩异动",
        INFO_SOURCE_GROUP,
        8,
        30,
        6,
        "ui.tabs.earnings_tab",
        "EarningsTab",
        "tab_earnings",
        TabConstructorProfile.DATA_PROVIDER_PARENT,
    ),
    (
        "fund_holdings",
        "基金持仓",
        INFO_SOURCE_GROUP,
        9,
        40,
        7,
        "ui.tabs.fund_holdings_tab",
        "FundHoldingsTab",
        "tab_fund_holdings",
        TabConstructorProfile.FUND_HOLDINGS,
    ),
    (
        "system_log",
        "系统日志",
        "系统",
        10,
        10,
        1,
        "ui.tabs.log_tab",
        "LogTab",
        "tab_log",
        TabConstructorProfile.WORKSPACE_PARENT,
    ),
)

EXPECTED_TAB_POLICY_CONTRACT = (
    (
        "watchlist",
        TabWarmPolicy.WIDGET_PREWARM,
        TabQuotePolicy.A_SHARE_REALTIME,
        TabF5SnapshotPolicy.SNAPSHOT,
        TabPostF5Policy.NONE,
        9,
        "watchlist_vm + global_store.quotes",
        None,
    ),
    (
        "lhb",
        TabWarmPolicy.WIDGET_PREWARM,
        TabQuotePolicy.A_SHARE_REALTIME,
        TabF5SnapshotPolicy.INDEPENDENT,
        TabPostF5Policy.NONE,
        5,
        "LhbPoolManager cache + local_quote_snapshot",
        None,
    ),
    (
        "asian_market",
        TabWarmPolicy.WIDGET_PREWARM,
        TabQuotePolicy.NONE,
        TabF5SnapshotPolicy.SNAPSHOT,
        TabPostF5Policy.NONE,
        1,
        "asian_market local cache + realtime quote cache",
        None,
    ),
    (
        "na_daily",
        TabWarmPolicy.WIDGET_PREWARM,
        TabQuotePolicy.A_SHARE_REALTIME,
        TabF5SnapshotPolicy.SNAPSHOT,
        TabPostF5Policy.NONE,
        2,
        "daily report markdown/json + global_store.quotes",
        None,
    ),
    (
        "stock_candidates",
        TabWarmPolicy.WIDGET_PREWARM,
        TabQuotePolicy.A_SHARE_REALTIME,
        TabF5SnapshotPolicy.INDEPENDENT,
        TabPostF5Policy.NONE,
        0,
        "workspace_stock_context",
        None,
    ),
    (
        "ai_industry_chain",
        TabWarmPolicy.WIDGET_PREWARM,
        TabQuotePolicy.NONE,
        TabF5SnapshotPolicy.INDEPENDENT,
        TabPostF5Policy.DATA_REFRESH,
        3,
        "AI industry chain workbook + local market data",
        None,
    ),
    (
        "scan",
        TabWarmPolicy.WIDGET_PREWARM,
        TabQuotePolicy.NONE,
        TabF5SnapshotPolicy.INDEPENDENT,
        TabPostF5Policy.DATA_REFRESH,
        4,
        "DataStore.scan_cache",
        None,
    ),
    (
        "foreign_block",
        TabWarmPolicy.WIDGET_PREWARM,
        TabQuotePolicy.NONE,
        TabF5SnapshotPolicy.INDEPENDENT,
        TabPostF5Policy.DATA_REFRESH,
        6,
        "foreign_block_trade_latest.json",
        None,
    ),
    (
        "earnings",
        TabWarmPolicy.WIDGET_PREWARM,
        TabQuotePolicy.NONE,
        TabF5SnapshotPolicy.INDEPENDENT,
        TabPostF5Policy.DATA_REFRESH,
        7,
        "earnings_state / local display window",
        None,
    ),
    (
        "fund_holdings",
        TabWarmPolicy.WIDGET_PREWARM,
        TabQuotePolicy.NONE,
        TabF5SnapshotPolicy.INDEPENDENT,
        TabPostF5Policy.DATA_REFRESH,
        8,
        "fund_holdings_store",
        None,
    ),
    (
        "system_log",
        TabWarmPolicy.WIDGET_PREWARM,
        TabQuotePolicy.NONE,
        TabF5SnapshotPolicy.NONE,
        TabPostF5Policy.NONE,
        None,
        None,
        "non_data_tab",
    ),
)

EXPECTED_TAB_RUNTIME_CONTRACT = (
    ("watchlist", TabRuntimeDelayPolicy.WATCHLIST, "", {}, {}),
    (
        "lhb",
        TabRuntimeDelayPolicy.LHB_POOL,
        "initial_load_delay_ms",
        {"autoload_pool": False},
        {},
    ),
    ("asian_market", TabRuntimeDelayPolicy.STANDARD, "local_cache_delay_ms", {}, {"local_cache_delay_ms": 1}),
    ("na_daily", TabRuntimeDelayPolicy.STANDARD, "runtime_start_delay_ms", {}, {}),
    ("stock_candidates", TabRuntimeDelayPolicy.STANDARD, "runtime_start_delay_ms", {}, {}),
    ("ai_industry_chain", TabRuntimeDelayPolicy.STANDARD, "runtime_start_delay_ms", {}, {}),
    ("scan", TabRuntimeDelayPolicy.STANDARD, "initial_cache_load_delay_ms", {}, {}),
    (
        "foreign_block",
        TabRuntimeDelayPolicy.STANDARD,
        "initial_cache_load_delay_ms",
        {},
        {"autoload": False},
    ),
    ("earnings", TabRuntimeDelayPolicy.SHELL_HEAVY, "runtime_start_delay_ms", {}, {}),
    (
        "fund_holdings",
        TabRuntimeDelayPolicy.SHELL_HEAVY,
        "initial_load_delay_ms",
        {"autoload": False},
        {},
    ),
    ("system_log", TabRuntimeDelayPolicy.NONE, "", {}, {}),
)


def test_tab_registry_has_exact_unique_runtime_contract():
    actual = tuple(
        (
            definition.key,
            definition.title,
            definition.group,
            definition.stack_order,
            definition.group_order,
            definition.startup_order,
            definition.module_name,
            definition.class_name,
            definition.legacy_attr,
            definition.constructor_profile,
        )
        for definition in TAB_DEFINITIONS
    )

    assert actual == EXPECTED_TAB_CONTRACT
    assert tuple(definition.stack_order for definition in TAB_DEFINITIONS) == tuple(range(11))
    assert sorted(definition.startup_order for definition in TAB_DEFINITIONS) == list(range(11))
    assert len(TAB_DEFINITIONS_BY_KEY) == len(TAB_DEFINITIONS) == 11
    assert tuple(TAB_DEFINITIONS_BY_KEY) == tuple(definition.key for definition in TAB_DEFINITIONS)


def test_tab_registry_has_exact_policy_and_lineage_contract():
    actual = tuple(
        (
            definition.key,
            definition.warm_policy,
            definition.quote_policy,
            definition.f5_snapshot_policy,
            definition.post_f5_policy,
            definition.lineage_order,
            definition.lineage.source if definition.lineage is not None else None,
            definition.lineage_exclusion.reason if definition.lineage_exclusion is not None else None,
        )
        for definition in TAB_DEFINITIONS
    )

    assert actual == EXPECTED_TAB_POLICY_CONTRACT


def test_tab_registry_separates_static_network_capability_from_runtime_activity():
    expected_capable = {
        "watchlist",
        "lhb",
        "asian_market",
        "na_daily",
        "foreign_block",
        "earnings",
        "fund_holdings",
    }
    definitions = lineage_tab_definitions()

    assert {
        definition.key
        for definition in definitions
        if definition.lineage is not None and definition.lineage.network_capable
    } == expected_capable
    for definition in definitions:
        defaults = definition.lineage.as_runtime_defaults(definition.key)
        assert type(defaults["network_capable"]) is bool
        assert defaults["network_capable"] is (definition.key in expected_capable)
        expected_activity = None if definition.key in expected_capable else False
        assert defaults["triggered_network"] is expected_activity


def test_tab_registry_factory_is_the_only_static_lineage_source():
    for definition in lineage_tab_definitions():
        static = definition.lineage
        assert static is not None
        lineage = create_tab_lineage_service(definition.key).describe([]).lineage
        payload = lineage.as_dict()

        assert payload["key"] == definition.key
        assert payload["view"] == definition.key
        assert payload["source"] == static.source
        assert payload["provider"] == static.provider
        assert payload["cache_refs"] == list(static.cache_refs)
        assert STATIC_LINEAGE_FIELDS.isdisjoint(lineage.as_dynamic_dict())

    with pytest.raises(KeyError):
        create_tab_lineage_service("system_log")


def test_tab_registry_has_exact_constructor_and_runtime_delay_contract():
    actual = tuple(
        (
            definition.key,
            definition.runtime_delay_policy,
            definition.runtime_delay_kwarg,
            definition.constructor_default_kwargs(),
            definition.noninteractive_default_kwargs(),
        )
        for definition in TAB_DEFINITIONS
    )

    assert actual == EXPECTED_TAB_RUNTIME_CONTRACT


def test_controlled_startup_probe_defer_keys_come_from_registry_without_system_log():
    expected_registry_keys = frozenset(
        definition.key for definition in TAB_DEFINITIONS if definition.key != "system_log"
    )
    expected_contract_keys = frozenset(
        row[0] for row in EXPECTED_TAB_POLICY_CONTRACT if row[0] != "system_log"
    )

    assert ClassicWorkspace.CONTROLLED_STARTUP_PROBE_DEFER_KEYS == expected_registry_keys
    assert ClassicWorkspace.CONTROLLED_STARTUP_PROBE_DEFER_KEYS == expected_contract_keys
    assert len(ClassicWorkspace.CONTROLLED_STARTUP_PROBE_DEFER_KEYS) == 10
    assert "system_log" not in ClassicWorkspace.CONTROLLED_STARTUP_PROBE_DEFER_KEYS


def test_tab_registry_generates_startup_health_lineage_and_warmup_orders():
    assert startup_tab_keys() == (
        "watchlist",
        "system_log",
        "ai_industry_chain",
        "na_daily",
        "scan",
        "foreign_block",
        "earnings",
        "fund_holdings",
        "lhb",
        "asian_market",
        "stock_candidates",
    )
    assert health_probe_tab_keys() == (
        "watchlist",
        "asian_market",
        "na_daily",
        "stock_candidates",
        "ai_industry_chain",
        "lhb",
        "scan",
        "foreign_block",
        "earnings",
        "fund_holdings",
        "system_log",
    )
    assert tuple(definition.key for definition in lineage_tab_definitions()) == (
        "stock_candidates",
        "asian_market",
        "na_daily",
        "ai_industry_chain",
        "scan",
        "lhb",
        "foreign_block",
        "earnings",
        "fund_holdings",
        "watchlist",
    )
    assert tuple(definition.key for definition in lineage_exclusion_tab_definitions()) == ("system_log",)
    assert widget_prewarm_tab_keys() == frozenset(definition.key for definition in TAB_DEFINITIONS)


def test_tab_preload_dependencies_are_topological_and_candidate_is_final_consumer():
    positions = {key: index for index, key in enumerate(startup_tab_keys())}

    for key, dependencies in TAB_PRELOAD_DEPENDENCIES.items():
        assert preload_dependencies_for(key) == dependencies
        assert all(positions[dependency] < positions[key] for dependency in dependencies)

    assert preload_dependencies_for("foreign_block") == ("ai_industry_chain",)
    assert preload_dependencies_for("earnings") == ("ai_industry_chain",)
    assert preload_dependencies_for("fund_holdings") == ("ai_industry_chain",)
    assert preload_dependencies_for("lhb") == ("ai_industry_chain",)
    assert positions["stock_candidates"] == len(positions) - 1


def test_tab_registry_is_immutable():
    definition = TAB_DEFINITIONS[0]
    with pytest.raises(FrozenInstanceError):
        definition.group = "changed"
    with pytest.raises(TypeError):
        TAB_DEFINITIONS_BY_KEY["new"] = definition


def test_importing_tab_registry_does_not_import_concrete_tabs():
    project_root = Path(__file__).resolve().parents[1]
    script = (
        "import sys\n"
        "import ui.workspaces.tab_registry\n"
        "loaded = sorted(name for name in sys.modules if name.startswith('ui.tabs.'))\n"
        "raise SystemExit(','.join(loaded) if loaded else 0)\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_classic_workspace_runtime_specs_are_generated_from_registry():
    workspace = SimpleNamespace(data_provider=object(), engine=object())
    workspace._tab_factory = lambda *_args, **_kwargs: lambda **_runtime_kwargs: None

    specs = ClassicWorkspace._build_tab_specs(workspace, {"startup_tasks_enabled": False})

    assert len(specs) == 11
    for spec, definition in zip(specs, TAB_DEFINITIONS, strict=True):
        assert {key: spec[key] for key in definition.runtime_spec_metadata()} == definition.runtime_spec_metadata()
        assert callable(spec["factory"])
        assert spec["widget"] is None
        assert spec["loaded"] is False


def test_tab_factory_reads_runtime_services_when_widget_is_created():
    calls = []
    workspace = SimpleNamespace(data_provider=None, engine=None)

    def _tab_factory(*args, **kwargs):
        return lambda **runtime_kwargs: calls.append((args, kwargs, runtime_kwargs)) or "widget"

    workspace._tab_factory = _tab_factory
    scan = get_tab_definition("scan")
    assert scan is not None
    factory = _tab_factory_for_definition(workspace, scan, {})

    provider = object()
    engine = object()
    workspace.data_provider = provider
    workspace.engine = engine

    assert factory(initial_cache_load_delay_ms=10) == "widget"
    assert calls == [
        (
            (scan.class_name, scan.module_name, provider, engine, workspace),
            {},
            {"initial_cache_load_delay_ms": 10},
        )
    ]


def test_explicit_quote_and_post_f5_policies_ignore_visual_group_changes():
    watchlist = get_tab_definition("watchlist")
    scan = get_tab_definition("scan")
    assert watchlist is not None and scan is not None

    watchlist_spec = watchlist.runtime_spec_metadata()
    watchlist_spec["group"] = INFO_SOURCE_GROUP
    scan_spec = scan.runtime_spec_metadata()
    scan_spec["group"] = "主工作台"
    loaded_tabs = {
        "watchlist": SimpleNamespace(get_realtime_quote_codes=lambda: {"000001"}),
        "scan": SimpleNamespace(
            get_realtime_quote_codes=lambda: {"000002"},
            refresh_data_after_f5=lambda: True,
        ),
    }
    workspace = SimpleNamespace(
        tab_specs=lambda: [watchlist_spec, scan_spec],
        get_loaded_tab=lambda key: loaded_tabs.get(key),
    )

    assert QuoteUniverseService(workspace).collect_realtime_quote_codes() == {"000001"}

    facade = object.__new__(WorkspaceFacade)
    facade._workspace = workspace
    assert [key for key, _tab in facade._iter_post_f5_information_source_tabs()] == ["scan"]

    moved_watchlist = replace(watchlist, group=INFO_SOURCE_GROUP)
    moved_scan = replace(scan, group="主工作台")
    assert moved_watchlist.quote_policy is TabQuotePolicy.A_SHARE_REALTIME
    assert moved_watchlist.post_f5_policy is TabPostF5Policy.NONE
    assert moved_scan.quote_policy is TabQuotePolicy.NONE
    assert moved_scan.post_f5_policy is TabPostF5Policy.DATA_REFRESH


def test_explicit_f5_snapshot_policy_overrides_legacy_method_guessing():
    watchlist = SimpleNamespace(
        workspace_key="watchlist",
        refresh_table_from_latest_snapshot=lambda: None,
        _schedule_context_refresh=lambda: None,
    )
    scan = SimpleNamespace(
        workspace_key="scan",
        refresh_table_from_latest_snapshot=lambda: None,
    )
    workspace = SimpleNamespace(
        iter_tabs=lambda: [watchlist, scan],
        tabs=SimpleNamespace(currentWidget=lambda: None),
    )

    service = WorkspaceTableService(workspace)

    assert service._ordered_refreshable_tabs(skip_cache_reload_tabs=True) == [watchlist]
    assert get_tab_definition("watchlist").f5_snapshot_policy is TabF5SnapshotPolicy.SNAPSHOT
    assert get_tab_definition("scan").f5_snapshot_policy is TabF5SnapshotPolicy.INDEPENDENT
    assert all(definition.warm_policy is TabWarmPolicy.WIDGET_PREWARM for definition in TAB_DEFINITIONS)
