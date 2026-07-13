# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Iterable

from app.services.stock_context_snapshot_service import (
    lhb_pool_cache_signature,
    load_ai_chain_cache_rows,
    load_earnings_state_payload,
    load_fund_holding_snapshot,
    load_lhb_pool_rows,
    load_named_cache_rows,
    load_scan_cache_rows,
    project_root,
)
from app.services.ui_task_lifecycle_service import TaskLifecycleGroup, invoke_with_cancellation
from app.services.ui_task_lifecycle_service import raise_if_cancelled as _checkpoint
from ui.workspaces.stock_signal import StockSignal, coerce_stock_signal
from ui.workspaces.tab_capabilities import ForeignKeywordCapability, StockSignalSourceCapability

KEY_CODE = "\u4ee3\u7801"
KEY_NAME = "\u540d\u79f0"
KEY_CATALYST = "\u50ac\u5316\u5242"
KEY_CATALYST_EMOJI = "\U0001f4e0\u50ac\u5316\u5242"
KEY_SUBSECTOR = "\u7ec6\u5206\u677f\u5757"
KEY_OLD_CHAIN_SEGMENT = "\u7ec6\u5206\u73af\u8282"
KEY_REMARK = "\u5907\u6ce8"
KEY_DETAIL = "\u4ea4\u6613\u8be6\u60c5"
KEY_BUY_BRANCH = "\u4e70\u65b9\u8425\u4e1a\u90e8"
KEY_SELL_BRANCH = "\u5356\u65b9\u8425\u4e1a\u90e8"
KEY_AMOUNT_WAN = "\u6210\u4ea4\u91d1\u989d(\u4e07\u5143)"
KEY_QOQ_PCT = "\u73af\u6bd4%"
KEY_REPORT_PERIOD = "\u62a5\u544a\u671f"
KEY_REPORT_TYPE = "\u7c7b\u578b"
KEY_REPORT_NAME = "\u8d22\u62a5\u540d\u79f0"
KEY_REPORT_TITLE = "\u62a5\u544a\u540d\u79f0"
KEY_REVEAL_DATE = "\u63ed\u6653\u65e5"
KEY_DISCOVERED_AT = "\u53d1\u73b0\u65f6\u95f4"
KEY_EARNINGS_MARK_DATE = "\u4e1a\u7ee9\u65e5"
KEY_EARNINGS_TEXT = "\u4e1a\u7ee9\u5f02\u52a8"
KEY_LAST_LISTED_RAW = "_\u6700\u8fd1\u4e0a\u699c_raw"
KEY_LAST_LISTED = "\u6700\u8fd1\u4e0a\u699c"
KEY_NET_WAN = "\u4e0a\u699c\u51c0\u4e70\u989d(\u4e07)"
KEY_INST_WAN = "\u673a\u6784\u51c0\u4e70(\u4e07)"
KEY_FOREIGN_WAN = "\u5916\u8d44\u51c0\u4e70(\u4e07)"
KEY_TRIGGER_DATE = "\u89e6\u53d1\u65e5\u671f"
KEY_SCORE = "\u8bc4\u5206"
KEY_RPS_STRENGTH = "RPS\u5f3a\u5ea6"
KEY_BREAK_DISTANCE = "\u8ddd\u7a81\u7834"
KEY_BREAK_STATUS = "\u7a81\u7834\u72b6\u6001"
KEY_HOT_SECTOR = "\u70ed\u95e8\u677f\u5757"
KEY_SUBJECT = "\u4e3b\u4f53"
KEY_SUBJECT_CODE = "\u4e3b\u4f53\u4ee3\u7801"
KEY_CAPITAL_ATTRIBUTE = "\u8d44\u91d1\u5c5e\u6027"
KEY_QUARTER = "\u5b63\u5ea6"
KEY_CHANGE_TYPE = "\u53d8\u5316\u7c7b\u578b"
KEY_CURRENT_RATIO = "\u672c\u671f\u5360\u6bd4"
KEY_HOLDING_DELTA = "\u6301\u80a1\u53d8\u5316"

RAW_STOCK_CODE = "\u80a1\u7968\u4ee3\u7801"
RAW_STOCK_NAME = "\u80a1\u7968\u540d\u79f0"
RAW_STOCK_SHORT_NAME = "\u80a1\u7968\u7b80\u79f0"
RAW_QOQ_PCT = "\u73af\u6bd4\u589e\u901f_\u767e\u5206\u6bd4"
RAW_DISCLOSURE_DATE = "\u516c\u544a\u65e5\u671f"
RAW_DATA_TYPE = "\u6570\u636e\u7c7b\u578b"
RAW_DISCOVERED_AT = KEY_DISCOVERED_AT
POST_F5_CONTEXT_SNAPSHOT_DEFER_SECONDS = 5.0
FUND_SNAPSHOT_TIMEOUT_SECONDS = 90.0
LHB_SNAPSHOT_TIMEOUT_SECONDS = 180.0


def _cancellable_items(values, cancellation_token=None):
    for value in values:
        _checkpoint(cancellation_token)
        yield value


def _start_fund_snapshot(service, force: bool, include_fund: bool) -> tuple[bool, bool]:
    with service._fund_rows_lock:
        already_running = service._fund_rows_loading
        start = include_fund and (force or (not service._fund_rows_loading and not service._fund_rows_loaded))
        if start:
            service._fund_rows_loading = True
    return start, already_running


def _schedule_fund_snapshot(service, domain_events, task_registry) -> None:
    def _on_success(rows):
        if service._shutdown:
            return
        with service._fund_rows_lock:
            service._fund_rows_snapshot = [dict(row) for row in (rows or [])]
            service._fund_rows_loaded = True
            service._fund_rows_loading = False
        domain_events.sig_stock_context_snapshot_updated.emit()

    def _on_error(_message: str):
        with service._fund_rows_lock:
            service._fund_rows_loading = False

    service._task_lifecycle.run_background(
        "fund-snapshot",
        lambda token: invoke_with_cancellation(service._load_fund_holding_rows_snapshot, token),
        on_success=_on_success,
        on_error=_on_error,
        task_id=task_registry.workspace("stock_context_fund_rows_snapshot"),
        timeout_sec=FUND_SNAPSHOT_TIMEOUT_SECONDS,
    )


