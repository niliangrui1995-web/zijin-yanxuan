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
    QVBoxLayout, QHBoxLayout, QTableView,
    QHeaderView, QPushButton, QLabel, QAbstractItemView
)
from PyQt6.QtCore import Qt, QTimer
from ui.models.table_models import StockTableModel, StockItemDelegate, RtSortFilterProxyModel
from core.event_bus import event_bus
from core.logger import get_logger
from ui.tabs.base_stock_tab import BaseStockTab

log = get_logger(__name__)

class NADailyTab(BaseStockTab):
    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self._na_daily_codes = set()
        self._cap_cache_na = {}
        self.setStyleSheet("background-color: transparent;")

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
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header_layout = QHBoxLayout()
        lbl_title = QLabel("北美战报 — P9 战报标的")
        lbl_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #C9CDD4;")
        header_layout.addWidget(lbl_title)
        header_layout.addStretch()

        self.na_daily_source_label = QLabel("未加载")
        self.na_daily_source_label.setStyleSheet("font-size: 11px; color: #6B7280;")
        header_layout.addWidget(self.na_daily_source_label)

        btn_refresh = QPushButton("🔄 刷新战报")
        btn_refresh.setObjectName("ctaButton")
        btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_refresh.setFixedWidth(120)
        btn_refresh.clicked.connect(self._load_na_daily_report)
        header_layout.addWidget(btn_refresh)
        layout.addLayout(header_layout)

        columns = [
            "代码", "名称", "现价", "涨幅%", "市值", "细分板块",
            "股价弹性", "催化剂", "风控", "评级"
        ]
        self.na_daily_table = QTableView()
        
        self.model = StockTableModel(columns)
        self.proxy_model = RtSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.model)
        self.na_daily_table.setModel(self.proxy_model)
        
        self.delegate = StockItemDelegate(self.na_daily_table)
        self.na_daily_table.setItemDelegate(self.delegate)
        
        self.na_daily_table.setAlternatingRowColors(True)
        self.na_daily_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.na_daily_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.na_daily_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.na_daily_table.setSortingEnabled(True)
        self.na_daily_table.verticalHeader().setVisible(False)
        self.na_daily_table.setShowGrid(False)

        header = self.na_daily_table.horizontalHeader()
        header.setStretchLastSection(False)
        default_widths = [60, 70, 60, 60, 60, 100, 80, 120, 50, 60]
        for i, w in enumerate(default_widths):
            if i < len(columns):
                header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
                self.na_daily_table.setColumnWidth(i, w)
        self.na_daily_table.verticalHeader().setDefaultSectionSize(32)

        # 绑定防抖自动保存与恢复配置
        self.bind_header_persistence(self.na_daily_table, "header_state_na_daily_v2")

        self.na_daily_table.doubleClicked.connect(self._on_double_click)
        self.na_daily_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.na_daily_table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.na_daily_table, 1)

    def _load_na_daily_report(self):
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            ))),
            "每日战报", "每日热点输出"
        )
        pattern = os.path.join(output_dir, "**", "战报_*.md")
        files = sorted(glob.glob(pattern, recursive=True))

        if not files:
            self.na_daily_source_label.setText("❌ 未找到战报文件")
            return

        today_prefix = "战报_" + datetime.datetime.now().strftime("%Y%m%d")
        today_files = [f for f in files if os.path.basename(f).startswith(today_prefix)]
        if not today_files:
            today_files = [files[-1]]

        all_stocks = []
        seen_codes = set()
        all_recommended = {}

        for file_idx, fpath in enumerate(today_files):
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
                                "代码": t.get("code", ""),
                                "近3月": t.get("chg_3m", ""),
                                "分位": t.get("percentile_250d", ""),
                                "量能": t.get("volume", ""),
                                "弹性": t.get("elasticity", ""),
                                "催化剂": t.get("catalyst", ""),
                                "风控": t.get("risk", ""),
                            })
                    
                    for adv in data.get("today_advice", []):
                        if isinstance(adv, dict) and adv.get("code"):
                            rec_map[adv["code"]] = {
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
                    continue
                stocks = self._parse_battle_report(content)
                rec_map = self._parse_recommendations(content)

            for s in stocks:
                code = s.get("代码", "")
                if code and code not in seen_codes:
                    seen_codes.add(code)
                    all_stocks.append(s)

            for code, reason_data in rec_map.items():
                all_recommended[code] = reason_data

        fnames = [os.path.basename(f) for f in today_files]
        if len(fnames) == 1:
            self.na_daily_source_label.setText(f"📄 {fnames[0]}")
        else:
            self.na_daily_source_label.setText(f"📄 {fnames[0]} +{len(fnames)-1}份增量 ({len(all_stocks)}只)")

        self._na_daily_codes = {s["代码"] for s in all_stocks}

        final_list = []
        for stock in all_stocks:
            code = stock.get("代码", "")
            rec_data = all_recommended.get(code, {})
            priority = rec_data.get("priority", "")

            
            raw_elasticity = stock.get("弹性", "")
            clean_elasticity = re.split(r'[（(]', raw_elasticity)[0].strip() if raw_elasticity else ""

            row_data = {
                "代码": code,
                "名称": stock.get("名称", ""),
                "现价": "--",
                "涨幅%": "--",
                "细分板块": stock.get("行业", ""),
                "市值": "--",
                "股价弹性": clean_elasticity,
                "催化剂": stock.get("催化剂", ""),
                "风控": stock.get("风控", ""),
                "评级": priority
            }
            final_list.append(row_data)

        self.model.update_data(final_list)
        
        # 强制清除表头的自定义排序指标，回归战报原始注入的顺序
        self.na_daily_table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)

        if self._na_daily_codes:
            self.async_update_market_caps()

    def _load_na_daily_incremental(self):
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            ))),
            "每日战报", "每日热点输出"
        )
        pattern = os.path.join(output_dir, "**", "战报_*.md")
        files = sorted(glob.glob(pattern, recursive=True))
        if not files:
            return

        latest_file = files[-1]
        mtime = os.path.getmtime(latest_file)
        
        if getattr(self, '_last_md_mtime', 0) == mtime:
            return
            
        json_path = os.path.splitext(latest_file)[0] + ".json"
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
                            "行业": industry, "名称": t.get("name", ""), "代码": t.get("code", ""),
                            "近3月": t.get("chg_3m", ""), "分位": t.get("percentile_250d", ""),
                            "量能": t.get("volume", ""), "弹性": t.get("elasticity", ""),
                            "催化剂": t.get("catalyst", ""), "风控": t.get("risk", ""),
                        })
                for adv in data.get("today_advice", []):
                    if isinstance(adv, dict) and adv.get("code"):
                        rec_map[adv["code"]] = {
                            "priority": adv.get("priority", ""), "reason": adv.get("reason", ""),
                            "strategy": adv.get("strategy", "")
                        }
                parsed_from_json = True
            except Exception as _e:
                log.debug(f"[北美战报] 增量 JSON 解析失败，回退 MD 解析: {_e}")

        if not parsed_from_json:
            try:
                with open(latest_file, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as _e:
                log.debug(f"[北美战报] 增量 MD 文件读取失败: {_e}")
                return
            stocks = self._parse_battle_report(content)
            rec_map = self._parse_recommendations(content)

        existing_codes = set([r.get("代码", "") for r in self.model.row_data])
        new_stocks = [s for s in stocks if s.get("代码", "") not in existing_codes]
        self._last_md_mtime = mtime
        
        if not new_stocks:
            return

        filename = os.path.basename(latest_file)
        self.na_daily_source_label.setText(f"📄 {filename}（+{len(new_stocks)}新增）")

        current_list = list(self.model.row_data)
        
        for stock in new_stocks:
            code = stock.get("代码", "")
            rec_data = rec_map.get(code, {})


            raw_elasticity = stock.get("弹性", "")
            clean_elasticity = re.split(r'[（(]', raw_elasticity)[0].strip() if raw_elasticity else ""

            row_data = {
                "细分板块": stock.get("行业", ""),
                "代码": code,
                "名称": stock.get("名称", ""),
                "现价": "--",
                "涨幅%": "--",
                "市值": "--",
                "股价弹性": clean_elasticity,
                "催化剂": stock.get("催化剂", ""),
                "风控": stock.get("风控", ""),
                "评级": rec_data.get("priority", "")
            }
            current_list.append(row_data)

        self.model.update_data(current_list)

        for s in new_stocks:
            self._na_daily_codes.add(s.get("代码", ""))

        if self._na_daily_codes:
            self.async_update_market_caps()


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
