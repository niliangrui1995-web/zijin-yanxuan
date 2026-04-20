from __future__ import annotations

from vcp.adjustment_service import AdjustmentService
from vcp.data_provider import TdxDataProvider
from vcp.local_history_provider import LocalHistoryProvider
from vcp.realtime_quote_provider import RealtimeQuoteProvider


def test_provider_lazy_services_available_on_bare_instance():
    provider = TdxDataProvider.__new__(TdxDataProvider)

    adjustment = provider._get_adjustment_service()
    history = provider._get_local_history_provider()
    realtime = provider._get_realtime_quote_provider()

    assert isinstance(adjustment, AdjustmentService)
    assert isinstance(history, LocalHistoryProvider)
    assert isinstance(realtime, RealtimeQuoteProvider)
