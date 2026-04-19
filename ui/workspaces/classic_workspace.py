# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from core.logger import get_logger
from ui.tabs.asian_market_tab import AsianMarketTab
from ui.tabs.earnings_tab import EarningsTab
from ui.tabs.foreign_block_trade_tab import ForeignBlockTradeTab
from ui.tabs.fund_holdings_tab import FundHoldingsTab
from ui.tabs.lhb_tab import LhbTab
from ui.tabs.log_tab import LogTab
from ui.tabs.na_daily_tab import NADailyTab
from ui.tabs.rt_monitor_tab import RtMonitorTab
from ui.tabs.scan_tab import ScanTab
from ui.tabs.watchlist_tab import WatchlistTab

log = get_logger(__name__)


class ClassicWorkspace(QWidget):
    mode = "classic"

    def __init__(self, data_provider, engine, host=None, parent=None):
        super().__init__(parent)
        self.data_provider = data_provider
        self.engine = engine
        self.host = host

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = QTabWidget(self)
        self.tabs.setDocumentMode(True)
        layout.addWidget(self.tabs, 1)

        self._tab_specs = [
            {
                "key": "watchlist",
                "title": "关注池",
                "group": "主工作台",
                "group_order": 10,
                "attr": "tab_watchlist",
                "widget": WatchlistTab(self.data_provider, self),
            },
            {
                "key": "asian_market",
                "title": "亚洲寡头",
                "group": "主工作台",
                "group_order": 20,
                "attr": "tab_asian_market",
                "widget": AsianMarketTab(self.data_provider, self),
            },
            {
                "key": "na_daily",
                "title": "北美战报",
                "group": "主工作台",
                "group_order": 30,
                "attr": "tab_na_daily",
                "widget": NADailyTab(self.data_provider, self),
            },
            {
                "key": "rt_monitor",
                "title": "盘中监控",
                "group": "主工作台",
                "group_order": 40,
                "attr": "tab_rt",
                "widget": RtMonitorTab(self.data_provider, self.engine, self),
            },
            {
                "key": "scan",
                "title": "VCP扫描",
                "group": "情报源",
                "group_order": 10,
                "attr": "tab_scan",
                "widget": ScanTab(self.data_provider, self.engine, self),
            },
            {
                "key": "lhb",
                "title": "龙虎榜",
                "group": "情报源",
                "group_order": 20,
                "attr": "tab_lhb",
                "widget": LhbTab(self.data_provider, self),
            },
            {
                "key": "foreign_block",
                "title": "大宗交易",
                "group": "情报源",
                "group_order": 30,
                "attr": "tab_foreign_block",
                "widget": ForeignBlockTradeTab(self.data_provider, self),
            },
            {
                "key": "earnings",
                "title": "业绩异动",
                "group": "情报源",
                "group_order": 40,
                "attr": "tab_earnings",
                "widget": EarningsTab(self.data_provider, self),
            },
            {
                "key": "fund_holdings",
                "title": "基金持仓",
                "group": "情报源",
                "group_order": 50,
                "attr": "tab_fund_holdings",
                "widget": FundHoldingsTab(self.data_provider, self),
            },
            {
                "key": "system_log",
                "title": "系统日志",
                "group": "系统",
                "group_order": 10,
                "attr": "tab_log",
                "widget": LogTab(self),
            },
        ]

        for spec in self._tab_specs:
            setattr(self, spec["attr"], spec["widget"])
            self.tabs.addTab(spec["widget"], spec["title"])

    def tab_specs(self) -> list[dict]:
        return list(self._tab_specs)

    def nav_groups(self) -> list[str]:
        groups: list[str] = []
        for spec in self._tab_specs:
            group = str(spec.get("group", "")).strip()
            if group and group not in groups:
                groups.append(group)
        return groups

    def tab_indices_by_group(self) -> dict[str, list[int]]:
        result: dict[str, list[int]] = {}
        for index, spec in enumerate(self._tab_specs):
            group = str(spec.get("group", "")).strip()
            result.setdefault(group, []).append(index)
        for group, indices in result.items():
            result[group] = sorted(
                indices,
                key=lambda idx: (
                    int(self._tab_specs[idx].get("group_order", idx) or idx),
                    idx,
                ),
            )
        return result

    def restore_last_tab(self, index: int):
        if 0 <= index < self.tabs.count():
            self.tabs.setCurrentIndex(index)

    def current_tab_index(self) -> int:
        return self.tabs.currentIndex()

    @staticmethod
    def _extract_a_share_codes(model_data) -> set[str]:
        codes: set[str] = set()
        for row in model_data or []:
            code = str((row or {}).get("代码", "")).strip()
            if len(code) == 6 and code.isdigit():
                codes.add(code)
        return codes

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

    def get_realtime_quote_codes(self) -> set[str]:
        codes: set[str] = set()
        extract_codes = ClassicWorkspace._extract_a_share_codes

        scan_model = getattr(getattr(self, "tab_scan", None), "source_model", None)
        if scan_model is not None:
            codes.update(extract_codes(getattr(scan_model, "row_data", None)))

        rt_model = getattr(getattr(self, "tab_rt", None), "source_model", None)
        if rt_model is not None:
            codes.update(extract_codes(getattr(rt_model, "row_data", None)))

        watchlist_model = getattr(getattr(self, "tab_watchlist", None), "model", None)
        if watchlist_model is not None:
            codes.update(extract_codes(getattr(watchlist_model, "row_data", None)))

        foreign_block = getattr(self, "tab_foreign_block", None)
        foreign_model = getattr(foreign_block, "model", None)
        if foreign_model is not None:
            codes.update(extract_codes(getattr(foreign_model, "row_data", None)))
        elif foreign_block is not None:
            for code in getattr(foreign_block, "_block_trade_codes", []) or []:
                code_text = str(code or "").strip()
                if len(code_text) == 6 and code_text.isdigit():
                    codes.add(code_text)

        na_daily_model = getattr(getattr(self, "tab_na_daily", None), "model", None)
        if na_daily_model is not None:
            codes.update(extract_codes(getattr(na_daily_model, "row_data", None)))

        earnings_model = getattr(getattr(self, "tab_earnings", None), "model", None)
        if earnings_model is not None:
            codes.update(extract_codes(getattr(earnings_model, "row_data", None)))

        lhb_model = getattr(getattr(self, "tab_lhb", None), "model", None)
        if lhb_model is not None:
            codes.update(extract_codes(getattr(lhb_model, "row_data", None)))

        return codes

    def get_scan_results(self) -> list[dict]:
        return list(getattr(getattr(self, "tab_scan", None), "_current_results", []) or [])

    def get_rt_table(self):
        return getattr(getattr(self, "tab_rt", None), "table_rt", None)

    def iter_tables(self) -> list:
        return [
            table
            for table in [
                getattr(getattr(self, "tab_scan", None), "table_scan", None),
                self.get_rt_table(),
                getattr(getattr(self, "tab_watchlist", None), "table_sp", None),
                getattr(getattr(self, "tab_na_daily", None), "na_daily_table", None),
                getattr(getattr(self, "tab_console", None), "table", None),
                getattr(getattr(self, "tab_lhb", None), "table", None),
                getattr(getattr(self, "tab_foreign_block", None), "table", None),
                getattr(getattr(self, "tab_fund_holdings", None), "table", None),
                getattr(getattr(self, "tab_asian_market", None), "asian_table", None),
                getattr(getattr(self, "tab_earnings", None), "table", None),
            ]
            if table is not None
        ]

    def iter_refreshable_tabs(self) -> list:
        return [
            tab
            for tab in [
                getattr(self, "tab_watchlist", None),
                getattr(self, "tab_lhb", None),
                getattr(self, "tab_na_daily", None),
                getattr(self, "tab_asian_market", None),
                getattr(self, "tab_rt", None),
                getattr(self, "tab_foreign_block", None),
                getattr(self, "tab_fund_holdings", None),
                getattr(self, "tab_earnings", None),
                getattr(self, "tab_scan", None),
            ]
            if tab is not None and hasattr(tab, "refresh_table_from_latest_snapshot")
        ]

    def refresh_all_tabs_after_f5(self) -> None:
        for tab in self.iter_refreshable_tabs():
            try:
                tab.refresh_table_from_latest_snapshot()
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[F5] {tab.__class__.__name__} 表格快照回灌失败: {exc}")

    def find_scan_result(self, code: str) -> dict | None:
        code_text = str(code or "").strip()
        if not code_text:
            return None
        for row in self.get_scan_results():
            if isinstance(row, dict) and str(row.get("代码", "")).strip() == code_text:
                return row
        return None

    def select_scan_row(self, index: int) -> bool:
        table = getattr(getattr(self, "tab_scan", None), "table_scan", None)
        if table is None or index < 0:
            return False
        try:
            table.selectRow(index)
        except (AttributeError, RuntimeError, TypeError):
            return False
        return True

    @staticmethod
    def _iter_tab_tables(tab) -> list:
        tables = []
        for attr_name in ("table_sp", "table_scan", "table_rt", "na_daily_table", "asian_table", "table"):
            table = getattr(tab, attr_name, None)
            if table is not None and hasattr(table, "model") and table not in tables:
                tables.append(table)
        return tables

    @staticmethod
    def _find_code_column(model) -> int:
        if model is None:
            return -1
        try:
            column_count = int(model.columnCount())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return -1

        for column in range(column_count):
            try:
                header_text = str(
                    model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) or ""
                ).strip()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                header_text = ""
            if header_text == "代码":
                return column
        return -1

    @classmethod
    def _select_code_in_tab(cls, tab, code: str) -> bool:
        code_text = str(code or "").strip()
        if not code_text:
            return False

        for table in cls._iter_tab_tables(tab):
            model = table.model()
            code_column = cls._find_code_column(model)
            if code_column < 0:
                continue

            try:
                row_count = int(model.rowCount())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue

            for row in range(row_count):
                try:
                    index = model.index(row, code_column)
                    row_code = str(model.data(index, Qt.ItemDataRole.DisplayRole) or "").strip()
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    continue
                if row_code != code_text:
                    continue

                try:
                    table.clearSelection()
                    table.setCurrentIndex(index)
                    table.selectRow(row)
                    table.scrollTo(index)
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    return False
                return True

        return False

    def select_code_row(self, code: str, preferred_tab_index: int | None = None) -> bool:
        code_text = str(code or "").strip()
        if not code_text:
            return False

        tab_widget = getattr(self, "tabs", None)
        if tab_widget is None:
            return False

        current_index = tab_widget.currentIndex()
        candidate_indices: list[int] = []

        if 0 <= current_index < tab_widget.count():
            candidate_indices.append(current_index)

        if isinstance(preferred_tab_index, int) and 0 <= preferred_tab_index < tab_widget.count():
            if preferred_tab_index not in candidate_indices:
                candidate_indices.append(preferred_tab_index)

        for tab_index in range(tab_widget.count()):
            if tab_index not in candidate_indices:
                candidate_indices.append(tab_index)

        for tab_index in candidate_indices:
            tab = tab_widget.widget(tab_index)
            if tab is None:
                continue
            if self._select_code_in_tab(tab, code_text):
                if tab_index != current_index:
                    tab_widget.setCurrentIndex(tab_index)
                return True

        return False

    def refresh_watchlist_names(self, code2name: dict[str, str]) -> bool:
        model = getattr(getattr(self, "tab_watchlist", None), "model", None)
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

    def schedule_watchlist_special_quotes(self, task_manager) -> None:
        watchlist_tab = getattr(self, "tab_watchlist", None)
        prime_state = getattr(watchlist_tab, "prime_startup_state", None)
        if callable(prime_state):
            prime_state()

    def run_post_online_refresh(self, task_manager) -> None:
        for attr_name in ("tab_na_daily", "tab_foreign_block"):
            tab = getattr(self, attr_name, None)
            auto_refresh = getattr(tab, "_auto_refresh_realtime", None)
            if callable(auto_refresh):
                auto_refresh(force=True)

        self.schedule_watchlist_special_quotes(task_manager)

    def auto_start_rt_monitor(self) -> bool:
        rt_tab = getattr(self, "tab_rt", None)
        toggle_monitor = getattr(rt_tab, "_toggle_rt_monitor", None)
        if not callable(toggle_monitor):
            return False
        toggle_monitor(auto=True)
        return True

    def collect_watchlist_radar_data(self) -> tuple[dict, dict, dict, dict, dict, dict | None]:
        na_data, na_subsector_data, block_data, earn_data, lhb_data = {}, {}, {}, {}, {}
        rps_bundle = self.engine.get_precomputed_rps() if hasattr(self, "engine") else None

        na_model = getattr(getattr(self, "tab_na_daily", None), "model", None)
        if na_model is not None:
            for row in getattr(na_model, "row_data", []) or []:
                code = str(row.get("代码", "")).strip()
                if not code:
                    continue
                na_data[code] = str(row.get("催化剂", "") or row.get("📨催化剂", ""))
                na_subsector_data[code] = str(row.get("细分板块", "") or "")

        foreign_model = getattr(getattr(self, "tab_foreign_block", None), "model", None)
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
                if best_text:
                    block_data[code] = best_text

        earnings_model = getattr(getattr(self, "tab_earnings", None), "model", None)
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
                earn_data[code] = f"{qoq_display}%"

        lhb_model = getattr(getattr(self, "tab_lhb", None), "model", None)
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
                # “外资净买入”是展示字符串（如“净买123万”），不能直接转 float。
                # 关注池汇总应读取数值字段“外资净买(万)”。
                fgn = self._safe_float(row.get("外资净买(万)", 0))

                net_s = f"净卖{abs(net):.0f}万" if net < 0 else f"净买{net:.0f}万"
                jg_s = f"机构净卖{abs(jg):.0f}万" if jg < 0 else f"机构净买{jg:.0f}万"
                fgn_s = f"外资净卖{abs(fgn):.0f}万" if fgn < 0 else f"外资净买{fgn:.0f}万"

                lhb_data[code] = {
                    "text": f"{date_mmdd} | {net_s} | {jg_s} | {fgn_s}",
                    "date": raw_date,
                }

        return na_data, na_subsector_data, block_data, earn_data, lhb_data, rps_bundle

    def open_security_detail(self, code: str, context=None):
        return None

    def shutdown(self):
        rt_worker = getattr(self.tab_rt, "rt_worker", None)
        if rt_worker is not None and rt_worker.isRunning():
            self.tab_rt._manual_stop_requested = False
            self.tab_rt._rt_stop_requested = True
            self.tab_rt._toggle_rt_monitor(auto=True)
            rt_worker.wait(2000)

        scan_worker = getattr(self.tab_scan, "worker", None)
        if scan_worker is not None and scan_worker.isRunning():
            self.tab_scan.cancel_scan()
            scan_worker.wait(2000)

        asian_auto_timer = getattr(self.tab_asian_market, "auto_cache_timer", None)
        if asian_auto_timer is not None:
            asian_auto_timer.stop()

        asian_cache_thread = getattr(self.tab_asian_market, "cache_thread", None)
        if asian_cache_thread is not None and asian_cache_thread.isRunning():
            asian_cache_thread.wait(2000)

        asian_worker = getattr(self.tab_asian_market, "worker", None)
        if asian_worker is not None and asian_worker.isRunning():
            asian_worker.stop()
            asian_worker.wait(2000)

        auto_timer = getattr(self.tab_rt, "_auto_timer", None)
        if auto_timer is not None:
            auto_timer.stop()

        log_flush_timer = getattr(self.tab_log, "_log_flush_timer", None)
        if log_flush_timer is not None:
            log_flush_timer.stop()
