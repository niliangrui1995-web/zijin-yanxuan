from __future__ import annotations

from vcp.data_provider_local import (
    apply_forward_adjustment as apply_forward_adjustment_impl,
)
from vcp.data_provider_local import (
    get_market_code,
    load_local_gbbq,
)


class AdjustmentService:
    """复权与股本数据服务。"""

    def __init__(self, provider) -> None:
        self.provider = provider

    def load_local_gbbq(self, force: bool = False):
        provider = self.provider
        provider._local_gbbq = load_local_gbbq(
            provider.tdx_vipdoc,
            provider.gbbq_cache_file,
            provider.legacy_gbbq_cache_file,
            provider._local_gbbq,
            force=force,
        )
        return provider._local_gbbq

    @staticmethod
    def get_market_code(stock_code):
        return get_market_code(stock_code)

    def apply_forward_adjustment(self, api, market, code, df):
        return apply_forward_adjustment_impl(
            api,
            market,
            code,
            df,
            getattr(self.provider, "_local_gbbq", {}),
        )
