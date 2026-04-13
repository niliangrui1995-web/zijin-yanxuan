# -*- coding: utf-8 -*-
"""
ui/tabs/na_daily_tab.py
北美战报 独立 Tab 组件 (MVC 版本重构)
"""
import os
import re
import glob
import datetime

from PyQt6.QtWidgets import (
    QVBoxLayout,
    QHeaderView, QPushButton, QLabel, QLineEdit
)
from PyQt6.QtCore import Qt, QTimer
from ui.models.table_models import StockTableModel, StockItemDelegate, RtSortFilterProxyModel
from core.event_bus import event_bus
from core.logger import get_logger
from ui.tabs.base_stock_tab import BaseStockTab
from ui.components import VCPTableView, TableStateWrapper

log = get_logger(__name__)

class NADailyTab(BaseStockTab):
    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self._na_daily_codes = set()
        self._last_report_signature = ()

        self._init_ui()

        # 开机延迟拉取/展现
        QTimer.singleShot(3500, self._load_na_daily_report)

        # 订阅中央广播站报价及开启大一统市值更新
        self.subscribe_global_quotes()

        # 统一巡逻定时器：每30秒检查一次（合并了增量检查 + 定时全量刷新）
        self._patrol_timer = QTimer(self)
        self._patrol_timer.timeout.connect(self._patrol_tick)
        self._patrol_timer.start(30 * 1000)
        self._na_daily_fired_today = set()
        self._initial_quotes_done = False

    def _patrol_tick(self):
        """统一巡逻：盘中增量检查 + 定时全量刷新 + 首次市值拉取"""
        from core.market_calendar import MarketCalendar
        is_active = MarketCalendar.is_market_active()

        # 1. 盘中：每30秒检查战报文件是否有增量
        if is_active:
            self._load_na_daily_incremental()

        # 2. 定时全量刷新（交易日 9:25 自动拉一次完整战报）
        now = datetime.datetime.now()
        if now.weekday() < 5:
            today_str = now.strftime('%Y%m%d')
            key = f"{today_str}_0925"
            if key not in self._na_daily_fired_today:
                if 0 <= (now.hour * 60 + now.minute) - (9 * 60 + 25) <= 1:
                    self._na_daily_fired_today.add(key)
                    self._load_na_daily_report()

        # 3. 首次启动：拉一次市值（盘后也能展示收盘数据）
        if not self._initial_quotes_done and self._na_daily_codes:
            self.async_update_market_caps()
            self._initial_quotes_done = True

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.na_daily_source_label = QLabel("未加载")
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码或名称...")
        self.search_box.setFixedWidth(160)
        self.search_box.textChanged.connect(self._on_search_text_changed)
        btn_refresh = QPushButton("刷新战报")
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.clicked.connect(self._load_na_daily_report)

        filter_widgets = [self.search_box]
        action_widgets = [btn_refresh]
        toolbar = self.build_tab_toolbar("北美战报", self.na_daily_source_label, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        columns = [
            "代码", "名称", "现价", "涨幅%", "市值", "日报时间", "细分板块",
            "股价弹性", "催化剂", "风控", "评级"
        ]
        self.na_daily_table = VCPTableView(default_row_height=30)
        self.table_state = TableStateWrapper(self.na_daily_table, empty_title="暂无战报数据", loading_title="加载中...")
        
        self.model = StockTableModel(columns)
        self.model.set_plain_style_headers(["日报时间"])
        self.proxy_model = RtSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.model)
        self.na_daily_table.setModel(self.proxy_model)
        
        self.delegate = StockItemDelegate(self.na_daily_table)
        self.na_daily_table.setItemDelegate(self.delegate)

        header = self.na_daily_table.horizontalHeader()
        header.setStretchLastSection(False)
        default_widths = [52, 60, 70, 60, 60, 70, 78, 100, 80, 120, 50, 60]
        for i, w in enumerate(default_widths):
            if i < len(self.model.headers):
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                self.na_daily_table.setColumnWidth(i, w)

        # 绑定防抖自动保存与恢复配置
        self.bind_header_persistence(self.na_daily_table, "header_state_na_daily_v4")

        self.na_daily_table.doubleClicked.connect(self._on_double_click)
        self.na_daily_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.na_daily_table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_state, 1)

    def _on_search_text_changed(self, text):
        self.proxy_model.setFilterText(text)

    def _set_report_status(self, primary: str, *segments: str):
        self.na_daily_source_label.setText(self.format_status_summary(primary, *segments))

    def _get_na_daily_output_dir(self):
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            ))),
            "每日战报", "每日热点输出"
        )

    def _parse_report_identity(self, fpath: str):
        basename = os.path.basename(fpath)
        match = re.search(r'战报_(\d{8})(\d{0,6})', basename)
        if match:
            report_date = match.group(1)
            time_part = match.group(2) or ""
            if time_part:
                padded_time = (time_part + "000000")[:6]
                report_dt = datetime.datetime.strptime(report_date + padded_time, "%Y%m%d%H%M%S")
            else:
                report_dt = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
                report_date = report_dt.strftime("%Y%m%d")
        else:
            report_dt = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
            report_date = report_dt.strftime("%Y%m%d")

        report_ts = int(report_dt.strftime("%Y%m%d%H%M%S"))
        return report_date, report_ts, basename

    def _list_recent_report_files(self, limit: int = 5):
        pattern = os.path.join(self._get_na_daily_output_dir(), "**", "战报_*.md")
        files = glob.glob(pattern, recursive=True)
        if not files:
            return []
        files.sort(key=lambda path: (self._parse_report_identity(path)[1], path))
        return files[-limit:]

    def _load_report_payload(self, fpath: str):
        json_path = os.path.splitext(fpath)[0] + ".json"
        stocks = []
        rec_map = {}
        parsed_from_json = False

        if os.path.exists(json_path):
            try:
                import json
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                for track in data.get("sniper_tables", []):
                    raw_industry = track.get("track_name", "未知赛道")
                    industry = re.split(r'[（(]', raw_industry)[0].strip()
                    industry = re.sub(r'^赛道[A-Za-z0-9]+[：:\s]*', '', industry)

                    for t in track.get("targets", []):
                        stocks.append({
                            "行业": industry,
                            "名称": t.get("name", ""),
                            "代码": str(t.get("code", "") or "").strip(),
                            "近3月": t.get("chg_3m", ""),
                            "分位": t.get("percentile_250d", ""),
                            "量能": t.get("volume", ""),
                            "弹性": t.get("elasticity", ""),
                            "催化剂": t.get("catalyst", ""),
                            "风控": t.get("risk", ""),
                        })

                for adv in data.get("today_advice", []):
                    if isinstance(adv, dict) and adv.get("code"):
                        rec_map[str(adv["code"]).strip()] = {
                            "priority": adv.get("priority", ""),
                            "reason": adv.get("reason", ""),
                            "strategy": adv.get("strategy", "")
                        }
                parsed_from_json = True
            except Exception as e:
                log.warning(f"解析 JSON 失败: {e}")

        if not parsed_from_json:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as _e:
                log.debug(f"[北美战报] 读取战报文件失败: {_e}")
                return [], {}
            stocks = self._parse_battle_report(content)
            rec_map = self._parse_recommendations(content)

        return stocks, rec_map

    def _build_na_daily_rows(self):
        report_files = self._list_recent_report_files(limit=5)
        if not report_files:
            return [], [], ()

        latest_rows = {}
        latest_recommendations = {}

        for fpath in report_files:
            report_date, report_ts, _ = self._parse_report_identity(fpath)
            stocks, rec_map = self._load_report_payload(fpath)

            for row_rank, stock in enumerate(stocks):
                code = str(stock.get("代码", "") or "").strip()
                if not code:
                    continue

                raw_elasticity = stock.get("弹性", "")
                clean_elasticity = re.split(r'[（(]', raw_elasticity)[0].strip() if raw_elasticity else ""
                clean_elasticity = "".join(c for c in clean_elasticity if c.isalnum() or '\u4e00' <= c <= '\u9fa5')

                # 提取红黄绿圈圈，丢弃所有文字内容
                raw_risk = str(stock.get("风控", ""))
                clean_risk = "".join([c for c in raw_risk if c in "🟢🔴🟡"])

                # 同一代码以时间最新的一次命中为准，后面的新战报覆盖旧内容。
                latest_rows[code] = {
                    "代码": code,
                    "名称": stock.get("名称", ""),
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "日报时间": report_date,
                    "细分板块": stock.get("行业", ""),
                    "股价弹性": clean_elasticity,
                    "催化剂": stock.get("催化剂", ""),
                    "风控": clean_risk,
                    "评级": "",
                    "_report_ts": report_ts,
                    "_report_row_rank": row_rank,
                }

            for code, reason_data in rec_map.items():
                latest_recommendations[str(code).strip()] = reason_data

        final_list = []
        for code, row_data in latest_rows.items():
            rec_data = latest_recommendations.get(code, {})
            row_data["评级"] = rec_data.get("priority", "")
            final_list.append(row_data)

        final_list.sort(key=lambda row: (
            -int(row.get("日报时间", "0") or 0),
            -int(row.get("_report_ts", 0) or 0),
            int(row.get("_report_row_rank", 0) or 0),
            str(row.get("代码", "") or "")
        ))

        report_signature = tuple(
            f"{os.path.basename(path)}:{int(os.path.getmtime(path))}"
            for path in report_files
        )
        return final_list, report_files, report_signature

    def _apply_na_daily_rows(self, final_list, report_files, report_signature):
        self._last_report_signature = report_signature

        if not report_files:
            self._set_report_status("未找到战报文件", "最近窗口为空")
            self.model.update_data([])
            self._na_daily_codes = set()
            event_bus.sig_na_daily_updated.emit()
            if hasattr(self, "table_state"):
                self.table_state.show_empty("暂无战报数据")
            return

        newest_file = max(report_files, key=lambda path: self._parse_report_identity(path)[1])
        newest_name = os.path.basename(newest_file)
        if len(report_files) == 1:
            self._set_report_status(
                newest_name,
                self._status_metric("合并 ", len(final_list), "只"),
            )
        else:
            self._set_report_status(
                "最近5份战报",
                newest_name,
                self._status_metric("覆盖 ", len(report_files), "份"),
                self._status_metric("合并 ", len(final_list), "只"),
            )

        self._na_daily_codes = {row.get("代码", "") for row in final_list if row.get("代码")}
        self.model.update_data(final_list)
        if hasattr(self, "table_state"):
            if final_list:
                self.table_state.show_table()
            else:
                self.table_state.show_empty("暂无战报数据")

        try:
            report_col = self.model.headers.index("日报时间")
            self.na_daily_table.sortByColumn(report_col, Qt.SortOrder.DescendingOrder)
        except ValueError:
            pass

        if self._na_daily_codes:
            self.async_update_market_caps()

        event_bus.sig_na_daily_updated.emit()

    def _load_na_daily_report(self):
        if hasattr(self, "table_state"):
            self.table_state.show_loading("正在加载战报...", "请稍候")
        final_list, report_files, report_signature = self._build_na_daily_rows()
        self._apply_na_daily_rows(final_list, report_files, report_signature)

    def _load_na_daily_incremental(self):
        report_files = self._list_recent_report_files(limit=5)
        if not report_files:
            return

        report_signature = tuple(
            f"{os.path.basename(path)}:{int(os.path.getmtime(path))}"
            for path in report_files
        )
        if self._last_report_signature == report_signature:
            return

        final_list, report_files, report_signature = self._build_na_daily_rows()
        self._apply_na_daily_rows(final_list, report_files, report_signature)


    def _on_double_click(self, index):
        if not index.isValid(): return
        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        
        code = self.model.row_data[row].get("代码")
        if code:
            code_list = []
            for r in range(self.proxy_model.rowCount()):
                s_idx = self.proxy_model.mapToSource(self.proxy_model.index(r, 0))
                if s_idx.row() < len(self.model.row_data):
                    rd = self.model.row_data[s_idx.row()]
                    code_list.append({'代码': rd.get("代码", ""), '名称': rd.get("名称", "")})
            
            current_idx = 0
            for i, c in enumerate(code_list):
                if c['代码'] == code:
                    current_idx = i
                    break
                    
            event_bus.sig_show_kline_with_list.emit(code, code_list, current_idx)

    def _show_context_menu(self, pos):
        index = self.na_daily_table.indexAt(pos)
        if not index.isValid(): return

        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        if row >= len(self.model.row_data): return
            
        code = self.model.row_data[row].get("代码", "")
        name = self.model.row_data[row].get("名称", "")
        row_data = self.model.row_data[row]
        if not code or not name: return

        from ui.components.stock_context_menu import build_stock_context_menu
        build_stock_context_menu(self, code, name, vcp_data=row_data)

    def _parse_battle_report(self, content: str) -> list:
        stocks = []
        section_match = re.search(r'##\s*二、标的狙击表(.*?)(?=##\s*三、|$)', content, re.DOTALL)
        if not section_match: return stocks

        section = section_match.group(1)
        industry_pattern = re.compile(r'###\s+(?:🔴\s*|🟢\s*|🟡\s*)?(.+?)[\n\r]')
        industry_matches = list(industry_pattern.finditer(section))

        for i, ind_match in enumerate(industry_matches):
            raw_industry = ind_match.group(1).strip()
            industry = re.split(r'[（(]', raw_industry)[0].strip()
            industry = re.sub(r'^赛道[A-Za-z0-9]+[：:\s]*', '', industry)
            start = ind_match.end()
            end = industry_matches[i + 1].start() if i + 1 < len(industry_matches) else len(section)
            block = section[start:end]
            table_rows = block.strip().split('\n')
            
            header_cells = []
            info_rows = []
            for row_text in table_rows:
                cells = [c.strip() for c in row_text.split('|')]
                if len(cells) >= 3 and cells[0] == '' and cells[-1] == '':
                    cells = cells[1:-1]
                elif not cells: continue
                
                if ('代码' in cells) and ('标的' in cells or '名称' in cells):
                    header_cells = cells
                elif header_cells:
                    if all('---' in c or not c for c in cells): continue
                    info_rows.append(cells)
                    
            if not header_cells: continue
                
            def get_col_idx(title_keywords):
                for ind, h in enumerate(header_cells):
                    for kw in title_keywords:
                        if kw in h: return ind
                return -1

            idx_name = get_col_idx(['标的', '名称'])
            idx_code = get_col_idx(['代码'])
            idx_chg_3m = get_col_idx(['近3月'])
            idx_pct_250d = get_col_idx(['分位'])
            idx_elasticity = get_col_idx(['弹性'])
            idx_rs = get_col_idx(['RS'])
            idx_weekly = get_col_idx(['周线'])
            idx_catalyst = get_col_idx(['催化剂'])
            idx_risk = get_col_idx(['风控'])

            for row_data in info_rows:
                if len(row_data) < 3 or idx_code == -1: continue
                def get_val(idx): return row_data[idx].replace('**', '').strip() if 0 <= idx < len(row_data) else ""

                name = get_val(idx_name)
                code = get_val(idx_code)
                if not re.match(r'^\d{6}$', code): continue
                    
                stocks.append({
                    "行业": industry, "名称": name, "代码": code,
                    "近3月": get_val(idx_chg_3m), "分位": get_val(idx_pct_250d),
                    "弹性": get_val(idx_elasticity), "RS强度": get_val(idx_rs),
                    "周线趋势": get_val(idx_weekly), "催化剂": get_val(idx_catalyst),
                    "风控": get_val(idx_risk),
                })
        return stocks

    def _parse_recommendations(self, content: str) -> dict:
        result = {}
        section_match = re.search(r'##\s*四、今日操作建议(.*?)(?=##\s*[一二三四五六七八九十]|$)', content, re.DOTALL)
        if not section_match: return result
        section = section_match.group(1)
        in_rec_table = False
        found_separator = False

        for line in section.split('\n'):
            stripped = line.strip()
            if not in_rec_table and stripped.startswith('|') and '优先级' in stripped:
                in_rec_table = True
                found_separator = False
                continue
            if in_rec_table:
                if '---' in stripped and stripped.startswith('|'):
                    found_separator = True
                    continue
                if not stripped.startswith('|') or not stripped: break
                if found_separator:
                    code_match = re.search(r'(\d{6})', stripped)
                    if code_match:
                        raw_cells = [c.strip() for c in stripped.split('|')]
                        if len(raw_cells) >= 3 and raw_cells[0] == '' and raw_cells[-1] == '':
                            cells = raw_cells[1:-1]
                        else:
                            cells = [c for c in raw_cells if c]
                            
                        if len(cells) >= 3:
                            code = code_match.group(1)
                            priority = cells[0].replace('**', '').strip()
                            reason = cells[3].replace('**', '').strip() if len(cells) > 3 else ""
                            strategy = cells[4].replace('**', '').strip() if len(cells) > 4 else ""
                            result[code] = {"priority": priority, "reason": reason, "strategy": strategy}
        return result