def _schedule_lhb_snapshot(service, domain_events, task_registry, signature) -> None:
    def _on_success(rows):
        if service._shutdown:
            return
        with service._lhb_rows_lock:
            service._lhb_rows_snapshot = [dict(row) for row in (rows or [])]
            service._lhb_rows_signature = signature
            service._lhb_rows_loading = False
        domain_events.sig_stock_context_snapshot_updated.emit()

    def _on_error(_message: str):
        with service._lhb_rows_lock:
            service._lhb_rows_loading = False

    service._task_lifecycle.run_background(
        "lhb-snapshot",
        lambda token: invoke_with_cancellation(service._load_lhb_pool_rows, token),
        on_success=_on_success,
        on_error=_on_error,
        task_id=task_registry.workspace("stock_context_lhb_rows_snapshot"),
        timeout_sec=LHB_SNAPSHOT_TIMEOUT_SECONDS,
    )

TEXT_BUY = "\u4e70\u5165"
TEXT_SELL = "\u5356\u51fa"
TEXT_INST_ONLY = "\u673a\u6784\u4e13\u7528"
TEXT_BLOCK_TRADE_MATCH = "\u5927\u5b97\u5bf9\u5012"
TEXT_INST_NET_BUY = "\u673a\u6784\u51c0\u4e70"
TEXT_INST_NET_SELL = "\u673a\u6784\u51c0\u5356"
TEXT_FOREIGN_NET_BUY = "\u5916\u8d44\u51c0\u4e70"
TEXT_FOREIGN_NET_SELL = "\u5916\u8d44\u51c0\u5356"
TEXT_NET_BUY = "\u51c0\u4e70"
TEXT_NET_SELL = "\u51c0\u5356"

SIGNAL_CATALYST = "catalyst"
SIGNAL_SUBSECTOR = "subsector"
SIGNAL_BLOCK_TRADE = "block_trade"
SIGNAL_EARNINGS = "earnings"
SIGNAL_LHB = "lhb"
SIGNAL_VCP_SCAN = "vcp_scan"
SIGNAL_FUND_HOLDING = "fund_holding"
FUND_HOLDING_ALLOWED_CHANGE_TYPES = frozenset({"\u65b0\u8fdb", "\u589e\u6301"})


