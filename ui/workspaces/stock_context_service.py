# -*- coding: utf-8 -*-
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from ui.workspaces.stock_signal import StockSignal, coerce_stock_signal
from ui.workspaces.tab_capabilities import ForeignKeywordCapability, StockSignalSourceCapability

KEY_CODE = "\u4ee3\u7801"
KEY_NAME = "\u540d\u79f0"
KEY_CATALYST = "\u50ac\u5316\u5242"
KEY_CATALYST_EMOJI = "\U0001f4e0\u50ac\u5316\u5242"
KEY_SUBSECTOR = "\u7ec6\u5206\u677f\u5757"
KEY_OLD_CHAIN_SEGMENT = "\u7ec6\u5206\u73af\u8282"
KEY_DETAIL = "\u4ea4\u6613\u8be6\u60c5"
KEY_BUY_BRANCH = "\u4e70\u65b9\u8425\u4e1a\u90e8"
KEY_SELL_BRANCH = "\u5356\u65b9\u8425\u4e1a\u90e8"
KEY_AMOUNT_WAN = "\u6210\u4ea4\u91d1\u989d(\u4e07\u5143)"
KEY_QOQ_PCT = "\u73af\u6bd4%"
KEY_LAST_LISTED_RAW = "_\u6700\u8fd1\u4e0a\u699c_raw"
KEY_LAST_LISTED = "\u6700\u8fd1\u4e0a\u699c"
KEY_NET_WAN = "\u4e0a\u699c\u51c0\u4e70\u989d(\u4e07)"
KEY_INST_WAN = "\u673a\u6784\u51c0\u4e70(\u4e07)"
KEY_FOREIGN_WAN = "\u5916\u8d44\u51c0\u4e70(\u4e07)"

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


