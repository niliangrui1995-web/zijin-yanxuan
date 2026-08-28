# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path

from app.services.stock_candidate_builder_service import build_stock_candidate_rows
from app.services.stock_context_model_service import StockContextReadPolicy, StockContextSnapshot
from app.services.stock_context_query_service import GENERAL_STOCK_CONTEXT_SOURCE_KEYS, StockContextQueryService
from app.services.stock_context_snapshot_service import load_lhb_cached_realtime_projection
from core.logger import get_logger
from infra.storage.na_daily_repository import NA_DAILY_CACHE_FILE
from infra.storage.stock_context_repository import load_named_cache_rows
from ui.workspaces.tab_capabilities import (
    F5OffMarketQuoteUniverseCapability,
    QuoteUniverseCapability,
    RealtimeQuoteSourceProjectionCapability,
)
from ui.workspaces.tab_registry import TabPostF5Policy, TabQuotePolicy

log = get_logger(__name__)
_HEADLESS_CACHE_SOURCE_KEYS = frozenset({"lhb", "na_daily", "stock_candidates"})


def _iter_codes(value) -> Iterable[object]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)):
        return (value,)
    try:
        return iter(value)
    except TypeError:
        return ()


def _code_stats(value) -> tuple[set[str], int, int, int]:
    codes: set[str] = set()
    raw_count = 0
    valid_count = 0
    invalid_count = 0
    for raw_code in _iter_codes(value):
        raw_count += 1
        code = str(raw_code or "").strip()
        if len(code) != 6 or not code.isdigit():
            invalid_count += 1
            continue
        valid_count += 1
        codes.add(code)
    return codes, raw_count, valid_count, invalid_count


def _source_projection(codes=(), *, status: str, reason: str = "", **extra) -> dict:
    return {
        "codes": tuple(_iter_codes(codes)),
        "status": str(status or "degraded"),
        "reason": str(reason or ""),
        **extra,
    }