class StockContextService:
    """Builds a stock-code keyed signal index across workspace tabs."""

    def __init__(self, workspace):
        self._workspace = workspace
        self._fund_rows_lock = threading.RLock()
        self._fund_rows_snapshot: list[dict] = []
        self._fund_rows_loaded = False
        self._fund_rows_loading = False
        self._lhb_rows_lock = threading.RLock()
        self._lhb_rows_snapshot: list[dict] = []
        self._lhb_rows_signature: tuple[str, int, int] | None = None
        self._lhb_rows_loading = False
        self._post_f5_snapshot_defer_until = 0.0
        self._task_lifecycle = TaskLifecycleGroup()
        self._shutdown = False

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            text = str(value or "").replace(",", "").strip()
            if not text:
                return float(default)
            return float(text)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _compact_block_trade_branch(branch: str, foreign_keywords: list[str]) -> str:
        text = str(branch or "").strip()
        if not text:
            return ""
        for keyword in foreign_keywords:
            if keyword in text:
                return keyword
        if TEXT_INST_ONLY in text:
            return TEXT_INST_ONLY
        return ""

    @classmethod
    def _build_watchlist_block_trade_signal(
        cls,
        detail: str,
        buy: str,
        sell: str,
        amount: float,
        foreign_keywords: list[str],
    ) -> tuple[str, float]:
        if amount < 0.01:
            return "", 0.0

        buy_label = cls._compact_block_trade_branch(buy, foreign_keywords)
        sell_label = cls._compact_block_trade_branch(sell, foreign_keywords)
        detail_text = str(detail or "")

        if TEXT_BUY in detail_text:
            if buy_label:
                return f"{buy_label}{TEXT_BUY}{amount:.0f}\u4e07", amount
            if sell_label:
                return f"{sell_label}{TEXT_SELL}{amount:.0f}\u4e07", amount

        if TEXT_SELL in detail_text:
            if sell_label:
                return f"{sell_label}{TEXT_SELL}{amount:.0f}\u4e07", amount
            if buy_label:
                return f"{buy_label}{TEXT_BUY}{amount:.0f}\u4e07", amount

        if buy and sell and buy == sell:
            return f"{TEXT_BLOCK_TRADE_MATCH} {amount:.0f}\u4e07", amount

        return "", 0.0

    def _get_tab(self, key: str):
        get_loaded_tab = getattr(self._workspace, "get_loaded_tab", None)
        if callable(get_loaded_tab):
            return get_loaded_tab(key)
        get_tab = getattr(self._workspace, "get_tab", None)
        if not callable(get_tab):
            return None
        return get_tab(key)

    def _tab_specs(self) -> list[dict]:
        tab_specs = getattr(self._workspace, "tab_specs", None)
        return list(tab_specs() or []) if callable(tab_specs) else []

    def _has_tab_key(self, key: str, loaded_tab=None) -> bool:
        if loaded_tab is not None:
            return True
        key_text = str(key or "").strip()
        if not key_text:
            return False
        return any(str(spec.get("key") or "").strip() == key_text for spec in self._tab_specs())

    def _iter_tabs_with_keys(self) -> Iterable[tuple[str, object]]:
        seen_ids: set[int] = set()
        for spec in self._tab_specs():
            key = str(spec.get("key", "")).strip()
            tab = self._get_tab(key)
            if not key or tab is None or id(tab) in seen_ids:
                continue
            seen_ids.add(id(tab))
            yield key, tab

        iter_tabs = getattr(self._workspace, "iter_tabs", None)
        if not callable(iter_tabs):
            return
        for tab in list(iter_tabs() or []):
            if tab is None or id(tab) in seen_ids:
                continue
            seen_ids.add(id(tab))
            yield "", tab

    @staticmethod
    def _get_rows(tab) -> list[dict]:
        radar_reader = getattr(tab, "get_watchlist_radar_rows", None)
        if callable(radar_reader):
            return list(radar_reader() or [])
        get_row_data = getattr(tab, "get_row_data", None)
        if not callable(get_row_data):
            return []
        return list(get_row_data() or [])

    @staticmethod
    def _project_root():
        return project_root()

    @staticmethod
    def _coerce_cache_rows(value) -> list[dict]:
        if not isinstance(value, (list, tuple)):
            return []
        return [dict(row) for row in value if isinstance(row, dict)]

    @staticmethod
    def _normalize_target_codes(target_codes) -> set[str]:
        if target_codes is None:
            return set()
        return {str(code or "").strip() for code in target_codes if str(code or "").strip()}

    def _load_foreign_block_cache_rows(self) -> list[dict]:
        return load_named_cache_rows("foreign_block_trade_latest.json", root=self._project_root())

    def _load_na_daily_cache_rows(self) -> list[dict]:
        return load_named_cache_rows("na_daily_latest.json", root=self._project_root())

    def _load_ai_chain_cache_rows(self) -> list[dict]:
        return self._coerce_cache_rows(load_ai_chain_cache_rows())

    @staticmethod
    def _normalize_earnings_code(row: dict) -> str:
        code = str(row.get(KEY_CODE) or row.get(RAW_STOCK_CODE) or "").strip()
        if code.isdigit() and len(code) <= 6:
            code = code.zfill(6)
        return code

    def _load_earnings_state_payload(self) -> tuple[dict, str]:
        return load_earnings_state_payload()

    @staticmethod
    def _earnings_report_type(row: dict) -> str:
        return str(row.get(RAW_DATA_TYPE) or row.get(KEY_REPORT_TYPE) or row.get(KEY_REPORT_TITLE) or "").strip()

    @staticmethod
    def _earnings_discovered_at(row: dict, fallback: str = "") -> str:
        return str(row.get(KEY_DISCOVERED_AT) or row.get("discovered_at") or fallback or "").strip()

    @staticmethod
    def _earnings_reveal_date(row: dict) -> str:
        return str(
            row.get(KEY_REVEAL_DATE)
            or row.get(KEY_EARNINGS_MARK_DATE)
            or row.get(RAW_DISCLOSURE_DATE)
            or row.get(KEY_TRIGGER_DATE)
            or row.get("\u6e90\u516c\u544a\u65e5\u671f")
            or ""
        ).strip()

    @classmethod
    def _earnings_capture_time(cls, row: dict, fallback: str = "") -> str:
        return str(
            row.get(KEY_DISCOVERED_AT)
            or row.get("discovered_at")
            or fallback
            or ""
        ).strip()

    def _earnings_discovery_lookup(self) -> dict[tuple[str, str, str], str]:
        payload, state_updated_at = self._load_earnings_state_payload()
        records = self._coerce_cache_rows(payload.get("records", [])) if payload else []
        lookup: dict[tuple[str, str, str], str] = {}
        for row in records:
            code = self._normalize_earnings_code(row)
            if not code:
                continue
            discovered_at = self._earnings_discovered_at(row, state_updated_at)
            if not discovered_at:
                continue
            report_period = str(row.get(KEY_REPORT_PERIOD, "") or "").strip()
            report_type = self._earnings_report_type(row)
            lookup[(code, report_period, report_type)] = discovered_at
            if report_period:
                lookup[(code, report_period, "")] = discovered_at
            lookup[(code, "", "")] = discovered_at
        return lookup

    @staticmethod
    def _earnings_lookup_discovery(
        lookup: dict[tuple[str, str, str], str],
        *,
        code: str,
        report_period: str,
        report_type: str,
    ) -> str:
        return (
            lookup.get((code, report_period, report_type))
            or lookup.get((code, report_period, ""))
            or lookup.get((code, "", ""))
            or ""
        )

    def _load_earnings_cache_rows(self) -> list[dict]:
        payload, state_updated_at = self._load_earnings_state_payload()
        if not payload:
            return []

        rows: list[dict] = []
        for raw_row in self._coerce_cache_rows(payload.get("records", [])):
            if raw_row.get(KEY_CODE):
                row = dict(raw_row)
                capture_time = self._earnings_capture_time(row, state_updated_at)
                reveal_date = self._earnings_reveal_date(row)
                if capture_time:
                    row[KEY_DISCOVERED_AT] = capture_time
                if reveal_date:
                    row[KEY_REVEAL_DATE] = reveal_date
                rows.append(row)
                continue

            code = self._normalize_earnings_code(raw_row)
            if not code:
                continue

            report_type = str(raw_row.get(RAW_DATA_TYPE, raw_row.get(KEY_REPORT_TITLE, "")) or "").strip()
            capture_time = self._earnings_capture_time(raw_row, state_updated_at)
            reveal_date = self._earnings_reveal_date(raw_row)
            rows.append(
                {
                    **raw_row,
                    KEY_CODE: code,
                    KEY_NAME: str(
                        raw_row.get(RAW_STOCK_NAME) or raw_row.get(RAW_STOCK_SHORT_NAME) or raw_row.get(KEY_NAME) or ""
                    ).strip(),
                    KEY_QOQ_PCT: raw_row.get(RAW_QOQ_PCT, raw_row.get(KEY_QOQ_PCT, "")),
                    KEY_REPORT_PERIOD: str(raw_row.get(KEY_REPORT_PERIOD, "") or "").strip(),
                    KEY_REPORT_TYPE: report_type,
                    KEY_REPORT_TITLE: report_type,
                    KEY_REVEAL_DATE: reveal_date,
                    KEY_DISCOVERED_AT: capture_time,
                    KEY_TRIGGER_DATE: str(raw_row.get(RAW_DISCLOSURE_DATE, "") or "").strip(),
                }
            )
        return rows

    def _lhb_pool_cache_signature(self) -> tuple[str, int, int] | None:
        return lhb_pool_cache_signature(root=self._project_root())

    def _load_lhb_pool_rows(self, *, cancellation_token=None) -> list[dict]:
        _checkpoint(cancellation_token)
        signature = self._lhb_pool_cache_signature()
        if signature is not None:
            with self._lhb_rows_lock:
                if signature == self._lhb_rows_signature:
                    return [dict(row) for row in self._lhb_rows_snapshot]

        pool = invoke_with_cancellation(
            load_lhb_pool_rows,
            cancellation_token,
            engine=getattr(self._workspace, "engine", None),
        )

        rows: list[dict] = []
        for row in _cancellable_items(self._coerce_cache_rows(pool), cancellation_token):
            raw_date = str(row.get(KEY_LAST_LISTED, "") or "").strip()
            if len(raw_date) == 8:
                row[KEY_LAST_LISTED_RAW] = raw_date
                row[KEY_LAST_LISTED] = f"{raw_date[4:6]}-{raw_date[6:8]}"
            rows.append(row)
        if signature is not None:
            _checkpoint(cancellation_token)
            with self._lhb_rows_lock:
                self._lhb_rows_snapshot = [dict(row) for row in rows]
                self._lhb_rows_signature = signature
        return rows

    def _cached_lhb_pool_rows(self, *, allow_async_refresh: bool = True) -> list[dict]:
        signature = self._lhb_pool_cache_signature()
        with self._lhb_rows_lock:
            if signature is not None and signature == self._lhb_rows_signature:
                return [dict(row) for row in self._lhb_rows_snapshot]
            loading = self._lhb_rows_loading
        if allow_async_refresh and not loading:
            self.refresh_async_snapshots()
        return []

    def prepare_post_f5_refresh(self) -> None:
        self._post_f5_snapshot_defer_until = max(
            float(getattr(self, "_post_f5_snapshot_defer_until", 0.0) or 0.0),
            time.monotonic() + POST_F5_CONTEXT_SNAPSHOT_DEFER_SECONDS,
        )

    def _should_defer_async_snapshots(self) -> bool:
        try:
            return time.monotonic() < float(getattr(self, "_post_f5_snapshot_defer_until", 0.0) or 0.0)
        except (TypeError, ValueError):
            return False

    def refresh_watchlist_names(self, code2name: dict[str, str]) -> bool:
        watchlist_tab = self._get_tab("watchlist")
        refresh_names = getattr(watchlist_tab, "refresh_watchlist_names", None)
        if not callable(refresh_names):
            return False
        return bool(refresh_names(code2name))

    def prime_watchlist_state(self) -> None:
        watchlist_tab = self._get_tab("watchlist")
        prime_state = getattr(watchlist_tab, "prime_startup_state", None)
        if callable(prime_state):
            prime_state()

    def _direct_signal_tab_keys(self) -> set[str]:
        keys: set[str] = set()
        for key, tab in self._iter_tabs_with_keys():
            if key and isinstance(tab, StockSignalSourceCapability):
                keys.add(key)
        return keys

    def _iter_direct_stock_signals(self) -> list[StockSignal]:
        signals: list[StockSignal] = []
        for key, tab in self._iter_tabs_with_keys():
            if not isinstance(tab, StockSignalSourceCapability):
                continue
            for raw_signal in list(tab.iter_stock_signals() or []):
                signal = coerce_stock_signal(raw_signal)
                if signal is None:
                    continue
                if not signal.source_tab and key:
                    signal = StockSignal(
                        code=signal.code,
                        name=signal.name,
                        source_tab=key,
                        source_label=signal.source_label,
                        signal_type=signal.signal_type,
                        summary=signal.summary,
                        numeric_value=signal.numeric_value,
                        observed_at=signal.observed_at,
                        refreshed_at=signal.refreshed_at,
                        freshness=signal.freshness,
                        row_ref=signal.row_ref,
                        payload=signal.payload,
                    )
                signals.append(signal)
        return signals

    def _iter_na_daily_signals(self, *, include_cache_fallback: bool = True) -> list[StockSignal]:
        signals: list[StockSignal] = []
        rows = self._get_rows(self._get_tab("na_daily"))
        if not rows and include_cache_fallback:
            rows = self._load_na_daily_cache_rows()
        for row_idx, row in enumerate(rows):
            code = str(row.get(KEY_CODE, "")).strip()
            if not code:
                continue
            name = str(row.get(KEY_NAME, "") or "")
            catalyst = str(row.get(KEY_CATALYST, "") or row.get(KEY_CATALYST_EMOJI, "") or "").strip()
            if catalyst:
                signals.append(
                    StockSignal(
                        code=code,
                        name=name,
                        source_tab="na_daily",
                        source_label="na_daily",
                        signal_type=SIGNAL_CATALYST,
                        summary=catalyst,
                        row_ref=row_idx,
                        payload=dict(row),
                    )
                )

            subsector = str(row.get(KEY_SUBSECTOR, "") or "").strip()
            if subsector:
                signals.append(
                    StockSignal(
                        code=code,
                        name=name,
                        source_tab="na_daily",
                        source_label="na_daily",
                        signal_type=SIGNAL_SUBSECTOR,
                        summary=subsector,
                        row_ref=row_idx,
                        payload=dict(row),
                    )
                )
        return signals

    def _iter_ai_chain_signals(self, *, include_cache_fallback: bool = True) -> list[StockSignal]:
        signals: list[StockSignal] = []
        rows_by_code: dict[str, dict] = {}
        row_refs_by_code: dict[str, int] = {}
        segments_by_code: dict[str, list[str]] = {}
        remarks_by_code: dict[str, list[str]] = {}
        ai_chain_tab = self._get_tab("ai_industry_chain")
        if not self._has_tab_key("ai_industry_chain", ai_chain_tab):
            return []
        rows = self._get_rows(ai_chain_tab)
        if not rows and include_cache_fallback:
            rows = self._load_ai_chain_cache_rows()
        for row_idx, row in enumerate(rows):
            code = str(row.get(KEY_CODE, "")).strip()
            if not code:
                continue
            segment = str(row.get(KEY_SUBSECTOR, "") or row.get(KEY_OLD_CHAIN_SEGMENT, "") or "").strip()
            remark = str(row.get(KEY_REMARK, "") or "").strip()
            if not segment and not remark:
                continue

            rows_by_code.setdefault(code, dict(row))
            row_refs_by_code.setdefault(code, row_idx)
            if segment:
                segments = segments_by_code.setdefault(code, [])
                if segment not in segments:
                    segments.append(segment)
            if remark:
                remarks = remarks_by_code.setdefault(code, [])
                if remark not in remarks:
                    remarks.append(remark)

        for code in rows_by_code:
            segments = segments_by_code.get(code, [])
            remarks = remarks_by_code.get(code, [])
            row = rows_by_code.get(code, {})
            merged_segment = " / ".join(segments)
            merged_remark = " / ".join(remarks)
            payload = dict(row)
            payload[KEY_SUBSECTOR] = merged_segment
            payload[KEY_REMARK] = merged_remark
            signals.append(
                StockSignal(
                    code=code,
                    name=str(row.get(KEY_NAME, "") or ""),
                    source_tab="ai_industry_chain",
                    source_label="ai_industry_chain",
                    signal_type=SIGNAL_SUBSECTOR,
                    summary=merged_segment,
                    row_ref=row_refs_by_code.get(code),
                    payload=payload,
                )
            )
        return signals

    def _iter_scan_signals(self, *, include_cache_fallback: bool = True) -> list[StockSignal]:
        scan_tab = self._get_tab("scan")
        if not self._has_tab_key("scan", scan_tab):
            return []
        scan_reader = getattr(scan_tab, "get_scan_results", None)
        rows = list(scan_reader() or []) if callable(scan_reader) else []
        if not rows:
            rows = self._get_rows(scan_tab)
        if not rows and include_cache_fallback:
            rows = load_scan_cache_rows(root=self._project_root())

        signals: list[StockSignal] = []
        for row_idx, row in enumerate(rows):
            code = str(row.get(KEY_CODE, "")).strip()
            if not code:
                continue

            trigger_date = str(row.get(KEY_TRIGGER_DATE, "") or "").strip()
            score_text = str(row.get(KEY_SCORE, "") or "").strip()
            rps_text = str(row.get(KEY_RPS_STRENGTH, "") or "").strip()
            distance_text = str(row.get(KEY_BREAK_DISTANCE, "") or "").strip()
            status_text = str(row.get(KEY_BREAK_STATUS, "") or "").strip()
            sector_text = str(row.get(KEY_HOT_SECTOR, "") or "").strip()

            summary_parts = []
            if trigger_date:
                summary_parts.append(f"触发{trigger_date}")
            if score_text:
                summary_parts.append(f"评分{score_text}")
            if rps_text:
                summary_parts.append(f"RPS{rps_text}")
            if distance_text:
                summary_parts.append(f"距突破{distance_text}")
            if status_text:
                summary_parts.append(status_text)
            if sector_text:
                summary_parts.append(sector_text)

            signals.append(
                StockSignal(
                    code=code,
                    name=str(row.get(KEY_NAME, "") or ""),
                    source_tab="scan",
                    source_label="scan",
                    signal_type=SIGNAL_VCP_SCAN,
                    summary=" | ".join(summary_parts) or "VCP扫描命中",
                    numeric_value=self._safe_float(score_text, default=0.0) if score_text else None,
                    observed_at=trigger_date,
                    row_ref=row_idx,
                    payload=dict(row),
                )
            )
        return signals

    def _iter_block_trade_signals(self, *, include_cache_fallback: bool = True) -> list[StockSignal]:
        foreign_block_tab = self._get_tab("foreign_block")
        if not self._has_tab_key("foreign_block", foreign_block_tab):
            return []
        foreign_keywords = (
            foreign_block_tab.get_foreign_keywords() if isinstance(foreign_block_tab, ForeignKeywordCapability) else []
        )

        block_aggregates: dict[str, dict] = {}
        rows = self._get_rows(foreign_block_tab)
        if not rows and include_cache_fallback:
            rows = self._load_foreign_block_cache_rows()
        for row_idx, row in enumerate(rows):
            code = str(row.get(KEY_CODE, "")).strip()
            if not code:
                continue

            detail = str(row.get(KEY_DETAIL, "") or "")
            buy = str(row.get(KEY_BUY_BRANCH, "") or "")
            sell = str(row.get(KEY_SELL_BRANCH, "") or "")
            amount = self._safe_float(row.get(KEY_AMOUNT_WAN, 0))

            signal_text, signal_amount = self._build_watchlist_block_trade_signal(
                detail,
                buy,
                sell,
                amount,
                foreign_keywords,
            )
            if not signal_text:
                continue

            bucket = block_aggregates.setdefault(
                code,
                {
                    "best_text": "",
                    "best_amount": 0.0,
                    "name": str(row.get(KEY_NAME, "") or ""),
                    "row_ref": row_idx,
                    "row": dict(row),
                },
            )
            best_amount = float(bucket.get("best_amount", 0.0) or 0.0)
            if signal_amount >= best_amount:
                bucket["best_text"] = signal_text
                bucket["best_amount"] = signal_amount
                bucket["name"] = str(row.get(KEY_NAME, "") or "")
                bucket["row_ref"] = row_idx
                bucket["row"] = dict(row)

        signals = []
        for code, stats in block_aggregates.items():
            best_text = str(stats.get("best_text", "") or "")
            best_amount = float(stats.get("best_amount", 0.0) or 0.0)
            if not best_text:
                continue
            signals.append(
                StockSignal(
                    code=code,
                    name=str(stats.get("name", "") or ""),
                    source_tab="foreign_block",
                    source_label="foreign_block",
                    signal_type=SIGNAL_BLOCK_TRADE,
                    summary=best_text,
                    numeric_value=best_amount,
                    row_ref=stats.get("row_ref"),
                    payload={
                        **dict(stats.get("row") or {}),
                        "amount_wan": best_amount,
                    },
                )
            )
        return signals

    def _iter_earnings_signals(self, *, include_cache_fallback: bool = True) -> list[StockSignal]:
        signals: list[StockSignal] = []
        earnings_tab = self._get_tab("earnings")
        if not self._has_tab_key("earnings", earnings_tab):
            return []
        rows = self._get_rows(earnings_tab)
        if not rows and include_cache_fallback:
            rows = self._load_earnings_cache_rows()
        discovery_lookup = self._earnings_discovery_lookup() if rows else {}
        for row_idx, row in enumerate(rows):
            code = str(row.get(KEY_CODE, "")).strip()
            if not code:
                continue

            qoq_raw = row.get(KEY_QOQ_PCT)
            qoq_text = str(qoq_raw).strip()
            if not qoq_text:
                continue

            qoq_value = self._safe_float(qoq_raw, default=0.0)
            qoq_display = f"{qoq_value:.2f}".rstrip("0").rstrip(".")
            report_label = self._earnings_report_label(row)
            summary = f"{report_label} {qoq_display}%" if report_label else f"{qoq_display}%"
            report_period = str(row.get(KEY_REPORT_PERIOD, "") or "").strip()
            report_type = self._earnings_report_type(row)
            discovered_at = self._earnings_discovered_at(row) or self._earnings_lookup_discovery(
                discovery_lookup,
                code=code,
                report_period=report_period,
                report_type=report_type,
            )
            reveal_date = self._earnings_reveal_date(row) or discovered_at
            payload = {
                **dict(row),
                "qoq_pct": qoq_value,
                KEY_EARNINGS_TEXT: summary,
            }
            if report_type:
                payload.setdefault(KEY_REPORT_TYPE, report_type)
            if reveal_date:
                payload.setdefault(KEY_REVEAL_DATE, reveal_date)
                payload[KEY_EARNINGS_MARK_DATE] = reveal_date
            if discovered_at:
                payload[KEY_DISCOVERED_AT] = discovered_at
            signals.append(
                StockSignal(
                    code=code,
                    name=str(row.get(KEY_NAME, "") or ""),
                    source_tab="earnings",
                    source_label="earnings",
                    signal_type=SIGNAL_EARNINGS,
                    summary=summary,
                    numeric_value=qoq_value,
                    observed_at=discovered_at or reveal_date,
                    row_ref=row_idx,
                    payload=payload,
                )
            )
        return signals

    @staticmethod
    def _earnings_report_label(row: dict) -> str:
        for key in (KEY_REPORT_NAME, KEY_REPORT_TITLE):
            label = str(row.get(key, "") or "").strip()
            if label:
                return label

        period = str(row.get(KEY_REPORT_PERIOD, "") or "").strip()
        if not period:
            return ""

        compact = period.replace("/", "-").replace(".", "-")
        if "Q1" in compact.upper() or compact.endswith("-03-31") or compact.endswith("0331"):
            return "\u4e00\u5b63\u5ea6"
        if "Q2" in compact.upper() or compact.endswith("-06-30") or compact.endswith("0630"):
            return "\u534a\u5e74\u62a5"
        if "Q3" in compact.upper() or compact.endswith("-09-30") or compact.endswith("0930"):
            return "\u4e09\u5b63\u5ea6"
        if "Q4" in compact.upper() or compact.endswith("-12-31") or compact.endswith("1231"):
            return "\u5e74\u62a5"
        return period

    def _iter_fund_holdings_signals(
        self,
        *,
        allow_async_snapshot_refresh: bool = True,
        target_codes=None,
    ) -> list[StockSignal]:
        signals: list[StockSignal] = []
        rows = self._fund_holding_rows(
            allow_async_snapshot_refresh=allow_async_snapshot_refresh,
            target_codes=target_codes,
        )
        latest_by_subject = self._latest_fund_holding_quarters(rows)
        for row_idx, row in enumerate(rows):
            code = str(row.get(KEY_CODE, "")).strip()
            if not code:
                continue

            subject = str(row.get(KEY_SUBJECT, "") or "").strip()
            capital_attribute = str(row.get(KEY_CAPITAL_ATTRIBUTE, "") or "").strip()
            quarter = str(row.get(KEY_QUARTER, "") or "").strip()
            change_type = str(row.get(KEY_CHANGE_TYPE, "") or "").strip()
            if change_type not in FUND_HOLDING_ALLOWED_CHANGE_TYPES:
                continue
            if not self._is_latest_fund_holding_row(row, latest_by_subject):
                continue
            current_ratio = str(row.get(KEY_CURRENT_RATIO, "") or "").strip()
            holding_delta = str(row.get(KEY_HOLDING_DELTA, "") or "").strip()

            summary_parts = []
            if subject:
                summary_parts.append(subject)
            if capital_attribute:
                summary_parts.append(capital_attribute)
            if change_type:
                summary_parts.append(change_type)
            if quarter:
                summary_parts.append(quarter)
            if current_ratio:
                summary_parts.append(f"占比{current_ratio}")
            if holding_delta:
                summary_parts.append(f"变化{holding_delta}")

            signals.append(
                StockSignal(
                    code=code,
                    name=str(row.get(KEY_NAME, "") or ""),
                    source_tab="fund_holdings",
                    source_label="fund_holdings",
                    signal_type=SIGNAL_FUND_HOLDING,
                    summary=" | ".join(summary_parts) or "基金持仓变动",
                    observed_at=quarter,
                    row_ref=row_idx,
                    payload=dict(row),
                )
            )
        return signals

    @staticmethod
    def _format_fund_holding_pct(value) -> str:
        try:
            return f"{float(value or 0):.2f}%"
        except (TypeError, ValueError):
            return "--"

    @staticmethod
    def _format_fund_holding_amount(value, *, divisor: float = 10000.0) -> str:
        try:
            number = float(value or 0) / divisor
        except (TypeError, ValueError):
            return "--"
        prefix = "+" if number > 0 else ""
        return f"{prefix}{number:,.2f}"

    @classmethod
    def _format_fund_holding_store_rows(
        cls,
        latest_quarter_map: dict,
        change_rows: list[dict],
        *,
        cancellation_token=None,
    ) -> list[dict]:
        try:
            from app.services.ui_fund_holdings_service import (
                QFII_CAPITAL_ATTRIBUTE_UNMARKED,
                SUBJECT_QFII,
            )
        except (ImportError, RuntimeError):
            return []

        view_rows: list[dict] = []
        qfii_subject_code = str((SUBJECT_QFII or {}).get("subject_code") or "")
        for row in _cancellable_items(change_rows, cancellation_token):
            stock_code = str(row.get("stock_code") or "").strip()
            subject_code = str(row.get("subject_code") or "").strip()
            quarter_key = str(row.get("quarter_key") or "").strip()
            change_type = str(row.get("change_type") or "").strip()
            if not stock_code or change_type not in FUND_HOLDING_ALLOWED_CHANGE_TYPES:
                continue
            if quarter_key != latest_quarter_map.get(subject_code):
                continue

            capital_attribute = str(row.get("capital_attribute") or "").strip()
            if subject_code == qfii_subject_code and not capital_attribute:
                capital_attribute = QFII_CAPITAL_ATTRIBUTE_UNMARKED

            view_rows.append(
                {
                    KEY_CODE: stock_code,
                    KEY_NAME: str(row.get("stock_name") or "").strip(),
                    KEY_SUBJECT: str(row.get("subject_name") or "").strip(),
                    KEY_CAPITAL_ATTRIBUTE: (
                        capital_attribute if capital_attribute != QFII_CAPITAL_ATTRIBUTE_UNMARKED else ""
                    ),
                    KEY_SUBJECT_CODE: subject_code,
                    KEY_QUARTER: quarter_key,
                    KEY_CHANGE_TYPE: change_type,
                    KEY_CURRENT_RATIO: cls._format_fund_holding_pct(row.get("curr_ratio_pct")),
                    KEY_HOLDING_DELTA: cls._format_fund_holding_amount(row.get("delta_hold_num_shares")),
                    "_is_latest_subject_quarter": True,
                }
            )
        return view_rows

    def _load_fund_holding_rows_snapshot(self, *, stock_codes=None, cancellation_token=None) -> list[dict]:
        latest_quarter_map, change_rows = invoke_with_cancellation(
            load_fund_holding_snapshot,
            cancellation_token,
            stock_codes=stock_codes,
        )
        rows = self._format_fund_holding_store_rows(
            latest_quarter_map,
            change_rows,
            cancellation_token=cancellation_token,
        )
        _checkpoint(cancellation_token)
        return rows

    def refresh_async_snapshots(
        self,
        *,
        force: bool = False,
        include_fund: bool = True,
        include_lhb: bool = True,
    ) -> bool:
        if self._shutdown or self._should_defer_async_snapshots():
            return False
        try:
            from app.services.ui_event_service import domain_events
            from app.services.ui_task_service import task_registry
        except (ImportError, RuntimeError):
            return False

        scheduled = False
        start_fund, already_running = _start_fund_snapshot(self, force, include_fund)
        if start_fund:
            scheduled = True
            _schedule_fund_snapshot(self, domain_events, task_registry)

        lhb_signature = self._lhb_pool_cache_signature() if include_lhb else None
        with self._lhb_rows_lock:
            already_running = already_running or self._lhb_rows_loading
            start_lhb = (
                lhb_signature is not None
                and (force or (not self._lhb_rows_loading and lhb_signature != self._lhb_rows_signature))
            )
            if start_lhb:
                self._lhb_rows_loading = True

        if start_lhb:
            scheduled = True
            _schedule_lhb_snapshot(self, domain_events, task_registry, lhb_signature)

        return scheduled or already_running

    def shutdown(self, *, timeout_ms: int = 750) -> bool:
        self._shutdown = True
        completed = self._task_lifecycle.shutdown(timeout_ms=timeout_ms)
        with self._fund_rows_lock:
            self._fund_rows_loading = False
        with self._lhb_rows_lock:
            self._lhb_rows_loading = False
        return completed

    def _cached_fund_holding_rows(self, *, allow_async_refresh: bool = True) -> list[dict]:
        with self._fund_rows_lock:
            if self._fund_rows_loaded:
                return [dict(row) for row in self._fund_rows_snapshot]
        if allow_async_refresh:
            self.refresh_async_snapshots()
        return []

    def _query_fund_holding_store_rows(self) -> list[dict]:
        return self._load_fund_holding_rows_snapshot()

    def _fund_holding_rows(self, *, allow_async_snapshot_refresh: bool = True, target_codes=None) -> list[dict]:
        if not self._has_fund_holdings_tab():
            return []
        target_code_set = self._normalize_target_codes(target_codes)
        if target_codes is not None:
            if not target_code_set:
                return []
            with self._fund_rows_lock:
                if self._fund_rows_loaded:
                    return [
                        dict(row)
                        for row in self._fund_rows_snapshot
                        if str(row.get(KEY_CODE) or "").strip() in target_code_set
                    ]
            return self._load_fund_holding_rows_snapshot(stock_codes=target_code_set)
        rows = self._cached_fund_holding_rows(allow_async_refresh=allow_async_snapshot_refresh)
        if rows:
            return rows
        return self._get_rows(self._get_tab("fund_holdings"))

    def _has_fund_holdings_tab(self) -> bool:
        if self._get_tab("fund_holdings") is not None:
            return True
        return any(str(spec.get("key") or "").strip() == "fund_holdings" for spec in self._tab_specs())

    @staticmethod
    def _latest_fund_holding_quarters(rows: list[dict]) -> dict[str, str]:
        latest_by_subject: dict[str, str] = {}
        for row in rows:
            quarter = str(row.get(KEY_QUARTER, "") or "").strip()
            if not quarter:
                continue
            subject_key = str(row.get(KEY_SUBJECT_CODE, "") or row.get(KEY_SUBJECT, "") or "__all__").strip()
            if quarter > latest_by_subject.get(subject_key, ""):
                latest_by_subject[subject_key] = quarter
        return latest_by_subject

    @staticmethod
    def _is_latest_fund_holding_row(row: dict, latest_by_subject: dict[str, str]) -> bool:
        if "_is_latest_subject_quarter" in row:
            return bool(row.get("_is_latest_subject_quarter"))
        quarter = str(row.get(KEY_QUARTER, "") or "").strip()
        if not quarter:
            return False
        subject_key = str(row.get(KEY_SUBJECT_CODE, "") or row.get(KEY_SUBJECT, "") or "__all__").strip()
        return quarter == latest_by_subject.get(subject_key)

    def _iter_lhb_signals(
        self,
        *,
        include_cache_fallback: bool = True,
        allow_cache_compute: bool = True,
        allow_async_snapshot_refresh: bool = True,
    ) -> list[StockSignal]:
        signals: list[StockSignal] = []
        seen_codes: set[str] = set()
        lhb_tab = self._get_tab("lhb")
        if not self._has_tab_key("lhb", lhb_tab):
            return []
        rows = self._get_rows(lhb_tab)
        is_lhb_loading = bool(lhb_tab is not None and getattr(lhb_tab, "_pool_load_in_progress", False))
        if not rows and include_cache_fallback and not is_lhb_loading:
            rows = (
                self._load_lhb_pool_rows()
                if allow_cache_compute
                else self._cached_lhb_pool_rows(allow_async_refresh=allow_async_snapshot_refresh)
            )
        for row_idx, row in enumerate(rows):
            code = str(row.get(KEY_CODE, "")).strip()
            if not code or code in seen_codes:
                continue
            seen_codes.add(code)

            raw_date = str(row.get(KEY_LAST_LISTED_RAW, "") or row.get(KEY_LAST_LISTED, "") or "")
            if len(raw_date) == 8:
                date_mmdd = f"{raw_date[4:6]}-{raw_date[6:8]}"
            elif "-" in raw_date:
                parts = raw_date.split("-")
                date_mmdd = "-".join(parts[-2:]) if len(parts) >= 2 else raw_date
            else:
                date_mmdd = raw_date

            net = self._safe_float(row.get(KEY_NET_WAN, 0))
            inst = self._safe_float(row.get(KEY_INST_WAN, 0))
            foreign = self._safe_float(row.get(KEY_FOREIGN_WAN, 0))

            net_text = f"{TEXT_NET_SELL}{abs(net):.0f}\u4e07" if net < 0 else f"{TEXT_NET_BUY}{net:.0f}\u4e07"
            inst_text = (
                f"{TEXT_INST_NET_SELL}{abs(inst):.0f}\u4e07" if inst < 0 else f"{TEXT_INST_NET_BUY}{inst:.0f}\u4e07"
            )
            foreign_text = (
                f"{TEXT_FOREIGN_NET_SELL}{abs(foreign):.0f}\u4e07"
                if foreign < 0
                else f"{TEXT_FOREIGN_NET_BUY}{foreign:.0f}\u4e07"
            )
            summary = f"{date_mmdd} | {net_text} | {inst_text} | {foreign_text}"

            signals.append(
                StockSignal(
                    code=code,
                    name=str(row.get(KEY_NAME, "") or ""),
                    source_tab="lhb",
                    source_label="lhb",
                    signal_type=SIGNAL_LHB,
                    summary=summary,
                    numeric_value=net,
                    observed_at=raw_date,
                    row_ref=row_idx,
                    payload={
                        **dict(row),
                        "date": raw_date,
                        "net_wan": net,
                        "inst_wan": inst,
                        "foreign_wan": foreign,
                    },
                )
            )
        return signals

    def iter_stock_signals(
        self,
        *,
        include_cache_fallback: bool = True,
        include_source_cache_fallback: bool | None = None,
        allow_lhb_cache_compute: bool = False,
        allow_async_snapshot_refresh: bool = True,
        target_codes=None,
    ) -> list[StockSignal]:
        target_code_set = self._normalize_target_codes(target_codes)
        if target_codes is not None and not target_code_set:
            return []
        target_code_filter = target_code_set if target_codes is not None else None
        direct_keys = self._direct_signal_tab_keys()
        signals = self._iter_direct_stock_signals()
        source_cache_fallback = include_cache_fallback if include_source_cache_fallback is None else bool(
            include_source_cache_fallback
        )

        if "scan" not in direct_keys:
            signals.extend(self._iter_scan_signals(include_cache_fallback=include_cache_fallback))
        if "ai_industry_chain" not in direct_keys:
            signals.extend(self._iter_ai_chain_signals(include_cache_fallback=source_cache_fallback))
        if "na_daily" not in direct_keys:
            signals.extend(self._iter_na_daily_signals(include_cache_fallback=source_cache_fallback))
        if "foreign_block" not in direct_keys:
            signals.extend(self._iter_block_trade_signals(include_cache_fallback=source_cache_fallback))
        if "earnings" not in direct_keys:
            signals.extend(self._iter_earnings_signals(include_cache_fallback=source_cache_fallback))
        if "fund_holdings" not in direct_keys:
            signals.extend(
                self._iter_fund_holdings_signals(
                    allow_async_snapshot_refresh=allow_async_snapshot_refresh,
                    target_codes=target_code_filter,
                )
            )
        if "lhb" not in direct_keys:
            signals.extend(
                self._iter_lhb_signals(
                    include_cache_fallback=source_cache_fallback,
                    allow_cache_compute=allow_lhb_cache_compute,
                    allow_async_snapshot_refresh=allow_async_snapshot_refresh,
                )
            )

        normalized_signals = [signal for signal in signals if signal.normalized_code()]
        if target_codes is not None:
            return [signal for signal in normalized_signals if signal.normalized_code() in target_code_set]
        return normalized_signals

    def collect_signals_by_code(
        self,
        *,
        include_cache_fallback: bool = True,
        include_source_cache_fallback: bool | None = None,
        allow_lhb_cache_compute: bool = False,
        allow_async_snapshot_refresh: bool = True,
    ) -> dict[str, list[StockSignal]]:
        signals_by_code: dict[str, list[StockSignal]] = defaultdict(list)
        for signal in self.iter_stock_signals(
            include_cache_fallback=include_cache_fallback,
            include_source_cache_fallback=include_source_cache_fallback,
            allow_lhb_cache_compute=allow_lhb_cache_compute,
            allow_async_snapshot_refresh=allow_async_snapshot_refresh,
        ):
            signals_by_code[signal.normalized_code()].append(signal)
        return dict(signals_by_code)

    def collect_watchlist_radar_data(
        self,
        *,
        include_cache_fallback: bool = False,
        include_source_cache_fallback: bool | None = None,
        target_codes=None,
        allow_lhb_cache_compute: bool = False,
    ) -> tuple[dict, dict, dict, dict, dict, dict | None]:
        workspace = self._workspace
        engine = getattr(workspace, "engine", None)
        rps_bundle = engine.get_precomputed_rps() if hasattr(engine, "get_precomputed_rps") else None

        target_code_set = self._normalize_target_codes(target_codes)
        target_code_filter = target_code_set if target_codes is not None else None
        signals = self.iter_stock_signals(
            include_cache_fallback=include_cache_fallback,
            include_source_cache_fallback=include_source_cache_fallback,
            allow_lhb_cache_compute=allow_lhb_cache_compute,
            target_codes=target_code_filter,
        )
        remark_data, na_subsector_data, block_data, earn_data, lhb_data = {}, {}, {}, {}, {}

        def in_scope(code: str) -> bool:
            return target_codes is None or code in target_code_set

        ai_subsector_data: dict[str, str] = {}
        na_subsector_fallback: dict[str, str] = {}
        for signal in signals:
            code = signal.normalized_code()
            if not in_scope(code):
                continue
            if signal.source_tab == "na_daily" and signal.signal_type == SIGNAL_CATALYST:
                continue
            if signal.source_tab == "na_daily" and signal.signal_type == SIGNAL_SUBSECTOR:
                na_subsector_fallback.setdefault(code, signal.summary)
            if signal.source_tab == "ai_industry_chain" and signal.signal_type == SIGNAL_SUBSECTOR:
                if signal.summary:
                    ai_subsector_data.setdefault(code, signal.summary)
                remark = str((signal.payload or {}).get(KEY_REMARK, "") or "").strip()
                if remark:
                    remark_data.setdefault(code, remark)

        na_subsector_data.update(ai_subsector_data)
        for code, summary in na_subsector_fallback.items():
            na_subsector_data.setdefault(code, summary)

        for signal in signals:
            code = signal.normalized_code()
            if not in_scope(code):
                continue
            if signal.signal_type == SIGNAL_BLOCK_TRADE and signal.summary:
                block_data[code] = {
                    "text": signal.summary,
                    "amount_wan": signal.payload.get("amount_wan", signal.numeric_value or 0.0),
                }
            elif signal.signal_type == SIGNAL_EARNINGS and signal.summary:
                earn_data[code] = {
                    "text": signal.summary,
                    "qoq_pct": signal.payload.get("qoq_pct", signal.numeric_value or 0.0),
                }
            elif signal.signal_type == SIGNAL_LHB and signal.summary:
                lhb_data[code] = {
                    "text": signal.summary,
                    "date": signal.payload.get("date", signal.observed_at),
                    "net_wan": signal.payload.get("net_wan", signal.numeric_value or 0.0),
                    "inst_wan": signal.payload.get("inst_wan", 0.0),
                    "foreign_wan": signal.payload.get("foreign_wan", 0.0),
                    "buy_point": str(signal.payload.get("买点", "") or "").strip(),
                }

        return remark_data, na_subsector_data, block_data, earn_data, lhb_data, rps_bundle
