# -*- coding: utf-8 -*-
"""Single source of truth for ClassicWorkspace tab metadata and policies.

This module intentionally contains no Qt or concrete ``ui.tabs`` imports so it
is safe to use from startup, diagnostics, and tests without constructing heavy
widgets or importing their runtime dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, Mapping

INFO_SOURCE_GROUP: Final = "情报源"
STATIC_LINEAGE_FIELDS: Final[frozenset[str]] = frozenset(
    {"key", "view", "source", "provider", "cache_refs", "network_capable"}
)


class TabConstructorProfile(StrEnum):
    WATCHLIST = "watchlist"
    DATA_PROVIDER_PARENT = "data_provider_parent"
    LHB = "lhb"
    SCAN = "scan"
    FUND_HOLDINGS = "fund_holdings"
    WORKSPACE_PARENT = "workspace_parent"


class TabWarmPolicy(StrEnum):
    LAZY = "lazy"
    WIDGET_PREWARM = "widget_prewarm"


class TabQuotePolicy(StrEnum):
    NONE = "none"
    A_SHARE_REALTIME = "a_share_realtime"


class TabF5SnapshotPolicy(StrEnum):
    NONE = "none"
    SNAPSHOT = "snapshot"
    INDEPENDENT = "independent"


class TabPostF5Policy(StrEnum):
    NONE = "none"
    DATA_REFRESH = "data_refresh"


class TabRuntimeDelayPolicy(StrEnum):
    NONE = "none"
    WATCHLIST = "watchlist"
    STANDARD = "standard"
    SHELL_HEAVY = "shell_heavy"
    LHB_POOL = "lhb_pool"


class TabLoadReason(StrEnum):
    PLACEHOLDER_ACTION = "placeholder_action"
    TAB_SWITCH = "tab_switch"
    USER = "user"
    RESTORE_LAST_TAB = "restore_last_tab"
    SHELL_NAV = "shell_nav"
    COMMAND = "command"
    STOCK_SIGNAL_SOURCE = "stock_signal_source"
    BACKGROUND_PREWARM = "background_prewarm"
    PERF_MEMORY_PROBE = "perf_memory_probe"
    PERF_MEMORY_PROBE_CYCLE = "perf_memory_probe_cycle"
    STOCK_CANDIDATES_ANCHOR = "stock_candidates_anchor"
    SCREENSHOT = "screenshot"
    SOAK_LEAK_PROBE = "soak_leak_probe"
    STARTUP_GUARD_RESTORE = "startup_guard_restore"


INTERACTIVE_TAB_LOAD_REASONS: Final[frozenset[str]] = frozenset(
    {
        TabLoadReason.PLACEHOLDER_ACTION.value,
        TabLoadReason.TAB_SWITCH.value,
        TabLoadReason.USER.value,
        TabLoadReason.RESTORE_LAST_TAB.value,
        TabLoadReason.SHELL_NAV.value,
        TabLoadReason.COMMAND.value,
        TabLoadReason.STOCK_SIGNAL_SOURCE.value,
    }
)
PROBE_TAB_LOAD_REASONS: Final[frozenset[str]] = frozenset(
    {
        TabLoadReason.PERF_MEMORY_PROBE.value,
        TabLoadReason.PERF_MEMORY_PROBE_CYCLE.value,
    }
)


def normalize_tab_load_reason(reason: object) -> str:
    if isinstance(reason, TabLoadReason):
        return reason.value
    return str(reason or "").strip()


def is_interactive_tab_load_reason(reason: object) -> bool:
    return normalize_tab_load_reason(reason) in INTERACTIVE_TAB_LOAD_REASONS


@dataclass(frozen=True, slots=True, kw_only=True)
class DataLineageDefinition:
    source: str
    cache_refs: tuple[str, ...]
    provider: str = ""
    network_capable: bool = False
    fallback_or_degraded: bool | None = False
    include_runtime_status: bool = False
    include_provider_fault_tolerance: bool = False

    def as_runtime_defaults(self, view: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "view": view,
            "source": self.source,
        }
        if self.provider:
            result["provider"] = self.provider
        result.update(
            {
                "cache_refs": list(self.cache_refs),
                "network_capable": bool(self.network_capable),
                "triggered_network": None if self.network_capable else False,
                "fallback_or_degraded": self.fallback_or_degraded,
            }
        )
        if self.include_runtime_status:
            result.update(
                {
                    "updated_at": "",
                    "errors": [],
                    "warnings": [],
                }
            )
        if self.include_provider_fault_tolerance:
            result["provider_fault_tolerance"] = {}
        return result


@dataclass(frozen=True, slots=True, kw_only=True)
class DataLineageExclusion:
    reason: str
    description: str

    def as_runtime_defaults(self) -> dict[str, str]:
        return {
            "reason": self.reason,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class TabDefinition:
    key: str
    title: str
    group: str
    stack_order: int
    group_order: int
    startup_order: int
    icon_key: str
    module_name: str
    class_name: str
    legacy_attr: str
    constructor_profile: TabConstructorProfile
    warm_policy: TabWarmPolicy
    quote_policy: TabQuotePolicy
    f5_snapshot_policy: TabF5SnapshotPolicy
    post_f5_policy: TabPostF5Policy
    runtime_delay_policy: TabRuntimeDelayPolicy = TabRuntimeDelayPolicy.NONE
    runtime_delay_kwarg: str = ""
    constructor_defaults: tuple[tuple[str, Any], ...] = ()
    noninteractive_defaults: tuple[tuple[str, Any], ...] = ()
    health_probe_order: int | None = None
    lineage_order: int | None = None
    lineage: DataLineageDefinition | None = None
    lineage_exclusion: DataLineageExclusion | None = None

    def runtime_spec_metadata(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "group": self.group,
            "stack_order": self.stack_order,
            "group_order": self.group_order,
            "startup_order": self.startup_order,
            "icon_key": self.icon_key,
            "module_name": self.module_name,
            "class_name": self.class_name,
            "attr": self.legacy_attr,
            "constructor_profile": self.constructor_profile.value,
            "warm_policy": self.warm_policy.value,
            "quote_policy": self.quote_policy.value,
            "f5_snapshot_policy": self.f5_snapshot_policy.value,
            "post_f5_policy": self.post_f5_policy.value,
            "runtime_delay_policy": self.runtime_delay_policy.value,
            "runtime_delay_kwarg": self.runtime_delay_kwarg,
            "constructor_defaults": dict(self.constructor_defaults),
            "noninteractive_defaults": dict(self.noninteractive_defaults),
        }

    def constructor_default_kwargs(self) -> dict[str, Any]:
        return dict(self.constructor_defaults)

    def noninteractive_default_kwargs(self) -> dict[str, Any]:
        return dict(self.noninteractive_defaults)


TAB_DEFINITIONS: Final[tuple[TabDefinition, ...]] = (
    TabDefinition(
        key="watchlist",
        title="关注池",
        group="主工作台",
        stack_order=0,
        group_order=10,
        startup_order=0,
        icon_key="watchlist",
        module_name="ui.tabs.watchlist_tab",
        class_name="WatchlistTab",
        legacy_attr="tab_watchlist",
        constructor_profile=TabConstructorProfile.WATCHLIST,
        warm_policy=TabWarmPolicy.WIDGET_PREWARM,
        quote_policy=TabQuotePolicy.A_SHARE_REALTIME,
        f5_snapshot_policy=TabF5SnapshotPolicy.SNAPSHOT,
        post_f5_policy=TabPostF5Policy.NONE,
        runtime_delay_policy=TabRuntimeDelayPolicy.WATCHLIST,
        health_probe_order=0,
        lineage_order=9,
        lineage=DataLineageDefinition(
            source="watchlist_vm + global_store.quotes",
            provider="watchlist_vm/global_store",
            cache_refs=("watchlist store", "global_store.quotes"),
            network_capable=True,
            fallback_or_degraded=None,
            include_runtime_status=True,
            include_provider_fault_tolerance=True,
        ),
    ),
    TabDefinition(
        key="lhb",
        title="龙虎榜",
        group="主工作台",
        stack_order=1,
        group_order=15,
        startup_order=8,
        icon_key="lhb",
        module_name="ui.tabs.lhb_tab",
        class_name="LhbTab",
        legacy_attr="tab_lhb",
        constructor_profile=TabConstructorProfile.LHB,
        warm_policy=TabWarmPolicy.WIDGET_PREWARM,
        quote_policy=TabQuotePolicy.A_SHARE_REALTIME,
        f5_snapshot_policy=TabF5SnapshotPolicy.INDEPENDENT,
        post_f5_policy=TabPostF5Policy.NONE,
        runtime_delay_policy=TabRuntimeDelayPolicy.LHB_POOL,
        runtime_delay_kwarg="initial_load_delay_ms",
        constructor_defaults=(("autoload_pool", False),),
        health_probe_order=5,
        lineage_order=5,
        lineage=DataLineageDefinition(
            source="LhbPoolManager cache + local_quote_snapshot",
            provider="LhbPoolManager",
            cache_refs=("data/Cache/lhb_pool_30d.json", "global_store.quotes", "local_tdx_cache"),
            network_capable=True,
        ),
    ),
    TabDefinition(
        key="asian_market",
        title="亚洲寡头",
        group="主工作台",
        stack_order=2,
        group_order=20,
        startup_order=9,
        icon_key="asian_market",
        module_name="ui.tabs.asian_market_tab",
        class_name="AsianMarketTab",
        legacy_attr="tab_asian_market",
        constructor_profile=TabConstructorProfile.DATA_PROVIDER_PARENT,
        warm_policy=TabWarmPolicy.WIDGET_PREWARM,
        quote_policy=TabQuotePolicy.NONE,
        f5_snapshot_policy=TabF5SnapshotPolicy.SNAPSHOT,
        post_f5_policy=TabPostF5Policy.NONE,
        runtime_delay_policy=TabRuntimeDelayPolicy.STANDARD,
        runtime_delay_kwarg="local_cache_delay_ms",
        noninteractive_defaults=(("local_cache_delay_ms", 1),),
        health_probe_order=1,
        lineage_order=1,
        lineage=DataLineageDefinition(
            source="asian_market local cache + realtime quote cache",
            provider="AsianMarketWorker/yfinance/cache",
            cache_refs=(
                "data/Cache/asian_klines_latest.json",
                "data/Cache/asian_rt_latest.json",
                "global_store.quotes",
            ),
            network_capable=True,
            fallback_or_degraded=None,
            include_runtime_status=True,
            include_provider_fault_tolerance=True,
        ),
    ),
    TabDefinition(
        key="na_daily",
        title="北美战报",
        group="主工作台",
        stack_order=3,
        group_order=30,
        startup_order=3,
        icon_key="na_daily",
        module_name="ui.tabs.na_daily_tab",
        class_name="NADailyTab",
        legacy_attr="tab_na_daily",
        constructor_profile=TabConstructorProfile.DATA_PROVIDER_PARENT,
        warm_policy=TabWarmPolicy.WIDGET_PREWARM,
        quote_policy=TabQuotePolicy.A_SHARE_REALTIME,
        f5_snapshot_policy=TabF5SnapshotPolicy.SNAPSHOT,
        post_f5_policy=TabPostF5Policy.NONE,
        runtime_delay_policy=TabRuntimeDelayPolicy.STANDARD,
        runtime_delay_kwarg="runtime_start_delay_ms",
        health_probe_order=2,
        lineage_order=2,
        lineage=DataLineageDefinition(
            source="daily report markdown/json + global_store.quotes",
            provider="NADailyTab local report reader",
            cache_refs=("daily report output:report json/markdown", "global_store.quotes"),
            network_capable=True,
            fallback_or_degraded=None,
            include_runtime_status=True,
            include_provider_fault_tolerance=True,
        ),
    ),
    TabDefinition(
        key="stock_candidates",
        title="综合候选",
        group="主工作台",
        stack_order=4,
        group_order=32,
        startup_order=10,
        icon_key="stock_candidates",
        module_name="ui.tabs.stock_candidate_tab",
        class_name="StockCandidateTab",
        legacy_attr="tab_stock_candidates",
        constructor_profile=TabConstructorProfile.DATA_PROVIDER_PARENT,
        warm_policy=TabWarmPolicy.WIDGET_PREWARM,
        quote_policy=TabQuotePolicy.A_SHARE_REALTIME,
        f5_snapshot_policy=TabF5SnapshotPolicy.INDEPENDENT,
        post_f5_policy=TabPostF5Policy.NONE,
        runtime_delay_policy=TabRuntimeDelayPolicy.STANDARD,
        runtime_delay_kwarg="runtime_start_delay_ms",
        health_probe_order=3,
        lineage_order=0,
        lineage=DataLineageDefinition(
            source="workspace_stock_context",
            provider="workspace_stock_context",
            cache_refs=("global_store.quotes", "workspace.collect_stock_context"),
            include_runtime_status=True,
        ),
    ),
    TabDefinition(
        key="ai_industry_chain",
        title="AI产业链",
        group=INFO_SOURCE_GROUP,
        stack_order=5,
        group_order=15,
        startup_order=2,
        icon_key="ai_industry_chain",
        module_name="ui.tabs.ai_industry_chain_tab",
        class_name="AIIndustryChainTab",
        legacy_attr="tab_ai_industry_chain",
        constructor_profile=TabConstructorProfile.DATA_PROVIDER_PARENT,
        warm_policy=TabWarmPolicy.WIDGET_PREWARM,
        quote_policy=TabQuotePolicy.NONE,
        f5_snapshot_policy=TabF5SnapshotPolicy.INDEPENDENT,
        post_f5_policy=TabPostF5Policy.DATA_REFRESH,
        runtime_delay_policy=TabRuntimeDelayPolicy.STANDARD,
        runtime_delay_kwarg="runtime_start_delay_ms",
        health_probe_order=4,
        lineage_order=3,
        lineage=DataLineageDefinition(
            source="AI industry chain workbook + local market data",
            provider="AIIndustryChainTab workbook reader",
            cache_refs=("AI industry chain workbook", "local market data provider", "global_store.quotes"),
            fallback_or_degraded=None,
            include_runtime_status=True,
            include_provider_fault_tolerance=True,
        ),
    ),
    TabDefinition(
        key="scan",
        title="VCP扫描",
        group=INFO_SOURCE_GROUP,
        stack_order=6,
        group_order=10,
        startup_order=4,
        icon_key="scan",
        module_name="ui.tabs.scan_tab",
        class_name="ScanTab",
        legacy_attr="tab_scan",
        constructor_profile=TabConstructorProfile.SCAN,
        warm_policy=TabWarmPolicy.WIDGET_PREWARM,
        quote_policy=TabQuotePolicy.NONE,
        f5_snapshot_policy=TabF5SnapshotPolicy.INDEPENDENT,
        post_f5_policy=TabPostF5Policy.DATA_REFRESH,
        runtime_delay_policy=TabRuntimeDelayPolicy.STANDARD,
        runtime_delay_kwarg="initial_cache_load_delay_ms",
        health_probe_order=6,
        lineage_order=4,
        lineage=DataLineageDefinition(
            source="DataStore.scan_cache",
            provider="scan_runtime_service",
            cache_refs=("data/vcp_hunter.db:kv_store.scan_cache", "data/scan_cache.json.migrated"),
            include_runtime_status=True,
            include_provider_fault_tolerance=True,
        ),
    ),
    TabDefinition(
        key="foreign_block",
        title="大宗交易",
        group=INFO_SOURCE_GROUP,
        stack_order=7,
        group_order=20,
        startup_order=5,
        icon_key="foreign_block",
        module_name="ui.tabs.foreign_block_trade_tab",
        class_name="ForeignBlockTradeTab",
        legacy_attr="tab_foreign_block",
        constructor_profile=TabConstructorProfile.DATA_PROVIDER_PARENT,
        warm_policy=TabWarmPolicy.WIDGET_PREWARM,
        quote_policy=TabQuotePolicy.NONE,
        f5_snapshot_policy=TabF5SnapshotPolicy.INDEPENDENT,
        post_f5_policy=TabPostF5Policy.DATA_REFRESH,
        runtime_delay_policy=TabRuntimeDelayPolicy.STANDARD,
        runtime_delay_kwarg="initial_cache_load_delay_ms",
        noninteractive_defaults=(("autoload", False),),
        health_probe_order=7,
        lineage_order=6,
        lineage=DataLineageDefinition(
            source="foreign_block_trade_latest.json",
            cache_refs=("data/Cache/foreign_block_trade_latest.json",),
            network_capable=True,
        ),
    ),
    TabDefinition(
        key="earnings",
        title="业绩异动",
        group=INFO_SOURCE_GROUP,
        stack_order=8,
        group_order=30,
        startup_order=6,
        icon_key="earnings",
        module_name="ui.tabs.earnings_tab",
        class_name="EarningsTab",
        legacy_attr="tab_earnings",
        constructor_profile=TabConstructorProfile.DATA_PROVIDER_PARENT,
        warm_policy=TabWarmPolicy.WIDGET_PREWARM,
        quote_policy=TabQuotePolicy.NONE,
        f5_snapshot_policy=TabF5SnapshotPolicy.INDEPENDENT,
        post_f5_policy=TabPostF5Policy.DATA_REFRESH,
        runtime_delay_policy=TabRuntimeDelayPolicy.SHELL_HEAVY,
        runtime_delay_kwarg="runtime_start_delay_ms",
        health_probe_order=8,
        lineage_order=7,
        lineage=DataLineageDefinition(
            source="earnings_state / local display window",
            cache_refs=("data/vcp_hunter.db:earnings_state", "global_store.quotes"),
            network_capable=True,
        ),
    ),
    TabDefinition(
        key="fund_holdings",
        title="基金持仓",
        group=INFO_SOURCE_GROUP,
        stack_order=9,
        group_order=40,
        startup_order=7,
        icon_key="fund_holdings",
        module_name="ui.tabs.fund_holdings_tab",
        class_name="FundHoldingsTab",
        legacy_attr="tab_fund_holdings",
        constructor_profile=TabConstructorProfile.FUND_HOLDINGS,
        warm_policy=TabWarmPolicy.WIDGET_PREWARM,
        quote_policy=TabQuotePolicy.NONE,
        f5_snapshot_policy=TabF5SnapshotPolicy.INDEPENDENT,
        post_f5_policy=TabPostF5Policy.DATA_REFRESH,
        runtime_delay_policy=TabRuntimeDelayPolicy.SHELL_HEAVY,
        runtime_delay_kwarg="initial_load_delay_ms",
        constructor_defaults=(("autoload", False),),
        health_probe_order=9,
        lineage_order=8,
        lineage=DataLineageDefinition(
            source="fund_holdings_store",
            cache_refs=("data/vcp_hunter.db:fund holdings tables", "global_store.quotes"),
            network_capable=True,
        ),
    ),
    TabDefinition(
        key="system_log",
        title="系统日志",
        group="系统",
        stack_order=10,
        group_order=10,
        startup_order=1,
        icon_key="system_log",
        module_name="ui.tabs.log_tab",
        class_name="LogTab",
        legacy_attr="tab_log",
        constructor_profile=TabConstructorProfile.WORKSPACE_PARENT,
        warm_policy=TabWarmPolicy.WIDGET_PREWARM,
        quote_policy=TabQuotePolicy.NONE,
        f5_snapshot_policy=TabF5SnapshotPolicy.NONE,
        post_f5_policy=TabPostF5Policy.NONE,
        health_probe_order=10,
        lineage_exclusion=DataLineageExclusion(
            reason="non_data_tab",
            description="system_log is an operational log surface, not a data table or upstream data source.",
        ),
    ),
)


def _build_registry_index() -> Mapping[str, TabDefinition]:
    by_key = {definition.key: definition for definition in TAB_DEFINITIONS}
    if len(by_key) != len(TAB_DEFINITIONS):
        raise ValueError("duplicate tab registry key")
    for field_name in ("stack_order", "startup_order"):
        values = [getattr(definition, field_name) for definition in TAB_DEFINITIONS]
        if len(values) != len(set(values)):
            raise ValueError(f"duplicate tab registry {field_name}")
    return MappingProxyType(by_key)


TAB_DEFINITIONS_BY_KEY: Final[Mapping[str, TabDefinition]] = _build_registry_index()


# Only hard data dependencies belong here.  The order itself remains driven by
# ``startup_order`` so diagnostics, tests, and the workspace share one source
# of truth.  Watchlist is loaded first as a reactive consumer; its source
# events converge while the remaining producers hydrate.
TAB_PRELOAD_DEPENDENCIES: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "foreign_block": ("ai_industry_chain",),
        "earnings": ("ai_industry_chain",),
        "fund_holdings": ("ai_industry_chain",),
        "lhb": ("ai_industry_chain",),
        "stock_candidates": (
            "watchlist",
            "ai_industry_chain",
            "na_daily",
            "scan",
            "foreign_block",
            "earnings",
            "fund_holdings",
            "lhb",
            "asian_market",
        ),
    }
)


def _validate_preload_dependencies() -> None:
    startup_positions = {definition.key: definition.startup_order for definition in TAB_DEFINITIONS}
    for key, dependencies in TAB_PRELOAD_DEPENDENCIES.items():
        if key not in startup_positions:
            raise ValueError(f"unknown preload dependency target: {key}")
        for dependency in dependencies:
            if dependency not in startup_positions:
                raise ValueError(f"unknown preload dependency: {key} <- {dependency}")
            if startup_positions[dependency] >= startup_positions[key]:
                raise ValueError(f"preload dependency order violation: {key} <- {dependency}")


_validate_preload_dependencies()


def get_tab_definition(key: object) -> TabDefinition | None:
    return TAB_DEFINITIONS_BY_KEY.get(str(key or "").strip())


def create_tab_lineage_service(key: object, **kwargs):
    """Build a tab lineage service from registry-owned static metadata."""
    definition = get_tab_definition(key)
    if definition is None or definition.lineage is None:
        raise KeyError(f"tab has no data-lineage definition: {str(key or '').strip()}")

    from app.services.tab_data_lineage_service import TabDataLineageService

    return TabDataLineageService(
        key=definition.key,
        source=definition.lineage.source,
        provider=definition.lineage.provider,
        cache_refs=definition.lineage.cache_refs,
        **kwargs,
    )


def startup_tab_keys() -> tuple[str, ...]:
    return tuple(definition.key for definition in sorted(TAB_DEFINITIONS, key=lambda item: item.startup_order))


def widget_prewarm_tab_keys() -> frozenset[str]:
    return frozenset(
        definition.key for definition in TAB_DEFINITIONS if definition.warm_policy is TabWarmPolicy.WIDGET_PREWARM
    )


def preload_dependencies_for(key: object) -> tuple[str, ...]:
    return TAB_PRELOAD_DEPENDENCIES.get(str(key or "").strip(), ())


def health_probe_tab_keys() -> tuple[str, ...]:
    definitions = (definition for definition in TAB_DEFINITIONS if definition.health_probe_order is not None)
    return tuple(definition.key for definition in sorted(definitions, key=lambda item: int(item.health_probe_order or 0)))


def lineage_tab_definitions() -> tuple[TabDefinition, ...]:
    definitions = (definition for definition in TAB_DEFINITIONS if definition.lineage is not None)
    return tuple(sorted(definitions, key=lambda item: int(item.lineage_order or 0)))


def lineage_exclusion_tab_definitions() -> tuple[TabDefinition, ...]:
    return tuple(definition for definition in TAB_DEFINITIONS if definition.lineage_exclusion is not None)
