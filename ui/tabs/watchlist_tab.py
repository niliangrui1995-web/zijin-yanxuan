# -*- coding: utf-8 -*-
# ui/tabs/watchlist_tab.py
# 关注池独立组件 — 从 WatchlistMixin 解耦重构为完全自治的 QWidget
import os
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QAbstractItemView, QHeaderView, QLabel, QLineEdit, QPushButton, QVBoxLayout

from core.event_bus import event_bus
from core.exceptions import CacheIOError, DataFormatError
from core.json_cache import load_json_file
from core.logger import get_logger
from core.task_manager import task_manager
from ui.components import TableStateWrapper, VCPTableView
from ui.components.toast_widget import show_toast
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.tabs.base_stock_tab import BaseStockTab
from ui.viewmodels.watchlist_vm import watchlist_vm
from vcp.constants import RPS_CACHE_FILE

log = get_logger(__name__)


class WatchlistTab(BaseStockTab):
    """
    关注池 独立 Tab 组件 (Controller + View)
    全权负责关注池的增删查改、实时报价、AI诊断结果展示。
    通过 EventBus 与外部通信，不直接依赖 MainWindowQT。
    """

    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)
        self._watchlist_last_update = ""
        self._init_ui()

        # 订阅全局报价与大一统市值更新机制
        self.subscribe_global_quotes()

        # 挂载全局事件总线
        event_bus.sig_watchlist_changed.connect(self._on_watchlist_changed)
        event_bus.sig_app_closing.connect(self._on_app_closing)

        # v4: 使用精准专用信道
        event_bus.sig_cache_bootstrap_ready.connect(self._on_cache_or_earnings_updated)
        event_bus.sig_cache_reload_completed.connect(self._on_cache_or_earnings_updated)
        event_bus.sig_earnings_updated.connect(self._on_cache_or_earnings_updated)
        event_bus.sig_na_daily_updated.connect(self._on_na_daily_updated)
        event_bus.sig_block_trade_updated.connect(self._on_block_trade_updated)
        event_bus.sig_lhb_pool_updated.connect(self._on_cache_or_earnings_updated)
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
        self.sp_search.setAccessibleName("关注池筛选")
        self.sp_search.setAccessibleDescription("按代码或名称筛选当前关注池股票")
        self.sp_search.setMinimumWidth(150)
        self.sp_search.setMaximumWidth(240)
        self.sp_search.textChanged.connect(self._filter_table)

        filter_widgets = [self.sp_search]

        self.add_stock_input = QLineEdit()
        self.add_stock_input.setPlaceholderText("输入A股代码，如 600519")
        self.add_stock_input.setAccessibleName("添加自选股输入框")
        self.add_stock_input.setAccessibleDescription("输入六位 A 股代码后可加入关注池")
        self.add_stock_input.setClearButtonEnabled(True)
        self.add_stock_input.setMinimumWidth(160)
        self.add_stock_input.setMaximumWidth(260)
        self.add_stock_input.returnPressed.connect(self._add_custom_stock)

        btn_add_stock = QPushButton("添加自选股")
        btn_add_stock.clicked.connect(self._add_custom_stock)

        btn_reset = QPushButton("解除列表排序")
        btn_reset.clicked.connect(self._reset_view)

        action_widgets = [self.add_stock_input, btn_add_stock, btn_reset]
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
            "代码", "名称", "来源", "现价", "涨幅%", "市值",
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
        sp_weights = [0.55, 0.78, 1.15, 0.72, 0.72, 0.92, 0.9, 1.2, 1.45, 1.45, 1.2, 1.75]
        header = self.table_sp.horizontalHeader()
        header.setStretchLastSection(True)
        for col_idx, w in enumerate(sp_weights):
            header.setSectionResizeMode(col_idx, QHeaderView.ResizeMode.Interactive)
            self.table_sp.setColumnWidth(col_idx, int(w * 80))
        # 绑定防抖自动保存与恢复配置（列结构变更后沿用新 key，避免旧状态错位）
        restored_sort = self.bind_header_persistence(self.table_sp, "header_state_watchlist_v8")
        if not restored_sort:
            self.table_sp.sortByColumn(-1, Qt.SortOrder.AscendingOrder)

        # 双击 → 查看K线图（通过 EventBus 广播）
        self.table_sp.doubleClicked.connect(self._on_double_click)

        # 右键菜单
        self.table_sp.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_sp.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_state)

    @staticmethod
    def _now_hhmm() -> str:
        return datetime.now().strftime("%H:%M")

    def _touch_watchlist_update(self, stamp: str | None = None) -> bool:
        text = str(stamp or "").strip() or self._now_hhmm()
        if not text or text == self._watchlist_last_update:
            return False
        self._watchlist_last_update = text
        return True

    # ================================================================
    # 数据加载
    # ================================================================
    def _load_special_data(self):
        """加载关注池数据：统一走 ViewModel/SQLite。"""
        data_dict = watchlist_vm.get_watchlist_data()

        all_codes = list(data_dict.keys())

        # 渲染表格
        self._render_table(all_codes, data_dict, {})

        # 主动触发 VCP 指标刷新（细分板块/RPS/业绩异动等）
        # 为什么不依赖 CACHE_LOADED 事件：因为存在时序竞态——
        # 缓存可能在 3.5s 延迟前就加载完了，那时 model.row_data 还是空的
        # 引入 _request_vcp_calc 防抖 500ms 重复合并
        self._request_vcp_calc()

    def _render_table(self, all_codes, data_dict, old_pool):
        """渲染关注池表格"""
        self._touch_watchlist_update()

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
            source_context = dict(info_old)
            source_context.update(info_new)
            source_tags = watchlist_vm.derive_source_tags(
                source_context,
                existing_tags=source_context.get("来源标签"),
            )
            source_text = watchlist_vm.format_source_tags(source_tags)
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
                "来源": source_text,
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
                "来源标签": source_tags,
                "_zongguben": live_entry.get("_zongguben", 0)
            }
            final_list.append(row_data)

        self.model.update_data(final_list)
        self.refresh_table_quotes_and_market_caps(quote_task_id="watchlist_quotes")
        self._update_status_summary()

    def _update_status_summary(self):
        rows = list(getattr(self.model, "row_data", []) or [])
        total = len(rows)
        visible = self.proxy_model.rowCount()
        search_text = self.sp_search.text().strip()
        if total == 0:
            self.lbl_sp_status.setText(
                self.format_workspace_status(
                    "关注池为空",
                    result="0只",
                    freshness=self._watchlist_last_update or "待加载",
                    current_filter=search_text or "全部",
                    next_step="输入代码或从其他页面加入",
                )
            )
            if hasattr(self, "table_state"):
                self.table_state.show_empty("暂无关注池数据")
            return

        source_tags = []
        for row in rows:
            for tag in watchlist_vm.normalize_source_tags(row.get("来源标签") or row.get("来源", "")):
                if tag not in source_tags:
                    source_tags.append(tag)

        extra_segments = []
        if source_tags:
            extra_segments.append(f"来源 {watchlist_vm.format_source_tags(source_tags)}")

        self.lbl_sp_status.setText(
            self.format_workspace_status(
                "关注池已就绪",
                result=f"{visible}/{total}只",
                freshness=self._watchlist_last_update or "待刷新",
                current_filter=search_text or "全部",
                next_step="",
                extra_segments=extra_segments,
            )
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
        """双击行 → 打开 K 线图。"""
        if not index.isValid():
            return
        source_index = self.proxy_model.mapToSource(index)
        row = source_index.row()
        if row >= len(self.model.row_data):
            return
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

    def _get_a_share_name_map(self) -> dict:
        cached = getattr(self, "_a_share_name_map", None)
        if isinstance(cached, dict) and cached:
            return cached

        provider = self.data_provider
        code_map = {}
        if provider is not None:
            code_map = getattr(provider, "code2name", {}) or {}
            if not code_map and hasattr(provider, "get_all_codes"):
                try:
                    code_map = provider.get_all_codes() or {}
                    provider.code2name = code_map
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
                    log.error(f"[关注池] 读取A股代码表失败: {e}")
                    code_map = {}

        normalized_map = {}
        for code, name in code_map.items():
            normalized_code = self._normalize_quote_code(code).zfill(6)
            if len(normalized_code) == 6 and normalized_code.isdigit():
                normalized_map[normalized_code] = str(name or normalized_code).strip()

        self._a_share_name_map = normalized_map
        return self._a_share_name_map

    def _add_custom_stock(self):
        raw_code = self.add_stock_input.text() if hasattr(self, "add_stock_input") else ""
        code = self._normalize_quote_code(raw_code).zfill(6)
        if len(code) != 6 or not code.isdigit():
            show_toast("请输入 6 位 A 股代码", "warning", self)
            if hasattr(self, "add_stock_input"):
                self.add_stock_input.setFocus()
                self.add_stock_input.selectAll()
            return

        name_map = self._get_a_share_name_map()
        name = str(name_map.get(code, "") or "").strip()
        if not name:
            show_toast(f"{code} 不在当前 A 股股票列表中", "warning", self)
            if hasattr(self, "add_stock_input"):
                self.add_stock_input.setFocus()
                self.add_stock_input.selectAll()
            return

        if watchlist_vm.is_in_watchlist(code):
            show_toast(f"{name} 已在关注池", "info", self)
            if hasattr(self, "add_stock_input"):
                self.add_stock_input.clear()
                self.add_stock_input.setFocus()
            return

        added = watchlist_vm.add_stock(
            code,
            name,
            {"代码": code, "名称": name, "code": code, "name": name},
            source_tags=["手动"],
        )
        if added:
            self.lbl_sp_status.setText(
                self.format_workspace_status(
                    "关注池已更新",
                    result=f"{len(getattr(self.model, 'row_data', []) or [])}只",
                    freshness=self._watchlist_last_update or self._now_hhmm(),
                    current_filter=self.sp_search.text().strip() or "全部",
                    next_step=f"等待 {name} 的行情与来源补齐",
                )
            )
            show_toast(f"{name} 已加入关注池，正在刷新行情与附加列", "success", self)
            if hasattr(self, "add_stock_input"):
                self.add_stock_input.clear()
                self.add_stock_input.setFocus()
        else:
            show_toast(f"{name} 已在关注池", "info", self)
            if hasattr(self, "add_stock_input"):
                self.add_stock_input.clear()
                self.add_stock_input.setFocus()

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
        workspace = getattr(self.window(), "_workspace", None)
        if workspace is None:
            return {}, {}, {}, {}, {}, None

        try:
            return workspace.collect_watchlist_radar_data()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
            log.warning(f"[关注池] 提取工作区雷达数据异常: {e}")
            return {}, {}, {}, {}, {}, None

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
            log.debug(f"[关注池] 开始计算 {len(codes_with_rows)} 只标的附加指标")

            # 1. 尝试从引擎获取RPS
            rps_bundle = radar_data_tuple[5] if radar_data_tuple and len(radar_data_tuple) > 5 else None

            if not rps_bundle:
                try:
                    if os.path.exists(RPS_CACHE_FILE):
                        rps_bundle = load_json_file(RPS_CACHE_FILE)
                except (CacheIOError, DataFormatError) as e:
                    log.debug(f"[关注池] RPS 缓存读取失败，改用空值: {e}")

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
                    has_rps120 = rps120_series is not None and code in rps120_series
                    has_rps250 = rps250_series is not None and code in rps250_series
                    rps120_val = float(rps120_series.get(code, 0)) if has_rps120 else 0
                    rps250_val = float(rps250_series.get(code, 0)) if has_rps250 else 0

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
                except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as _e:
                    log.debug(f"[关注池] {code} RPS指标计算异常: {_e}")
                    continue

            if results:
                event_bus.sig_vcp_watchlist_ready.emit(results)
                log.info(f"[关注池] {len(results)} 只标的附加指标已就绪")

        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
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

            source_tags = watchlist_vm.derive_source_tags(
                row_dict,
                existing_tags=row_dict.get("来源标签"),
            )
            row_dict["来源标签"] = source_tags
            row_dict["来源"] = watchlist_vm.format_source_tags(source_tags)

            # trigger row update
            self.model.dataChanged.emit(
                self.model.index(row_idx, 0),
                self.model.index(row_idx, len(self.model._headers)-1)
            )

        self._persist_watchlist_metrics(results)
        self._touch_watchlist_update()
        self._update_status_summary()

    def _persist_watchlist_metrics(self, results: dict):
        if not results:
            return

        patch_payload: dict[str, dict] = {}
        for code, data in results.items():
            entry_patch = {
                "RPS强度": str(data.get('rps', '')),
            }
            if data.get('subsector'):
                entry_patch["细分板块"] = str(data['subsector'])

            if data.get('na_catalyst'):
                entry_patch["美股日报"] = str(data['na_catalyst'])
            if data.get('block_trade'):
                entry_patch["大宗交易"] = str(data['block_trade'])
            if data.get('earnings'):
                entry_patch["业绩异动"] = str(data['earnings'])
            if data.get('lhb'):
                new_lhb = data['lhb']
                if isinstance(new_lhb, dict):
                    new_date = new_lhb.get("date", "")
                    new_text = new_lhb.get("text", "")
                    entry_patch["龙虎榜"] = str(new_text)
                    entry_patch["龙虎榜日期"] = str(new_date)

            patch_payload[str(code)] = entry_patch

        watchlist_vm.bulk_patch_entries(patch_payload, remove_keys=["催化剂", "热点板块"])


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
                entry["来源标签"] = watchlist_vm.derive_source_tags(
                    row_dict,
                    existing_tags=row_dict.get("来源标签"),
                )
                entry.pop("催化剂", None)
                entry.pop("热点板块", None)

                # 按视觉顺序保存
                new_cache[code] = entry

            # 防护网：如果用户有关闭前正在搜索过滤，没显示在表面的隐身票，原样追回防止丢票
            for code, entry in current_cache.items():
                if code not in new_cache:
                    new_cache[code] = entry

            if new_cache:
                watchlist_vm.replace_watchlist_data(new_cache)
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
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

    def prime_startup_state(self):
        """工作区联动：启动后主动补一次关注池行情与附加指标。"""
        if not self.model or not getattr(self.model, "row_data", None):
            return
        self.refresh_table_quotes_and_market_caps(quote_task_id="smart_startup_watchlist")
        self._request_vcp_calc(delay_ms=0)

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
        self._update_status_summary()

    def _on_rt_quotes_direct(self, quotes: dict):
        super()._on_rt_quotes_direct(quotes)
        if not self.isVisible() or not quotes:
            return
        if self._touch_watchlist_update():
            self._update_status_summary()

