# -*- coding: utf-8 -*-
from __future__ import annotations


class WatchlistRadarService:
    """聚合关注池依赖的跨 Tab 雷达数据。"""

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
        if "机构专用" in text:
            return "机构专用"
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

        if "买入" in detail_text:
            if buy_label:
                return f"{buy_label}买入{amount:.0f}万", amount
            if sell_label:
                return f"{sell_label}卖出{amount:.0f}万", amount

        if "卖出" in detail_text:
            if sell_label:
                return f"{sell_label}卖出{amount:.0f}万", amount
            if buy_label:
                return f"{buy_label}买入{amount:.0f}万", amount

        if buy and sell and buy == sell:
            return f"大宗对倒 {amount:.0f}万", amount

        return "", 0.0

    def refresh_watchlist_names(self, code2name: dict[str, str]) -> bool:
        model = getattr(getattr(self._workspace, "tab_watchlist", None), "model", None)
        if model is None:
            return False

        changed = False
        for row in getattr(model, "row_data", []) or []:
            code = str(row.get("代码", "")).strip()
            name = str(row.get("名称", "")).strip()
            if code and (not name or name == code):
                resolved = str(code2name.get(code, code)).strip()
                if resolved and resolved != name:
                    row["名称"] = resolved
                    changed = True

        if changed:
            model.layoutChanged.emit()
        return changed

    def prime_watchlist_state(self) -> None:
        watchlist_tab = getattr(self._workspace, "tab_watchlist", None)
        prime_state = getattr(watchlist_tab, "prime_startup_state", None)
        if callable(prime_state):
            prime_state()

    def collect_radar_data(self) -> tuple[dict, dict, dict, dict, dict, dict | None]:
        workspace = self._workspace
        na_data, na_subsector_data, block_data, earn_data, lhb_data = {}, {}, {}, {}, {}
        engine = getattr(workspace, "engine", None)
        rps_bundle = engine.get_precomputed_rps() if hasattr(engine, "get_precomputed_rps") else None

        na_model = getattr(getattr(workspace, "tab_na_daily", None), "model", None)
        if na_model is not None:
            for row in getattr(na_model, "row_data", []) or []:
                code = str(row.get("代码", "")).strip()
                if not code:
                    continue
                na_data[code] = str(row.get("催化剂", "") or row.get("📨催化剂", ""))
                na_subsector_data[code] = str(row.get("细分板块", "") or "")

        foreign_model = getattr(getattr(workspace, "tab_foreign_block", None), "model", None)
        if foreign_model is not None:
            from ui.tabs.foreign_block_trade_tab import FOREIGN_KEYWORDS

            block_aggregates: dict[str, dict] = {}
            for row in getattr(foreign_model, "row_data", []) or []:
                code = str(row.get("代码", "")).strip()
                if not code:
                    continue

                detail = str(row.get("交易详情", "") or "")
                buy = str(row.get("买方营业部", "") or "")
                sell = str(row.get("卖方营业部", "") or "")
                amount = self._safe_float(row.get("成交金额(万元)", 0))

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

        earnings_model = getattr(getattr(workspace, "tab_earnings", None), "model", None)
        if earnings_model is not None:
            for row in getattr(earnings_model, "row_data", []) or []:
                code = str(row.get("代码", "")).strip()
                if not code:
                    continue

                qoq_raw = row.get("环比%")
                qoq_text = str(qoq_raw).strip()
                if not qoq_text:
                    continue

                qoq_value = self._safe_float(qoq_raw, default=0.0)
                qoq_display = f"{qoq_value:.2f}".rstrip("0").rstrip(".")
                earn_data[code] = {
                    "text": f"{qoq_display}%",
                    "qoq_pct": qoq_value,
                }

        lhb_model = getattr(getattr(workspace, "tab_lhb", None), "model", None)
        if lhb_model is not None:
            for row in getattr(lhb_model, "row_data", []) or []:
                code = str(row.get("代码", "")).strip()
                if not code or code in lhb_data:
                    continue

                raw_date = str(row.get("_最近上榜_raw", "") or row.get("最近上榜", "") or "")
                if len(raw_date) == 8:
                    date_mmdd = f"{raw_date[4:6]}-{raw_date[6:8]}"
                elif "-" in raw_date:
                    parts = raw_date.split("-")
                    date_mmdd = "-".join(parts[-2:]) if len(parts) >= 2 else raw_date
                else:
                    date_mmdd = raw_date

                net = self._safe_float(row.get("上榜净买额(万)", 0))
                jg = self._safe_float(row.get("机构净买(万)", 0))
                fgn = self._safe_float(row.get("外资净买(万)", 0))

                net_s = f"净卖{abs(net):.0f}万" if net < 0 else f"净买{net:.0f}万"
                jg_s = f"机构净卖{abs(jg):.0f}万" if jg < 0 else f"机构净买{jg:.0f}万"
                fgn_s = f"外资净卖{abs(fgn):.0f}万" if fgn < 0 else f"外资净买{fgn:.0f}万"

                lhb_data[code] = {
                    "text": f"{date_mmdd} | {net_s} | {jg_s} | {fgn_s}",
                    "date": raw_date,
                    "net_wan": net,
                    "inst_wan": jg,
                    "foreign_wan": fgn,
                }

        return na_data, na_subsector_data, block_data, earn_data, lhb_data, rps_bundle
