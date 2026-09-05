# -*- coding: utf-8 -*-

"""基金持仓领域导出。

对比规则没有 I/O，可在轻量 UI shell 中安全导入；SQLite store 和同步服务
则会创建全局单例，不应因为只解析一个 Tab 类而抢占 GUI 线程。保留原有
公有导出，但把两个带运行时副作用的模块延迟到实际调用方需要它们时。
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from domains.fund_holdings.compare import *  # noqa: F401,F403

if TYPE_CHECKING:
    from domains.fund_holdings.store import FundHoldingsStore, fund_holdings_store
    from domains.fund_holdings.sync import FundHoldingsSyncService, fund_holdings_sync_service

__all__ = [
    "FundHoldingsStore",
    "FundHoldingsSyncService",
    "fund_holdings_store",
    "fund_holdings_sync_service",
]

_LAZY_EXPORTS = {
    "FundHoldingsStore": ("domains.fund_holdings.store", "FundHoldingsStore"),
    "fund_holdings_store": ("domains.fund_holdings.store", "fund_holdings_store"),
    "FundHoldingsSyncService": ("domains.fund_holdings.sync", "FundHoldingsSyncService"),
    "fund_holdings_sync_service": ("domains.fund_holdings.sync", "fund_holdings_sync_service"),
}


def __getattr__(name: str):
    """Resolve store/sync exports only at their explicit use boundary."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