class QuoteUniverseService:
    """汇总中央 A 股行情轮询池，并说明来源、去重及缓存降级状态。"""

    def __init__(
        self,
        workspace,
        *,
        context_snapshot_reader: Callable[[], StockContextSnapshot | None] | None = None,
        context_snapshot_primer: Callable[..., object] | None = None,
        headless_source_readers: Mapping[str, Callable[[], Mapping | Iterable[object] | None]] | None = None,
    ):
        self._workspace = workspace
        self._context_snapshot_reader = context_snapshot_reader
        self._context_snapshot_primer = context_snapshot_primer
        self._headless_cache: dict[str, dict] = {}
        self._headless_context_primed = False
        self._last_coverage_signature = None
        self._headless_source_readers: dict[str, Callable[[], Mapping | Iterable[object] | None]] = {
            "lhb": self._read_lhb_headless_projection,
            "na_daily": self._read_na_daily_headless_projection,
            "stock_candidates": self._read_stock_candidates_headless_projection,
        }
        self._headless_source_readers.update(
            {
                str(key or "").strip(): reader
                for key, reader in (headless_source_readers or {}).items()
                if str(key or "").strip() in _HEADLESS_CACHE_SOURCE_KEYS and callable(reader)
            }
        )

    def _tab_specs(self) -> list[dict]:
        workspace = self._workspace
        specs = getattr(workspace, "_tab_specs", None)
        if specs is not None:
            return list(specs)
        tab_specs = getattr(workspace, "tab_specs", None)
        return list(tab_specs() or []) if callable(tab_specs) else []

    def _realtime_tab_keys(self) -> tuple[str, ...]:
        specs = self._tab_specs()
        if not specs:
            return ()

        keys = []
        for spec in specs:
            key = str(spec.get("key", "")).strip()
            if not key:
                continue
            policy = str(spec.get("quote_policy") or "").strip()
            if policy == TabQuotePolicy.A_SHARE_REALTIME.value:
                keys.append(key)
        return tuple(keys)

    def _eligible_realtime_tab_keys(self) -> tuple[str, ...]:
        """Loaded quote sources never wait for visual preload completion.

        Background preload remains deliberately lazy for widgets, but the
        central quote registry must not silently shrink to the visible page.
        Unloaded realtime sources are handled by separate headless cache
        projections.
        """

        return self._realtime_tab_keys()

    def _f5_off_market_tab_keys(self) -> tuple[str, ...]:
        keys = []
        for spec in self._tab_specs():
            key = str(spec.get("key", "")).strip()
            if not key:
                continue
            quote_policy = str(spec.get("quote_policy") or "").strip()
            post_f5_policy = str(spec.get("post_f5_policy") or "").strip()
            if (
                quote_policy == TabQuotePolicy.A_SHARE_REALTIME.value
                or post_f5_policy == TabPostF5Policy.DATA_REFRESH.value
            ):
                keys.append(key)
        return tuple(keys)

    @staticmethod
    def _loaded_projection(tab) -> dict:
        if not isinstance(tab, QuoteUniverseCapability):
            return _source_projection((), status="degraded", reason="loaded_tab_missing_quote_capability")
        if isinstance(tab, RealtimeQuoteSourceProjectionCapability):
            source_projection_reader = tab.get_realtime_quote_source_projection
            try:
                source_projection = source_projection_reader()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return _source_projection((), status="degraded", reason="loaded_tab_quote_projection_error")
            if not isinstance(source_projection, Mapping):
                return _source_projection((), status="degraded", reason="loaded_tab_quote_projection_invalid")
            return _source_projection(
                source_projection.get("codes", ()),
                status=str(source_projection.get("status") or "degraded"),
                reason=str(source_projection.get("reason") or ""),
            )
        try:
            codes = tab.get_realtime_quote_codes()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return _source_projection((), status="degraded", reason="loaded_tab_quote_codes_error")
        return _source_projection(codes, status="registered", origin="loaded_tab")

    @staticmethod
    def _headless_fallback_for_pending_loaded_projection(
        loaded_projection: Mapping,
        headless_projection: Mapping,
    ) -> dict | None:
        """Keep an explicitly pending tab from briefly shrinking the quote universe.

        The source tab must explicitly report ``pending``.  A ready, genuinely
        empty pool is authoritative and must never be repopulated from a stale
        cache.  The fallback diagnostic is deliberately degraded so cached
        registration cannot look like a fully ready tab.
        """

        if str(loaded_projection.get("status") or "").strip() != "pending":
            return None
        headless_codes, _raw_count, _valid_count, _invalid_count = _code_stats(
            headless_projection.get("codes", ())
        )
        if not headless_codes:
            return None
        reasons = [str(loaded_projection.get("reason") or "").strip() or "loaded_tab_pending"]
        reasons.append("headless_cache_fallback")
        headless_reason = str(headless_projection.get("reason") or "").strip()
        if headless_reason:
            reasons.append(headless_reason)
        return _source_projection(
            headless_projection.get("codes", ()),
            status="registered_degraded",
            reason=";".join(reasons),
        )

    def _prime_headless_context_snapshots(self) -> None:
        if self._headless_context_primed:
            return
        self._headless_context_primed = True
        primer = self._context_snapshot_primer
        if not callable(primer):
            return
        try:
            primer(include_fund=True, include_lhb=True)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            log.debug("[行情覆盖] 跳过上下文缓存预热: %s", exc)

    @staticmethod
    def _read_lhb_headless_projection() -> dict:
        projection = load_lhb_cached_realtime_projection()
        if not isinstance(projection, Mapping):
            return _source_projection((), status="degraded", reason="lhb_cache_invalid")
        return _source_projection(
            projection.get("codes", ()),
            status=str(projection.get("status") or "degraded"),
            reason=str(projection.get("reason") or ""),
        )

    @staticmethod
    def _read_na_daily_headless_projection() -> dict:
        try:
            rows = load_named_cache_rows("na_daily_latest.json")
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return _source_projection((), status="degraded", reason="na_daily_cache_invalid")
        codes = tuple(
            row.get("代码")
            for row in rows
            if isinstance(row, Mapping)
        )
        if codes:
            return _source_projection(codes, status="registered")
        reason = "na_daily_cache_missing" if not Path(NA_DAILY_CACHE_FILE).exists() else "na_daily_cache_empty"
        return _source_projection((), status="degraded", reason=reason)

    def _headless_candidate_snapshot(self) -> StockContextSnapshot:
        snapshot_reader = self._context_snapshot_reader
        snapshot = None
        if callable(snapshot_reader):
            try:
                snapshot = snapshot_reader()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                snapshot = None
        if not isinstance(snapshot, StockContextSnapshot):
            return StockContextSnapshot(available_sources=frozenset(GENERAL_STOCK_CONTEXT_SOURCE_KEYS))
        return replace(
            snapshot,
            available_sources=frozenset(snapshot.available_sources | GENERAL_STOCK_CONTEXT_SOURCE_KEYS),
        )

    def _read_stock_candidates_headless_projection(self) -> dict:
        self._prime_headless_context_snapshots()
        try:
            snapshot = self._headless_candidate_snapshot()
            policy = StockContextReadPolicy.build(
                allow_lhb_cache_compute=False,
                allow_fund_store_query=False,
                sources=GENERAL_STOCK_CONTEXT_SOURCE_KEYS,
            )
            context = StockContextQueryService(
                snapshot,
                engine=getattr(self._workspace, "engine", None),
            ).query_by_code(policy)
            rows = build_stock_candidate_rows(context, tab_titles=snapshot.tab_titles)
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
            return _source_projection((), status="degraded", reason="stock_candidates_cache_invalid")
        codes = tuple(row.get("代码") for row in rows if isinstance(row, Mapping))
        pending_sources = sorted(
            set(snapshot.loading_sources) & {"fund_holdings", "lhb"}
        )
        if codes:
            if pending_sources:
                return _source_projection(
                    codes,
                    status="registered_degraded",
                    reason="stock_context_snapshot_pending:" + ",".join(pending_sources),
                )
            return _source_projection(codes, status="registered")
        if pending_sources:
            return _source_projection(
                (),
                status="degraded",
                reason="stock_context_snapshot_pending:" + ",".join(pending_sources),
            )
        reason = "stock_candidates_cache_empty" if not context else "stock_candidates_no_qualified_rows"
        return _source_projection((), status="degraded", reason=reason)

    def _headless_projection(self, key: str) -> dict:
        cached = self._headless_cache.get(key)
        if cached is not None:
            return dict(cached)
        reader = self._headless_source_readers.get(key)
        if not callable(reader):
            projection = _source_projection((), status="degraded", reason="unloaded_no_registry")
        else:
            try:
                raw_projection = reader()
            except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
                raw_projection = None
            if isinstance(raw_projection, Mapping):
                projection = _source_projection(
                    raw_projection.get("codes", ()),
                    status=str(raw_projection.get("status") or "degraded"),
                    reason=str(raw_projection.get("reason") or ""),
                )
            else:
                projection = _source_projection(raw_projection, status="registered")
        self._headless_cache[key] = dict(projection)
        return projection

    def invalidate_headless_cache(self, sources: Iterable[str] | None = None) -> tuple[str, ...]:
        requested = _HEADLESS_CACHE_SOURCE_KEYS if sources is None else {
            str(source or "").strip() for source in sources
        }
        invalidated = []
        for key in sorted(_HEADLESS_CACHE_SOURCE_KEYS & set(requested)):
            if key in self._headless_cache:
                self._headless_cache.pop(key, None)
                invalidated.append(key)
        if sources is None or "stock_candidates" in requested:
            self._headless_context_primed = False
        return tuple(invalidated)

    def _excluded_by_policy(self) -> dict[str, str]:
        excluded: dict[str, str] = {}
        realtime_keys = set(self._realtime_tab_keys())
        for spec in self._tab_specs():
            key = str(spec.get("key") or "").strip()
            if not key or key in realtime_keys:
                continue
            post_f5_policy = str(spec.get("post_f5_policy") or "").strip()
            if key == "asian_market":
                excluded[key] = "separate_asian_runtime"
            elif post_f5_policy == TabPostF5Policy.DATA_REFRESH.value:
                excluded[key] = "f5_information_or_history_cache"
            else:
                excluded[key] = "not_a_share_realtime_policy"
        return excluded

    @staticmethod
    def _coverage_log_signature(coverage: Mapping) -> tuple:
        by_source = coverage.get("by_source", {})
        source_signature = tuple(
            (
                key,
                entry.get("origin", ""),
                entry.get("status", ""),
                entry.get("reason", ""),
                entry.get("added_unique", 0),
                entry.get("cross_source_duplicate_count", 0),
            )
            for key, entry in sorted(by_source.items())
            if isinstance(entry, Mapping)
        )
        return (
            coverage.get("total_unique", 0),
            coverage.get("duplicate_dropped", 0),
            source_signature,
            tuple(coverage.get("degraded_reasons", ())),
        )

    def _log_coverage_if_changed(self, coverage: Mapping) -> None:
        signature = self._coverage_log_signature(coverage)
        if signature == self._last_coverage_signature:
            return
        self._last_coverage_signature = signature
        by_source = coverage.get("by_source", {})
        source_text = ", ".join(
            f"{key}={entry.get('added_unique', 0)}({entry.get('origin', '-')})"
            for key, entry in sorted(by_source.items())
            if isinstance(entry, Mapping)
        ) or "-"
        degraded_text = ", ".join(coverage.get("degraded_reasons", ())) or "无"
        excluded_text = ", ".join(
            f"{key}:{reason}"
            for key, reason in sorted((coverage.get("excluded_by_policy") or {}).items())
        ) or "无"
        log.info(
            "[行情覆盖] 统一轮询 total_unique=%s duplicate_dropped=%s sources=%s degraded=%s excluded=%s",
            coverage.get("total_unique", 0),
            coverage.get("duplicate_dropped", 0),
            source_text,
            degraded_text,
            excluded_text,
        )

    def collect_realtime_quote_coverage(self) -> dict:
        workspace = self._workspace
        get_loaded_tab = getattr(workspace, "get_loaded_tab", None)
        union_codes: set[str] = set()
        by_source: dict[str, dict] = {}
        degraded_reasons: list[str] = []
        total_valid_count = 0

        for key in self._eligible_realtime_tab_keys():
            tab = get_loaded_tab(key) if callable(get_loaded_tab) else None
            if tab is not None:
                projection = self._loaded_projection(tab)
                origin = "loaded_tab"
                loaded_codes, _raw_count, _valid_count, _invalid_count = _code_stats(
                    projection.get("codes", ())
                )
                if not loaded_codes and key in _HEADLESS_CACHE_SOURCE_KEYS:
                    fallback_projection = self._headless_projection(key)
                    fallback = self._headless_fallback_for_pending_loaded_projection(
                        projection,
                        fallback_projection,
                    )
                    if fallback is not None:
                        projection = fallback
                        origin = "loaded_tab_pending_headless_cache"
            elif key in _HEADLESS_CACHE_SOURCE_KEYS:
                projection = self._headless_projection(key)
                origin = "headless_cache"
            else:
                projection = _source_projection((), status="degraded", reason="unloaded_no_registry")
                origin = "unloaded"

            source_codes, raw_count, valid_count, invalid_count = _code_stats(projection.get("codes", ()))
            if raw_count and not valid_count and not projection.get("reason"):
                projection = _source_projection(
                    projection.get("codes", ()),
                    status="degraded",
                    reason="invalid_a_share_codes",
                )
            source_duplicate_dropped = max(0, valid_count - len(source_codes))
            cross_source_duplicate_count = len(source_codes & union_codes)
            added_unique = len(source_codes - union_codes)
            union_codes.update(source_codes)
            total_valid_count += valid_count
            entry = {
                "origin": origin,
                "status": str(projection.get("status") or "degraded"),
                "reason": str(projection.get("reason") or ""),
                "raw_count": raw_count,
                "valid_count": valid_count,
                "unique_count": len(source_codes),
                "invalid_count": invalid_count,
                "source_duplicate_dropped": source_duplicate_dropped,
                "cross_source_duplicate_count": cross_source_duplicate_count,
                "added_unique": added_unique,
            }
            by_source[key] = entry
            if entry["reason"] and (
                entry["status"] != "registered" or entry["origin"] == "unloaded"
            ):
                degraded_reasons.append(f"{key}:{entry['reason']}")

        coverage = {
            "codes": tuple(sorted(union_codes)),
            "total_unique": len(union_codes),
            "duplicate_dropped": max(0, total_valid_count - len(union_codes)),
            "by_source": by_source,
            "degraded_reasons": degraded_reasons,
            "excluded_by_policy": self._excluded_by_policy(),
        }
        self._log_coverage_if_changed(coverage)
        return coverage

    def collect_realtime_quote_codes(self) -> set[str]:
        return set(self.collect_realtime_quote_coverage()["codes"])

    def collect_f5_off_market_quote_codes(self) -> set[str]:
        workspace = self._workspace
        get_loaded_tab = getattr(workspace, "get_loaded_tab", None)
        if not callable(get_loaded_tab):
            return set()

        codes: set[str] = set()
        for key in self._f5_off_market_tab_keys():
            tab = get_loaded_tab(key)
            if not isinstance(tab, F5OffMarketQuoteUniverseCapability):
                continue
            for code in tab.get_f5_off_market_quote_codes() or set():
                normalized = str(code or "").strip()
                if len(normalized) == 6 and normalized.isdigit():
                    codes.add(normalized)
        return codes
