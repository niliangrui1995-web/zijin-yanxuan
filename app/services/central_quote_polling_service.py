# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable

from app.services.runtime_services import load_local_tdx_capital_snapshot
from app.services.scan_runtime_service import batch_get_finance_info
from app.services.ui_quote_service import enrich_quotes_with_finance
from core.logger import get_logger
from infra.market_data.provider_ports import RealtimeQuotePort

log = get_logger(__name__)


def _has_valid_share_capital(entry: dict | None) -> bool:
    try:
        return float((entry or {}).get("zongguben") or (entry or {}).get("_zongguben") or 0) > 0
    except (TypeError, ValueError):
        return False


class CentralQuotePoller:
    """Build realtime quote payloads without depending on Qt timers or QObject state."""

    def __init__(
        self,
        data_provider: RealtimeQuotePort,
        *,
        missing_finance_codes: Callable[[set[str]], list[str]] | None = None,
        finance_lookup: Callable[[list[str]], dict] | None = None,
        quote_enricher: Callable[[dict, dict], dict] | None = None,
    ):
        self.data_provider = data_provider
        self._missing_finance_codes = missing_finance_codes
        self._finance_lookup = finance_lookup or self._lookup_finance_with_local_tdx
        self._quote_enricher = quote_enricher or enrich_quotes_with_finance

    def _lookup_finance_with_local_tdx(self, codes: list[str]) -> dict:
        normalized_codes = [str(code or "").strip().zfill(6) for code in dict.fromkeys(codes or [])]
        normalized_codes = [code for code in normalized_codes if len(code) == 6 and code.isdigit()]
        if not normalized_codes:
            return {}

        finance_data: dict[str, dict] = {}
        tdx_vipdoc = getattr(self.data_provider, "tdx_vipdoc", None)
        if tdx_vipdoc:
            try:
                finance_data.update(load_local_tdx_capital_snapshot(normalized_codes, tdx_vipdoc))
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.debug(f"[报价站] 读取本地通达信股本失败: {exc}")

        missing_codes = [code for code in normalized_codes if not _has_valid_share_capital(finance_data.get(code))]
        if missing_codes:
            finance_data.update(batch_get_finance_info(missing_codes) or {})

        return finance_data

    def missing_finance_codes(self, codes: set[str]) -> list[str]:
        if not callable(self._missing_finance_codes):
            return []
        try:
            return list(self._missing_finance_codes(codes) or [])
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            log.debug(f"[报价站] 读取股本缺口失败: {exc}")
            return []

    def fetch_payload(self, codes: set[str]) -> dict:
        quotes = self.data_provider.fetch_realtime_quotes_batch(list(codes))
        quote_request_stats = self.get_quote_request_stats()
        finance_data = {}

        finance_codes = self.missing_finance_codes(codes)
        if finance_codes:
            try:
                finance_data = self._finance_lookup(finance_codes) or {}
            except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.debug(f"[报价站] 批量补股本失败: {exc}")

        return {
            "quotes": self._quote_enricher(quotes, finance_data),
            "finance_data": finance_data,
            "provider_stats": self.get_runtime_stats(),
            "quote_request_stats": quote_request_stats,
        }

    def get_runtime_stats(self) -> dict:
        stats_getter = getattr(self.data_provider, "get_realtime_runtime_stats", None)
        if callable(stats_getter):
            try:
                stats = stats_getter() or {}
                return stats if isinstance(stats, dict) else {}
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                log.debug(f"[报价站] 读取运行态统计失败: {exc}")
        return {}

    def get_quote_request_stats(self) -> dict:
        stats_getter = getattr(self.data_provider, "get_quote_request_stats", None)
        if callable(stats_getter):
            try:
                stats = stats_getter() or {}
                return stats if isinstance(stats, dict) else {}
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                log.debug(f"[报价站] 读取报价请求统计失败: {exc}")
        return {}

    def compact_runtime_caches(self) -> dict:
        compact = getattr(self.data_provider, "compact_runtime_caches", None)
        if callable(compact):
            try:
                stats = compact() or {}
                return stats if isinstance(stats, dict) else {}
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                log.debug(f"[报价站] 运行时缓存清理失败: {exc}")
        return {}

    def protect_against_thread_anomaly(self, pytdx_threads: int) -> bool:
        protect = getattr(self.data_provider, "protect_against_thread_anomaly", None)
        if callable(protect):
            try:
                return bool(protect(pytdx_threads))
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                log.debug(f"[报价站] pytdx 线程防护失败: {exc}")
        return False

    def enter_realtime_cooldown(self, reason: str, *, cooldown_sec: int) -> None:
        enter_cooldown = getattr(self.data_provider, "enter_realtime_cooldown", None)
        if callable(enter_cooldown):
            try:
                enter_cooldown(reason, cooldown_sec=cooldown_sec)
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                log.debug(f"[报价站] 触发 provider 冷却失败: {exc}")

    def is_online(self) -> bool:
        is_online = getattr(self.data_provider, "is_online", None)
        return bool(is_online()) if callable(is_online) else False
