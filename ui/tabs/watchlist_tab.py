# -*- coding: utf-8 -*-
# ui/tabs/watchlist_tab.py
# 关注池独立组件 — 从 WatchlistMixin 解耦重构为完全自治的 QWidget
import os
import pickle
import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QHeaderView, QPushButton, QLineEdit, QAbstractItemView,
    QFileDialog
)
from ui.components.toast_widget import show_toast
from PyQt6.QtCore import Qt, QTimer

from ui.viewmodels.watchlist_vm import watchlist_vm
from ui.models.table_models import StockTableModel, StockItemDelegate, RtSortFilterProxyModel
from ui.components import VCPTableView, TableStateWrapper
from core.event_bus import event_bus
from core.logger import get_logger
from core.task_manager import task_manager
from ui.tabs.base_stock_tab import BaseStockTab

log = get_logger(__name__)


class WatchlistTab(BaseStockTab):
    """
    关注池 独立 Tab 组件 (Controller + View)
    全权负责关注池的增删查改、实时报价、AI诊断结果展示。
    通过 EventBus 与外部通信，不直接依赖 MainWindowQT。
    """

    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)

        self._init_ui()

        # 订阅全局报价与大一统市值更新机制
        self.subscribe_global_quotes()

        # 挂载全局事件总线
        event_bus.sig_watchlist_changed.connect(self._on_watchlist_changed)
        event_bus.sig_app_closing.connect(self._on_app_closing)
        
        # v4: 使用精准专用信道
        event_bus.sig_cache_loaded.connect(self._on_cache_or_earnings_updated)
        event_bus.sig_earnings_updated.connect(self._on_cache_or_earnings_updated)
        event_bus.sig_na_daily_updated.connect(self._on_na_daily_updated)
        event_bus.sig_block_trade_updated.connect(self._on_block_trade_updated)
        event_bus.sig_vcp_watchlist_ready.connect(self._on_vcp_watchlist_ready)

        # 先立即回填一次，避免启动期 UI 忙时定时器延后导致“关注池长期空白”。
        self._load_special_data()
        # 再做一次延迟回填，兜住启动后缓存/名称映射后到位的场景。
        QTimer.singleShot(3500, self._load_special_data)

    # ================================================================
    # UI 构建
    # ================================================================
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.lbl_sp_status = QLabel("")

        self.sp_search = QLineEdit()
        self.sp_search.setPlaceholderText("筛选关注池...")
        self.sp_search.setFixedWidth(150)
        self.sp_search.textChanged.connect(self._filter_table)

        filter_widgets = [self.sp_search]

        btn_reset = QPushButton("解除列表排序")
        btn_reset.clicked.connect(self._reset_view)

        btn_export_sp = QPushButton("📄 导出")
        btn_export_sp.clicked.connect(self._export_to_excel)

        action_widgets = [btn_reset, btn_export_sp]
        toolbar = self.build_tab_toolbar("关注池", self.lbl_sp_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        # 表格控件
        self.table_sp = VCPTableView(default_row_height=30)
        
        # 拖拽排序设置 (只有在默认排序状态下才可用)
        self.table_sp.setDragEnabled(True)
        self.table_sp.setAcceptDrops(True)
        self.table_sp.setDropIndicatorShown(True)
        self.table_sp.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.table_sp.setDragDropOverwriteMode(False)
        
        # 绑定 Model 与 Delegate
        headers = [
            "代码", "名称", "现价", "涨幅%", "市值",
            "RPS强度", "细分板块", "催化剂", "业绩异动", "大宗交易", "龙虎榜"
        ]
        self.model = StockTableModel(headers)
        self.proxy_model = RtSortFilterProxyModel(self.table_sp)
        self.proxy_model.setSourceModel(self.model)
        self.table_sp.setModel(self.proxy_model)
        
        self.delegate = StockItemDelegate(self.table_sp)
        self.table_sp.setItemDelegate(self.delegate)
        self.table_state = TableStateWrapper(self.table_sp, empty_title="暂无关注池数据", loading_title="加载中...")

        # 接收模型发出的手动排序完成信号
        self.model.sig_rows_reordered.connect(self._on_rows_reordered)

        # 自适应列宽
        sp_weights = [0.55, 0.75, 0.65, 1.4, 0.75, 0.9, 0.8, 1.4, 1.8, 1.2, 1.8, 2.8]
        header = self.table_sp.horizontalHeader()
        header.setStretchLastSection(True)
        for col_idx, w in enumerate(sp_weights):
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            self.table_sp.setColumnWidth(col_idx, int(w * 80))
        # 绑定防抖自动保存与恢复配置（restoreState 会连带把上次的排序列也恢复了）
        # 列结构变更（移除“时间”列），升级配置 key，避免旧列状态错位恢复
        self.bind_header_persistence(self.table_sp, "header_state_watchlist_v8")
        
        # 【修复】强制抹掉任何因为 header.restoreState 还原出来的自动排序状态
        # 因为在关闭时，我们已经把当前的各种（哪怕是点击表头排出来的）视觉顺序定死并按此顺序拍扁存入硬盘了
        # 所以重启后，应当直接默认展示物理顺序，而不受过去排序标记的干扰
        self.table_sp.sortByColumn(-1, Qt.SortOrder.AscendingOrder)

        # 双击 → 查看K线图（通过 EventBus 广播）
        self.table_sp.doubleClicked.connect(self._on_double_click)

        # 右键菜单
        self.table_sp.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_sp.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_state)

    # ================================================================
    # 数据加载
    # ================================================================
    def _load_special_data(self):
        """加载关注池数据：新UI JSON + 老UI pkl 兼容 + AI诊断缓存"""
        # 1. 统一由 ViewModel 管理的数据
        data_dict = watchlist_vm.get_watchlist_data()

        # 2. 老UI pkl 兼容迁移
        old_pool = {}
        if not data_dict:
            from vcp.constants import SPECIAL_POOL_DATA_CACHE
            pool_pkl = SPECIAL_POOL_DATA_CACHE
            if os.path.exists(pool_pkl):
                try:
                    with open(pool_pkl, 'rb') as f:
                        raw = pickle.load(f)
                    old_pool = raw.get('data', {}) if isinstance(raw, dict) else {}
                except Exception as e:
                    log.error(f"[关注池] 老UI缓存读取异常: {e}")


        all_codes = list(data_dict.keys())
        for code in old_pool:
            if code not in data_dict:
                all_codes.append(code)

        # 渲染表格
        self._render_table(all_codes, data_dict, old_pool)

        # 抛弃本地缓存回填，直接触发基类的大一统市值刷新方案
        if all_codes:
            self.async_update_market_caps()

        # 主动触发 VCP 指标刷新（细分板块/RPS/业绩异动等）
        # 为什么不依赖 CACHE_LOADED 事件：因为存在时序竞态——
        # 缓存可能在 3.5s 延迟前就加载完了，那时 model.row_data 还是空的
        # 引入 _request_vcp_calc 防抖 500ms 重复合并
        self._request_vcp_calc()

    def _render_table(self, all_codes, data_dict, old_pool):
        """渲染关注池表格"""
        
        # 提取当前表格中活跃的实时行情和市值，避免重绘时发生闪退或变成 '--'
        live_data_map = {}
        if hasattr(self, 'model') and getattr(self.model, 'row_data', None):
            for r in self.model.row_data:
                c = r.get("代码")
                if c:
                    live_data_map[c] = {
                        "现价": r.get("现价", "--"),
                        "涨幅%": r.get("涨幅%", "--"),
                        "市值": r.get("市值", "--"),
                        "_zongguben": r.get("_zongguben", 0)
                    }

        final_list = []
        for row_idx, code in enumerate(all_codes):
            info_new = data_dict.get(code, {})
            info_old = old_pool.get(code, {})
            # 优先从新老池数据中提取名称，最后使用全局映射
            name = info_new.get("名称") or info_old.get("名称")
            if not name or name == str(code):
                name = getattr(self.data_provider, 'code2name', {}).get(code, code)

            live_entry = live_data_map.get(code, {})
            # 优先保留活跃数据
            cur_price = live_entry.get("现价") if live_entry.get("现价", "--") != "--" else info_new.get("现价", '--')
            pct_str = live_entry.get("涨幅%") if live_entry.get("涨幅%", "--") != "--" else info_new.get("涨幅%", '--')
            cap = live_entry.get("市值") if live_entry.get("市值", "--") != "--" else info_new.get("市值", '--')

            rps = info_new.get("RPS强度", '--')
            subsector = (
                info_new.get("细分板块")
                or info_old.get("细分板块")
                or info_new.get("subsector", "")
                or ""
            )
            cap_display = cap if cap and cap != '--' else ''

            row_data = {
                "代码": code,
                "名称": name,
                "现价": str(cur_price),
                "涨幅%": str(pct_str),
                "市值": str(cap_display),
                "RPS强度": str(rps),
                "细分板块": str(subsector),
                "催化剂": (
                    info_new.get("美股日报")
                    or info_new.get("催化剂")
                    or info_old.get("美股日报", "")
                    or info_old.get("催化剂", "")
                    or ""
                ),
                "大宗交易": info_new.get("大宗交易", ""),
                "业绩异动": info_new.get("业绩异动", ""),
                "龙虎榜": info_new.get("龙虎榜", ""),
                "龙虎榜日期": info_new.get("龙虎榜日期", ""),
                "_zongguben": live_entry.get("_zongguben", 0)
            }
            final_list.append(row_data)

        self.model.update_data(final_list)
        self._update_status_summary()

    def _update_status_summary(self):
        rows = list(getattr(self.model, "row_data", []) or [])
        total = len(rows)
        if total == 0:
            self.lbl_sp_status.setText("暂无标的")
            if hasattr(self, "table_state"):
                self.table_state.show_empty("暂无关注池数据")
            return

        def _filled(row, key):
            val = str(row.get(key, "") or "").strip()
            return val not in ("", "--")

        rps_count = sum(_filled(r, "RPS强度") for r in rows)
        catalyst_count = sum(_filled(r, "催化剂") for r in rows)
        earnings_count = sum(_filled(r, "业绩异动") for r in rows)
        block_count = sum(_filled(r, "大宗交易") for r in rows)
        lhb_count = sum(_filled(r, "龙虎榜") for r in rows)

        self.lbl_sp_status.setText(
            f"{total}只 | RPS {rps_count} | 催化 {catalyst_count} | 业绩 {earnings_count} | 大宗 {block_count} | 龙虎 {lhb_count}"
        )
        if hasattr(self, "table_state"):
            self.table_state.show_table()

    def _on_rows_reordered(self, new_codes_list):
        """当用户在表格手动拖拽重排后，更新VM字典保存并重新渲染"""
        # 1. 如果表格处于按某列排序模式(如按涨幅排)，禁止拖拽覆盖
        if self.proxy_model.sortColumn() != -1:
            from ui.components.toast_widget import show_toast
            show_toast("当前正处于条件排序状态，拖拽无效，请点击右上角【还原默认视图】后再拖拽！", "warning", self)
            self._load_special_data() # 撤销刚刚拖拽引发的界面错乱，滚回原状
            return
            
        # 2. 调用 VM 写入磁盘
        watchlist_vm.reorder(new_codes_list)
        
        # 3. 再重新拉取一次保持严格同步
        self._load_special_data()


    # ================================================================
    # 交互事件
    # ================================================================
    def _on_double_click(self, index):
        """双击行 → 备注列弹编辑框(#11)，其他列跳 K 线图"""
        if not index.isValid():
            return
        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        col = source_index.column()
        if row >= len(self.model.row_data):
            return

        # #11: 如果双击的是备注列(第9列)，弹出编辑框
        notes_col_idx = self.model.headers.index("备注") if "备注" in self.model.headers else -1
        if col == notes_col_idx:
            code = self.model.row_data[row].get("代码", "")
            old_note = self.model.row_data[row].get("备注", "")
            from PyQt6.QtWidgets import QInputDialog
            new_note, ok = QInputDialog.getText(
                self, "编辑备注",
                f"请输入 {code} 的备注:",
                text=old_note
            )
            if ok:
                # 更新内存
                self.model.row_data[row]["备注"] = new_note
                # 触发表格刷新
                idx = self.model.index(row, notes_col_idx)
                self.model.dataChanged.emit(idx, idx)
                # 持久化到 WatchlistVM JSON
                vm_data = watchlist_vm.get_watchlist_data()
                if code in vm_data:
                    vm_data[code]["备注"] = new_note
                    watchlist_vm._cache[code] = vm_data[code]
                    watchlist_vm._save_data()
            return

        # 非备注列 → K 线图
        code = self.model.row_data[row].get("代码")
        if code:
            watchlist_data = watchlist_vm.get_watchlist_data()
            code_list = []
            for r in range(self.proxy_model.rowCount()):
                s_idx = self.proxy_model.mapToSource(self.proxy_model.index(r, 0))
                if s_idx.row() < len(self.model.row_data):
                    rd = dict(self.model.row_data[s_idx.row()] or {})
                    code_key = str(rd.get("代码", "")).strip()
                    merged = {"代码": code_key, "名称": rd.get("名称", "")}
                    persisted = watchlist_data.get(code_key, {})
                    if isinstance(persisted, dict):
                        for k, v in persisted.items():
                            if v not in (None, "", [], {}):
                                merged[k] = v
                    for k, v in rd.items():
                        if v not in (None, "", [], {}):
                            merged[k] = v
                    code_list.append(merged)
            
            current_idx = 0
            for i, c in enumerate(code_list):
                if c['代码'] == code:
                    current_idx = i
                    break
                    
            event_bus.sig_show_kline_with_list.emit(code, code_list, current_idx)

    def _show_context_menu(self, pos):
        """关注池右键菜单 — 委托给统一菜单工厂 (#2)"""
        index = self.table_sp.indexAt(pos)
        if not index.isValid():
            return

        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        if row >= len(self.model.row_data):
            return
            
        code = self.model.row_data[row].get("代码", "")
        name = self.model.row_data[row].get("名称", "")
        if not code or not name:
            return

        from ui.components.stock_context_menu import build_stock_context_menu
        build_stock_context_menu(self, code, name)



    def _export_to_excel(self):
        """导出关注池表格到 Excel"""
        if self.model.rowCount() == 0:
            show_toast("关注池为空，无法导出", "warning", self)
            return
        import pandas as pd
        path, _ = QFileDialog.getSaveFileName(
            self, "导出关注池",
            f"关注池_{datetime.date.today().strftime('%Y%m%d')}.xlsx",
            "Excel Files (*.xlsx)"
        )
        if not path:
            return
        try:
            headers = self.model.headers
            rows = []
            for row_dict in self.model.row_data:
                row = []
                for header in headers:
                    row.append(str(row_dict.get(header, "")))
                rows.append(row)
            df = pd.DataFrame(rows, columns=headers)
            df.to_excel(path, index=False, engine='openpyxl')
            event_bus.sig_system_log.emit(
                "info", f"✅ 已导出 {len(rows)} 条关注池记录至 {path}"
            )
            show_toast("自选股导出成功!", "success", self)
        except Exception as e:
            show_toast(f"导出失败: {str(e)}", "error", self)

    def _reset_view(self):
        """取消强制排序：仅重置表格排序状态，不影响用户自定义的列宽"""
        # 还原默认排序列，使得可随意拖拽
        self.table_sp.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        
        show_toast("已解除列表排序，您可以自由拖拽个股顺序了", "success", self.window(), duration=2500)

    # ================================================================
    # EventBus 事件监听及同步更新
    # ================================================================
    def _gather_radar_data(self):
        """主线程快速提取 UI 数据，供后台线程使用（避免跨线程访问UI崩溃）"""
        na_data, na_subsector_data, block_data, earn_data, lhb_data = {}, {}, {}, {}, {}
        rps_bundle = None
        try:
            main_win = self.window()
            if main_win:
                if hasattr(main_win, 'engine'):
                    rps_bundle = main_win.engine.get_precomputed_rps()
                    
                if hasattr(main_win, 'tab_na_daily') and hasattr(main_win.tab_na_daily, 'model'):
                    for r in main_win.tab_na_daily.model.row_data:
                        c = str(r.get("代码", ""))
                        if c:
                            na_data[c] = str(r.get("催化剂", "") or r.get("💥催化剂", ""))
                            na_subsector_data[c] = str(r.get("细分板块", "") or "")
                        
                if hasattr(main_win, 'tab_foreign_block') and hasattr(main_win.tab_foreign_block, 'model'):
                    # 用于聚合单只股票下各主力席位的买卖净额: code -> { short_branch: net_amount_wan }
                    block_aggregates = {}
                    from ui.tabs.foreign_block_trade_tab import FOREIGN_KEYWORDS
                    for r in main_win.tab_foreign_block.model.row_data:
                        c = str(r.get("代码", ""))
                        if c:
                            detail = str(r.get("交易详情", ""))
                            buy = str(r.get("买方营业部", ""))
                            sell = str(r.get("卖方营业部", ""))
                            amt = str(r.get("成交金额(万元)", "0"))
                            
                            try:
                                amt_val = float(amt) if amt and amt != "--" else 0.0
                            except:
                                amt_val = 0.0
                                
                            branch = ""
                            sign = 1.0
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
                            for kw in FOREIGN_KEYWORDS:
                                if kw in branch:
                                    short_branch = kw
                                    break
                                    
                            if c not in block_aggregates:
                                block_aggregates[c] = {}
                            block_aggregates[c][short_branch] = block_aggregates[c].get(short_branch, 0.0) + (amt_val * sign)
                            
                    # 将聚合的数据重组为显示串
                    for c, branch_data in block_aggregates.items():
                        memos = []
                        # 降序排列，净买金额大的排前面，净卖排后面
                        sorted_branches = sorted(branch_data.items(), key=lambda x: x[1], reverse=True)
                        for br, total_amt in sorted_branches:
                            if total_amt > 0:
                                memos.append(f"{br}买入{total_amt:.0f}万")
                            elif total_amt < 0:
                                memos.append(f"{br}卖出{abs(total_amt):.0f}万")
                            else:
                                memos.append(f"{br}净买0万")
                        block_data[c] = " | ".join(memos)
                            
                if hasattr(main_win, 'tab_earnings') and hasattr(main_win.tab_earnings, 'model'):
                    for r in main_win.tab_earnings.model.row_data:
                        c = str(r.get("代码", ""))
                        pct = str(r.get("环比%", ""))
                        if c and pct and pct != "--": earn_data[c] = f"{pct}%"
                        
                if hasattr(main_win, 'tab_lhb') and hasattr(main_win.tab_lhb, 'model'):
                    for r in main_win.tab_lhb.model.row_data:
                        c = str(r.get("代码", ""))
                        if c:
                            # 日期格式兼容：lhb_worker输出的是 "20260410" 纯数字格式
                            raw_date = str(r.get("上榜日期", ""))
                            if len(raw_date) == 8:
                                date_mmdd = f"{raw_date[4:6]}-{raw_date[6:8]}"
                            elif '-' in raw_date:
                                parts = raw_date.split('-')
                                date_mmdd = "-".join(parts[-2:]) if len(parts) >= 2 else raw_date
                            else:
                                date_mmdd = raw_date
                            
                            net = float(r.get("上榜净买额(万)", 0))
                            jg = float(r.get("机构净买(万)", 0))
                            fgn = float(r.get("外资净买(万)", 0))
                            
                            net_s = f"净卖:{abs(net):.0f}万" if net < 0 else f"净买:{net:.0f}万"
                            jg_s = f"机构净卖:{abs(jg):.0f}万" if jg < 0 else f"机构净买:{jg:.0f}万"
                            fgn_s = f"外资净卖:{abs(fgn):.0f}万" if fgn < 0 else f"外资净买:{fgn:.0f}万"
                            
                            lhb_data[c] = {
                                "text": f"{date_mmdd} | {net_s} | {jg_s} | {fgn_s}",
                                "date": str(r.get("上榜日期", ""))
                            }
                            
        except Exception as e:
            log.warning(f"[关注池] 提取主界面数据异常: {e}")
        return na_data, na_subsector_data, block_data, earn_data, lhb_data, rps_bundle

    def _on_watchlist_changed(self, action: str, _code: str):
        """外部请求关注池变更时，防抖 300ms 后再重新加载（防止快速增删导致任务堆积）"""
        if not hasattr(self, '_debounce_timer'):
            self._debounce_timer = QTimer(self)
            self._debounce_timer.setSingleShot(True)
            self._debounce_timer.timeout.connect(self._do_watchlist_reload)
        # 每次新信号进来都重置计时器，只有最后一次 300ms 后才真正触发
        self._debounce_timer.start(80 if action == "add" else 300)

    def _do_watchlist_reload(self):
        """防抖后的实际重载逻辑"""
        self._load_special_data()

    def _refresh_vcp_indicators(self, codes_with_rows, radar_data_tuple=None):
        """后台线程：计算关注池标的的 RPS 和跨 Tab 附加字段。"""
        try:
            import pickle
            import os

            log.debug(f"[关注池] 开始计算 {len(codes_with_rows)} 只标的附加指标")

            # 1. 尝试从引擎获取RPS
            rps_bundle = radar_data_tuple[5] if radar_data_tuple and len(radar_data_tuple) > 5 else None

            if not rps_bundle:
                cache_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    'data', 'Cache'
                )
                rps_path = os.path.join(cache_dir, 'vcp_rps_precomputed.pkl')
                if os.path.exists(rps_path):
                    with open(rps_path, 'rb') as f:
                        rps_bundle = pickle.load(f)

            rps120_series = rps_bundle.get('rps120') if rps_bundle else None
            rps250_series = rps_bundle.get('rps250') if rps_bundle else None

            # --- 动态扫盘：三大挂载战场的雷达数据提取 ---
            na_data, na_subsector_data, block_data, earn_data, lhb_data = (
                radar_data_tuple[0],
                radar_data_tuple[1],
                radar_data_tuple[2],
                radar_data_tuple[3],
                radar_data_tuple[4]
            ) if radar_data_tuple else ({}, {}, {}, {}, {})

            # 剥离不再必要的重复计算市值逻辑 (由大一统机制负责)
            results = {}  # 修复局部变量未初始化的 bug
            
            for _, code in codes_with_rows:
                try:
                    rps120_val = float(rps120_series.get(code, 0)) if rps120_series is not None and code in rps120_series else 0
                    rps250_val = float(rps250_series.get(code, 0)) if rps250_series is not None and code in rps250_series else 0
                    
                    rps_display = '--'
                    if rps250_val > 0:
                        rps_display = f"{rps250_val:.0f}"
                        if rps120_val > 0:
                            rps_display += f"/{rps120_val:.0f}"
                            
                    results[code] = {
                        'rps': rps_display,
                        'subsector': na_subsector_data.get(code, ''),
                        'na_catalyst': na_data.get(code, ''),
                        'block_trade': block_data.get(code, ''),
                        'earnings': earn_data.get(code, ''),
                        'lhb': lhb_data.get(code, '')
                    }
                except Exception as _e:
                    log.debug(f"[关注池] {code} RPS指标计算异常: {_e}")
                    continue
            
            if results:
                event_bus.sig_vcp_watchlist_ready.emit(results)
                log.info(f"[关注池] {len(results)} 只标的附加指标已就绪")

        except Exception as e:
            log.error(f"[关注池] 附加指标批量计算异常: {e}")

    def _apply_vcp_indicators_ui(self, results: dict):
        """主线程：将 VCP 指标更新到 Model（按股票代码匹配，不再按行号，防止排序/拖拽后错位）"""
        if not results: return
        
        # 构建 code -> row_idx 的当前映射（实时安全）
        code_to_row = {}
        for idx, row_dict in enumerate(self.model.row_data):
            c = row_dict.get('代码')
            if c:
                code_to_row[c] = idx
        
        for code, data in results.items():
            row_idx = code_to_row.get(code, -1)
            if row_idx < 0 or row_idx >= len(self.model.row_data): continue
            
            row_dict = self.model.row_data[row_idx]
            row_dict['RPS强度'] = data.get('rps', '--')
            if data.get('subsector'):
                row_dict['细分板块'] = data['subsector']
            
            # 三大阵营的数据注入 (如果原本有数据但不为空，我们不覆盖；如果本次扫到了，坚决覆盖)
            if data.get('na_catalyst'):
                row_dict['催化剂'] = data['na_catalyst']
            if data.get('block_trade'): row_dict['大宗交易'] = data['block_trade']
            if data.get('earnings'): row_dict['业绩异动'] = data['earnings']
            if data.get('lhb'): 
                new_lhb = data['lhb']
                if isinstance(new_lhb, dict):
                    new_date = new_lhb.get("date", "")
                    new_text = new_lhb.get("text", "")
                    # 【逻辑变更】：根据龙虎榜表信息无条件刷新，不考虑历史日期锁定
                    row_dict["龙虎榜"] = new_text
                    row_dict["龙虎榜日期"] = new_date
                        
            # trigger row update
            self.model.dataChanged.emit(
                self.model.index(row_idx, 0),
                self.model.index(row_idx, len(self.model._headers)-1)
            )

        self._persist_watchlist_metrics(results)
        self._update_status_summary()

    def _persist_watchlist_metrics(self, results: dict):
        if not results:
            return

        current_cache = watchlist_vm.get_watchlist_data()
        if not current_cache:
            return

        cache_dirty = False
        for code, data in results.items():
            entry = current_cache.get(code)
            if not entry:
                continue

            entry["RPS强度"] = str(data.get('rps', entry.get("RPS强度", "")))
            if data.get('subsector'):
                entry["细分板块"] = str(data['subsector'])
            else:
                entry.setdefault("细分板块", entry.get("细分板块", ""))

            if data.get('na_catalyst'):
                entry["美股日报"] = str(data['na_catalyst'])
            if data.get('block_trade'):
                entry["大宗交易"] = str(data['block_trade'])
            if data.get('earnings'):
                entry["业绩异动"] = str(data['earnings'])
            if data.get('lhb'):
                new_lhb = data['lhb']
                if isinstance(new_lhb, dict):
                    new_date = new_lhb.get("date", "")
                    new_text = new_lhb.get("text", "")
                    entry["龙虎榜"] = str(new_text)
                    entry["龙虎榜日期"] = str(new_date)

            entry.pop("催化剂", None)
            entry.pop("热点板块", None)
            current_cache[code] = entry
            cache_dirty = True

        if cache_dirty:
            watchlist_vm._cache = current_cache
            watchlist_vm._save_data()


    def _on_app_closing(self):
        """应用关闭前保存缓存"""
        if self.model.row_data:
            self._save_special_cache_from_table()

    def _save_special_cache_from_table(self):
        """应用关闭前将表格最新数据更新回 ViewModel，同时保存最终的视觉排序效果"""
        try:
            current_cache = watchlist_vm.get_watchlist_data()
            if not current_cache:
                return

            new_cache = {}
            # 从 proxy_model 里拿，确保记录的是屏幕上最终排序后的顺序
            row_count = self.proxy_model.rowCount()
            for r in range(row_count):
                source_idx = self.proxy_model.mapToSource(self.proxy_model.index(r, 0))
                if not source_idx.isValid():
                    continue
                row_dict = self.model.row_data[source_idx.row()]
                code = str(row_dict.get("代码", ""))
                if not code or code not in current_cache:
                    continue
                
                # 更新最新的结构化指标到 ViewModel
                entry = current_cache[code]
                entry["RPS强度"] = str(row_dict.get("RPS强度", ""))
                entry["AI结论"] = str(row_dict.get("AI结论", ""))
                entry["细分板块"] = str(row_dict.get("细分板块", ""))
                entry["美股日报"] = str(row_dict.get("催化剂", ""))
                entry["大宗交易"] = str(row_dict.get("大宗交易", ""))
                entry["业绩异动"] = str(row_dict.get("业绩异动", ""))
                entry["龙虎榜日期"] = str(row_dict.get("龙虎榜日期", ""))
                entry.pop("催化剂", None)
                entry.pop("热点板块", None)
                
                # 按视觉顺序保存
                new_cache[code] = entry

            # 防护网：如果用户有关闭前正在搜索过滤，没显示在表面的隐身票，原样追回防止丢票
            for code, entry in current_cache.items():
                if code not in new_cache:
                    new_cache[code] = entry

            if new_cache:
                watchlist_vm._cache = new_cache
                watchlist_vm._save_data()
        except Exception as e:
            log.error(f"[关注池] 同步缓存到 ViewModel 失败: {e}")

    def _on_cache_or_earnings_updated(self):
        """统一事件消费： F5缓存完成 or 业绩数据更新"""
        # F5 缓存完成后作为第二次刷新机会（此时 earnings/大宗交易/美股等可能已有数据）
        # 合并启动后期的重复触发
        self._request_vcp_calc()

    def _on_na_daily_updated(self):
        """美股日报最近5份内容刷新后，同步关注池的细分板块/催化剂缓存。"""
        self._request_vcp_calc()

    def _on_block_trade_updated(self):
        """大宗交易数据刷新后，同步关注池的大宗交易缓存。"""
        self._request_vcp_calc()

    def _on_vcp_watchlist_ready(self, payload: object):
        if payload:
            log.debug(f"[关注池] 写入 {len(payload)} 条附加指标")
            self._apply_vcp_indicators_ui(payload)

    def _request_vcp_calc(self, delay_ms: int = 500):
        """请求计算 VCP 附加指标，带有防抖功能，防止启动时多次触发"""
        if not hasattr(self, '_vcp_calc_timer'):
            self._vcp_calc_timer = QTimer(self)
            self._vcp_calc_timer.setSingleShot(True)
            self._vcp_calc_timer.timeout.connect(self._do_vcp_calc)
        self._vcp_calc_timer.start(max(0, int(delay_ms)))

    def _do_vcp_calc(self):
        """实际计算"""
        if self.model and self.model.row_data:
            codes_with_rows = [(idx, str(r.get("代码")))
                               for idx, r in enumerate(self.model.row_data) if r.get("代码")]
            if codes_with_rows:
                radar_data_tuple = self._gather_radar_data()
                task_manager.run_in_background(
                    self._refresh_vcp_indicators, codes_with_rows, radar_data_tuple,
                    on_error=lambda e: log.error(f"[关注池] 附加指标后台计算异常: {e}"),
                    task_id="watchlist_vcp_refresh"
                )

    # ================================================================
    # 工具方法
    # ================================================================
    def _filter_table(self, text):
        """搜索过滤：支持代码、名称、拼音首字母"""
        self.proxy_model.setFilterText(text)

