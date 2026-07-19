from __future__ import annotations

from vcp.data_provider_local import (
    apply_forward_adjustment as apply_forward_adjustment_impl,
)
from vcp.data_provider_local import (
    get_market_code,
    load_local_gbbq,
    load_local_gbbq_for_code,
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

    def load_local_gbbq_for_code(self, code: str):
        provider = self.provider
        return load_local_gbbq_for_code(
            provider.tdx_vipdoc,
            provider.gbbq_cache_file,
            provider.legacy_gbbq_cache_file,
            getattr(provider, "_local_gbbq_code_cache", {}),
            code,
        )

    @staticmethod
    def get_market_code(stock_code):
        return get_market_code(stock_code)

    def apply_forward_adjustment(
        self,
        api,
        market,
        code,
        df,
        *,
        local_gbbq: dict | None = None,
        cancellation_token=None,
    ):
        cancellation_kwargs = (
            {"cancellation_token": cancellation_token} if cancellation_token is not None else {}
        )
        return apply_forward_adjustment_impl(
            api,
            market,
            code,
            df,
            getattr(self.provider, "_local_gbbq", {}) if local_gbbq is None else local_gbbq,
            **cancellation_kwargs,
        )
