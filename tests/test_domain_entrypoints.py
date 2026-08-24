from __future__ import annotations

import pytest

import domains
import domains.earnings as earnings_domain
from core.market_calendar import MarketCalendar as LegacyMarketCalendar
from core.quote_dispatcher import publish_rt_quotes as legacy_publish_rt_quotes
from domains.earnings import EarningsEngine
from domains.earnings.scheduler import EarningsScheduler as DeprecatedDomainEarningsScheduler
from domains.market_calendar import MarketCalendar
from domains.quotes import publish_rt_quotes
from domains.scan import IndicatorService, RpsService
from domains.watchlist import watchlist_vm as domain_watchlist_vm
from earnings.engine import EarningsEngine as LegacyEarningsEngine
from earnings.scheduler import EarningsScheduler as LegacyEarningsScheduler
from ui.viewmodels.watchlist_vm import watchlist_vm as legacy_watchlist_vm
from vcp.indicator_service import IndicatorService as LegacyIndicatorService
from vcp.rps_service import RpsService as LegacyRpsService


def test_legacy_scan_service_paths_forward_to_domain_entrypoints():
    assert LegacyIndicatorService is IndicatorService
    assert LegacyRpsService is RpsService


def test_legacy_earnings_and_market_calendar_paths_are_thin_shims():
    assert LegacyEarningsEngine is EarningsEngine
    assert LegacyEarningsScheduler is DeprecatedDomainEarningsScheduler
    assert LegacyMarketCalendar is MarketCalendar


def test_domains_does_not_reexport_ui_backed_earnings_scheduler():
    assert "EarningsScheduler" not in domains.__all__
    assert "EarningsScheduler" not in earnings_domain.__all__
    with pytest.raises(AttributeError):
        getattr(domains, "EarningsScheduler")
    with pytest.raises(AttributeError):
        getattr(earnings_domain, "EarningsScheduler")


def test_legacy_quotes_and_watchlist_paths_forward_to_domain_entrypoints():
    assert legacy_publish_rt_quotes is publish_rt_quotes
    assert legacy_watchlist_vm is domain_watchlist_vm
