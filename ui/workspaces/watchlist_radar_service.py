# -*- coding: utf-8 -*-
from __future__ import annotations

KEY_CODE = "\u4ee3\u7801"
KEY_NAME = "\u540d\u79f0"
KEY_CATALYST = "\u50ac\u5316\u5242"
KEY_CATALYST_EMOJI = "\U0001f4e0\u50ac\u5316\u5242"
KEY_SUBSECTOR = "\u7ec6\u5206\u677f\u5757"
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


class WatchlistRadarService:
    """Collects cross-tab radar data for the watchlist view."""

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

    @staticmethod
    def _get_rows(tab) -> list[dict]:
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

    def collect_radar_data(self) -> tuple[dict, dict, dict, dict, dict, dict | None]:
        workspace = self._workspace
        na_data, na_subsector_data, block_data, earn_data, lhb_data = {}, {}, {}, {}, {}
        engine = getattr(workspace, "engine", None)
        rps_bundle = engine.get_precomputed_rps() if hasattr(engine, "get_precomputed_rps") else None

        for row in self._get_rows(self._get_tab("na_daily")):
            code = str(row.get(KEY_CODE, "")).strip()
            if not code:
                continue
            na_data[code] = str(row.get(KEY_CATALYST, "") or row.get(KEY_CATALYST_EMOJI, ""))
            na_subsector_data[code] = str(row.get(KEY_SUBSECTOR, "") or "")

        foreign_block_tab = self._get_tab("foreign_block")
        if foreign_block_tab is not None:
            from ui.tabs.foreign_block_trade_tab import FOREIGN_KEYWORDS

            block_aggregates: dict[str, dict] = {}
            for row in self._get_rows(foreign_block_tab):
                code = str(row.get(KEY_CODE, "")).strip()
                if not code:
                    continue

                detail = str(row.get(KEY_DETAIL, "") or "")
                buy = str(row.get(KEY_BUY_BRANCH, "") or "")
                sell = str(row.get(KEY_SELL_BRANCH, "") or "")
                amount = self._safe_float(row.get(KEY_AMOUNT_WAN, 0))

                bucket = block_aggregates.setdefault(
                    code,
                    {
                        "best_text": "",
                        "best_amount": 0.0,
                    },
                )

                signal_text, signal_amount = self._build_watchlist_block_trade_signal(
                    detail,
                    buy,
                    sell,
                    amount,
                    FOREIGN_KEYWORDS,
                )
                best_amount = float(bucket.get("best_amount", 0.0) or 0.0)
                if signal_text and signal_amount >= best_amount:
                    bucket["best_text"] = signal_text
                    bucket["best_amount"] = signal_amount

            for code, stats in block_aggregates.items():
                best_text = str(stats.get("best_text", "") or "")
                best_amount = float(stats.get("best_amount", 0.0) or 0.0)
                if best_text:
                    block_data[code] = {
                        "text": best_text,
                        "amount_wan": best_amount,
                    }

        for row in self._get_rows(self._get_tab("earnings")):
            code = str(row.get(KEY_CODE, "")).strip()
            if not code:
                continue

            qoq_raw = row.get(KEY_QOQ_PCT)
            qoq_text = str(qoq_raw).strip()
            if not qoq_text:
                continue

            qoq_value = self._safe_float(qoq_raw, default=0.0)
            qoq_display = f"{qoq_value:.2f}".rstrip("0").rstrip(".")
            earn_data[code] = {
                "text": f"{qoq_display}%",
                "qoq_pct": qoq_value,
            }

        for row in self._get_rows(self._get_tab("lhb")):
            code = str(row.get(KEY_CODE, "")).strip()
            if not code or code in lhb_data:
                continue

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

            lhb_data[code] = {
                "text": f"{date_mmdd} | {net_text} | {inst_text} | {foreign_text}",
                "date": raw_date,
                "net_wan": net,
                "inst_wan": inst,
                "foreign_wan": foreign,
            }

        return na_data, na_subsector_data, block_data, earn_data, lhb_data, rps_bundle
