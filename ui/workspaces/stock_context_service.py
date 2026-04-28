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
KEY_REPORT_PERIOD = "\u62a5\u544a\u671f"
KEY_REPORT_NAME = "\u8d22\u62a5\u540d\u79f0"
KEY_REPORT_TITLE = "\u62a5\u544a\u540d\u79f0"
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

    def _iter_scan_signals(self) -> list[StockSignal]:
        scan_tab = self._get_tab("scan")
        if scan_tab is None:
            return []

        scan_reader = getattr(scan_tab, "get_scan_results", None)
        rows = list(scan_reader() or []) if callable(scan_reader) else []
        if not rows:
            rows = self._get_rows(scan_tab)

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
            report_label = self._earnings_report_label(row)
            summary = f"{report_label} {qoq_display}%" if report_label else f"{qoq_display}%"
            signals.append(
                StockSignal(
                    code=code,
                    name=str(row.get(KEY_NAME, "") or ""),
                    source_tab="earnings",
                    source_label="earnings",
                    signal_type=SIGNAL_EARNINGS,
                    summary=summary,
                    numeric_value=qoq_value,
                    row_ref=row_idx,
                    payload={
                        **dict(row),
                        "qoq_pct": qoq_value,
                    },
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

    def _iter_fund_holdings_signals(self) -> list[StockSignal]:
        signals: list[StockSignal] = []
        rows = self._fund_holding_rows()
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

    def _query_fund_holding_store_rows(self) -> list[dict]:
        try:
            from app.services.ui_runtime_service import (
                QFII_CAPITAL_ATTRIBUTE_UNMARKED,
                SUBJECT_QFII,
                fund_holdings_store,
            )
        except (ImportError, RuntimeError):
            return []

        try:
            latest_quarter_map = dict(fund_holdings_store.get_latest_quarter_map() or {})
            change_rows = list(fund_holdings_store.query_change_rows() or [])
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return []

        view_rows: list[dict] = []
        qfii_subject_code = str((SUBJECT_QFII or {}).get("subject_code") or "")
        for row in change_rows:
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
                        capital_attribute
                        if capital_attribute != QFII_CAPITAL_ATTRIBUTE_UNMARKED
                        else ""
                    ),
                    KEY_SUBJECT_CODE: subject_code,
                    KEY_QUARTER: quarter_key,
                    KEY_CHANGE_TYPE: change_type,
                    KEY_CURRENT_RATIO: self._format_fund_holding_pct(row.get("curr_ratio_pct")),
                    KEY_HOLDING_DELTA: self._format_fund_holding_amount(row.get("delta_hold_num_shares")),
                    "_is_latest_subject_quarter": True,
                }
            )
        return view_rows

    def _fund_holding_rows(self) -> list[dict]:
        if not self._has_fund_holdings_tab():
            return []
        rows = self._query_fund_holding_store_rows()
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

        if "scan" not in direct_keys:
            signals.extend(self._iter_scan_signals())
        if "na_daily" not in direct_keys:
            signals.extend(self._iter_na_daily_signals())
        if "ai_industry_chain" not in direct_keys:
            signals.extend(self._iter_ai_chain_signals())
        if "foreign_block" not in direct_keys:
            signals.extend(self._iter_block_trade_signals())
        if "earnings" not in direct_keys:
            signals.extend(self._iter_earnings_signals())
        if "fund_holdings" not in direct_keys:
            signals.extend(self._iter_fund_holdings_signals())
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
