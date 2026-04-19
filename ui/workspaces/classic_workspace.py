# -*- coding: utf-8 -*-
from __future__ import annotations

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

        self.tab_watchlist = WatchlistTab(self.data_provider, self)
        self.tab_lhb = LhbTab(self.data_provider, self)
        self.tab_na_daily = NADailyTab(self.data_provider, self)
        self.tab_asian_market = AsianMarketTab(self.data_provider, self)
        self.tab_rt = RtMonitorTab(self.data_provider, self.engine, self)
        self.tab_foreign_block = ForeignBlockTradeTab(self.data_provider, self)
        self.tab_fund_holdings = FundHoldingsTab(self.data_provider, self)
        self.tab_earnings = EarningsTab(self.data_provider, self)
        self.tab_scan = ScanTab(self.data_provider, self.engine, self)
        self.tab_log = LogTab(self)

        self.tabs.addTab(self.tab_watchlist, "关注池")
        self.tabs.addTab(self.tab_lhb, "龙虎榜")
        self.tabs.addTab(self.tab_na_daily, "美股日报")
        self.tabs.addTab(self.tab_asian_market, "亚洲寡头")
        self.tabs.addTab(self.tab_rt, "盘中监控")
        self.tabs.addTab(self.tab_foreign_block, "大宗交易")
        self.tabs.addTab(self.tab_fund_holdings, "基金持仓")
        self.tabs.addTab(self.tab_earnings, "业绩异动")
        self.tabs.addTab(self.tab_scan, "VCP扫描")
        self.tabs.addTab(self.tab_log, "系统日志")

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

        # 基金持仓包含股票过多，不纳入中央实时报价轮询池。
        # 该 Tab 仅在自身重载数据库后做一次性补价/补市值，避免盘中全局轮询卡顿。

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
                na_data[code] = str(row.get("催化剂", "") or row.get("💥催化剂", ""))
                na_subsector_data[code] = str(row.get("细分板块", "") or "")

        foreign_model = getattr(getattr(self, "tab_foreign_block", None), "model", None)
        if foreign_model is not None:
            from ui.tabs.foreign_block_trade_tab import FOREIGN_KEYWORDS

            block_aggregates: dict[str, dict[str, float]] = {}
            for row in getattr(foreign_model, "row_data", []) or []:
                code = str(row.get("代码", "")).strip()
                if not code:
                    continue

                detail = str(row.get("交易详情", "") or "")
                buy = str(row.get("买方营业部", "") or "")
                sell = str(row.get("卖方营业部", "") or "")
                try:
                    amount = float(str(row.get("成交金额(万元)", "0") or "0"))
                except (TypeError, ValueError):
                    amount = 0.0

                if "买入" in detail:
                    branch = buy
                    sign = 1.0
                elif "卖出" in detail:
                    branch = sell
                    sign = -1.0
                else:
                    branch = buy if buy else sell
                    sign = 1.0

                short_branch = branch
                for keyword in FOREIGN_KEYWORDS:
                    if keyword in branch:
                        short_branch = keyword
                        break

                branch_data = block_aggregates.setdefault(code, {})
                branch_data[short_branch] = branch_data.get(short_branch, 0.0) + (amount * sign)

            for code, branch_data in block_aggregates.items():
                memos = []
                for branch, total_amount in sorted(branch_data.items(), key=lambda item: item[1], reverse=True):
                    if total_amount > 0:
                        memos.append(f"{branch}买入{total_amount:.0f}万")
                    elif total_amount < 0:
                        memos.append(f"{branch}卖出{abs(total_amount):.0f}万")
                    else:
                        memos.append(f"{branch}净买0万")
                block_data[code] = " | ".join(memos)

        earnings_model = getattr(getattr(self, "tab_earnings", None), "model", None)
        if earnings_model is not None:
            for row in getattr(earnings_model, "row_data", []) or []:
                code = str(row.get("代码", "")).strip()
                pct = str(row.get("环比%", "")).strip()
                if code and pct and pct != "--":
                    earn_data[code] = f"{pct}%"

        lhb_model = getattr(getattr(self, "tab_lhb", None), "model", None)
        if lhb_model is not None:
            for row in getattr(lhb_model, "row_data", []) or []:
                code = str(row.get("代码", "")).strip()
                if not code:
                    continue

                raw_date = str(row.get("上榜日期", "") or "")
                if len(raw_date) == 8:
                    date_mmdd = f"{raw_date[4:6]}-{raw_date[6:8]}"
                elif "-" in raw_date:
                    parts = raw_date.split("-")
                    date_mmdd = "-".join(parts[-2:]) if len(parts) >= 2 else raw_date
                else:
                    date_mmdd = raw_date

                net = float(row.get("上榜净买额(万)", 0) or 0)
                jg = float(row.get("机构净买(万)", 0) or 0)
                fgn = float(row.get("外资净买(万)", 0) or 0)

                net_s = f"净卖:{abs(net):.0f}万" if net < 0 else f"净买:{net:.0f}万"
                jg_s = f"机构净卖:{abs(jg):.0f}万" if jg < 0 else f"机构净买:{jg:.0f}万"
                fgn_s = f"外资净卖:{abs(fgn):.0f}万" if fgn < 0 else f"外资净买:{fgn:.0f}万"

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