class StockContextService:
    """Builds a stock-code keyed signal index across workspace tabs."""

    def __init__(self, workspace):
        self._workspace = workspace

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
        get_tab = getattr(self._workspace, "get_tab", None)
        if not callable(get_tab):
            return None
        return get_tab(key)

    def _tab_specs(self) -> list[dict]:
        tab_specs = getattr(self._workspace, "tab_specs", None)
        return list(tab_specs() or []) if callable(tab_specs) else []

    def _iter_tabs_with_keys(self) -> Iterable[tuple[str, object]]:
        seen_ids: set[int] = set()
        for spec in self._tab_specs():
            key = str(spec.get("key", "")).strip()
            tab = spec.get("widget") or self._get_tab(key)
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

    def _iter_na_daily_signals(self) -> list[StockSignal]:
        signals: list[StockSignal] = []
        for row_idx, row in enumerate(self._get_rows(self._get_tab("na_daily"))):
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

    def _iter_ai_chain_signals(self) -> list[StockSignal]:
        signals: list[StockSignal] = []
        seen_codes: set[str] = set()
        for row_idx, row in enumerate(self._get_rows(self._get_tab("ai_industry_chain"))):
            code = str(row.get(KEY_CODE, "")).strip()
            if not code or code in seen_codes:
                continue
            segment = str(row.get(KEY_SUBSECTOR, "") or row.get(KEY_OLD_CHAIN_SEGMENT, "") or "").strip()
            if not segment:
                continue
            seen_codes.add(code)
            signals.append(
                StockSignal(
                    code=code,
                    name=str(row.get(KEY_NAME, "") or ""),
                    source_tab="ai_industry_chain",
                    source_label="ai_industry_chain",
                    signal_type=SIGNAL_SUBSECTOR,
                    summary=segment,
                    row_ref=row_idx,
                    payload=dict(row),
                )
            )
        return signals

    def _iter_block_trade_signals(self) -> list[StockSignal]:
        foreign_block_tab = self._get_tab("foreign_block")
        if foreign_block_tab is None:
            return []

        foreign_keywords = (
            foreign_block_tab.get_foreign_keywords()
            if isinstance(foreign_block_tab, ForeignKeywordCapability)
            else []
        )

        block_aggregates: dict[str, dict] = {}
        for row_idx, row in enumerate(self._get_rows(foreign_block_tab)):
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

    def _iter_earnings_signals(self) -> list[StockSignal]:
        signals: list[StockSignal] = []
        for row_idx, row in enumerate(self._get_rows(self._get_tab("earnings"))):
            code = str(row.get(KEY_CODE, "")).strip()
            if not code:
                continue

            qoq_raw = row.get(KEY_QOQ_PCT)
            qoq_text = str(qoq_raw).strip()
            if not qoq_text:
                continue

            qoq_value = self._safe_float(qoq_raw, default=0.0)
            qoq_display = f"{qoq_value:.2f}".rstrip("0").rstrip(".")
            signals.append(
                StockSignal(
                    code=code,
                    name=str(row.get(KEY_NAME, "") or ""),
                    source_tab="earnings",
                    source_label="earnings",
                    signal_type=SIGNAL_EARNINGS,
                    summary=f"{qoq_display}%",
                    numeric_value=qoq_value,
                    row_ref=row_idx,
                    payload={
                        **dict(row),
                        "qoq_pct": qoq_value,
                    },
                )
            )
        return signals

    def _iter_lhb_signals(self) -> list[StockSignal]:
        signals: list[StockSignal] = []
        seen_codes: set[str] = set()
        for row_idx, row in enumerate(self._get_rows(self._get_tab("lhb"))):
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
                f"{TEXT_INST_NET_SELL}{abs(inst):.0f}\u4e07"
                if inst < 0
                else f"{TEXT_INST_NET_BUY}{inst:.0f}\u4e07"
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

    def iter_stock_signals(self) -> list[StockSignal]:
        direct_keys = self._direct_signal_tab_keys()
        signals = self._iter_direct_stock_signals()

        if "na_daily" not in direct_keys:
            signals.extend(self._iter_na_daily_signals())
        if "ai_industry_chain" not in direct_keys:
            signals.extend(self._iter_ai_chain_signals())
        if "foreign_block" not in direct_keys:
            signals.extend(self._iter_block_trade_signals())
        if "earnings" not in direct_keys:
            signals.extend(self._iter_earnings_signals())
        if "lhb" not in direct_keys:
            signals.extend(self._iter_lhb_signals())

        return [signal for signal in signals if signal.normalized_code()]

    def collect_signals_by_code(self) -> dict[str, list[StockSignal]]:
        signals_by_code: dict[str, list[StockSignal]] = defaultdict(list)
        for signal in self.iter_stock_signals():
            signals_by_code[signal.normalized_code()].append(signal)
        return dict(signals_by_code)

    def collect_watchlist_radar_data(self) -> tuple[dict, dict, dict, dict, dict, dict | None]:
        workspace = self._workspace
        engine = getattr(workspace, "engine", None)
        rps_bundle = engine.get_precomputed_rps() if hasattr(engine, "get_precomputed_rps") else None

        signals = self.iter_stock_signals()
        na_data, na_subsector_data, block_data, earn_data, lhb_data = {}, {}, {}, {}, {}

        for signal in signals:
            code = signal.normalized_code()
            if signal.source_tab == "na_daily" and signal.signal_type == SIGNAL_CATALYST:
                na_data[code] = signal.summary
            if signal.source_tab == "na_daily" and signal.signal_type == SIGNAL_SUBSECTOR:
                na_subsector_data[code] = signal.summary

        seen_ai_chain_codes: set[str] = set()
        for signal in signals:
            code = signal.normalized_code()
            if signal.source_tab != "ai_industry_chain" or signal.signal_type != SIGNAL_SUBSECTOR:
                continue
            if code in seen_ai_chain_codes:
                continue
            if signal.summary:
                na_subsector_data[code] = signal.summary
                seen_ai_chain_codes.add(code)

        for signal in signals:
            code = signal.normalized_code()
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
                }

        return na_data, na_subsector_data, block_data, earn_data, lhb_data, rps_bundle
