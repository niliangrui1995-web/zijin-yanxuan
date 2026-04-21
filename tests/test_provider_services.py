from __future__ import annotations

from infra.market_data import AdjustmentService, LocalHistoryProvider, RealtimeQuoteProvider
from vcp.data_provider import TdxDataProvider


def test_provider_lazy_services_available_on_bare_instance():
    provider = TdxDataProvider.__new__(TdxDataProvider)

    adjustment = provider._get_adjustment_service()
    history = provider._get_local_history_provider()
    realtime = provider._get_realtime_quote_provider()

    assert isinstance(adjustment, AdjustmentService)
    assert isinstance(history, LocalHistoryProvider)
    assert isinstance(realtime, RealtimeQuoteProvider)
