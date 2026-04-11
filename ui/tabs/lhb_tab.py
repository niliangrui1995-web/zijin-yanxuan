# -*- coding: utf-8 -*-
"""
ui/tabs/lhb_tab.py
龙虎榜 · 20 日关注池 Tab

替代旧的"单日视图"，改为滚动 20 个交易日的关注池：
- 入池条件：20 日内至少有一天同时满足 上榜净买额>0 且 机构净买>0
- 展示每只合格标的的最近一次上榜详情 + 20 日内满足条件天数
- 每天 20:00 后自动抓取当天龙虎榜数据并刷新池
- 首次使用自动回填缺失的历史交易日数据
"""

import datetime
import time

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
)
from PyQt6.QtCore import Qt, QTimer

from ui.models.table_models import StockTableModel, StockItemDelegate, RtSortFilterProxyModel
from ui.components import VCPTableView, TableStateWrapper
from core.event_bus import event_bus
from core.logger import get_logger
from core.task_manager import task_manager
from ui.tabs.base_stock_tab import BaseStockTab
from core.lhb_pool_manager import LhbPoolManager, POOL_WINDOW
from core.market_calendar import MarketCalendar

log = get_logger(__name__)


class LhbTab(BaseStockTab):
    """龙虎榜 20 日关注池 Tab"""

    def __init__(self, data_provider, parent=None):
        super().__init__(data_provider=data_provider, parent=parent)

        self.pool_manager = LhbPoolManager()
        self._backfill_in_progress = False
        # 记录今天是否已经自动抓取过，避免重复拉取
        self._today_auto_fetched = False
        # 交易日历加载重试计数器，防止网络永久断开时无限重试
        self._calendar_retry_count = 0

        self._init_ui()
        # 启动时先用缓存数据展示，再后台检查缺失天数
        self._load_and_display_pool()
        self._start_auto_scheduler()

        # 订阅中央行情站实时报价 + 大一统市值更新
        self.subscribe_global_quotes()

        # 订阅全局缓存异步加载完成事件：
        # RPS 缓存是由后台线程在 2.5 秒后注入 engine 的。
        self._rps_injected_flag = False
        event_bus.sig_cache_loaded.connect(self._on_global_cache_loaded)

    def _on_global_cache_loaded(self):
        """处理延迟的 RPS 数据加载，仅执行一次避免和自身发出的同名信号造成无限死循环"""
        if self._rps_injected_flag:
            return
        self._rps_injected_flag = True
        self._load_and_display_pool()

    @staticmethod
    def _get_engine():
        """懒加载获取 VCPEngine 单例，用于读取 F5 预算的 RPS250 缓存"""
        try:
            from vcp.engine import VCPEngine
            return VCPEngine.get_instance()
        except Exception:
            return None

    # ================================================================
    # UI 构建
    # ================================================================
    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.lbl_status = QLabel("加载中...")

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码或名称...")
        self.search_box.setFixedWidth(180)
        self.search_box.textChanged.connect(self._filter_table)

        filter_widgets = [self.search_box]

        self.btn_refresh = QPushButton("刷新关注池")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self._manual_refresh)

        action_widgets = [self.btn_refresh]
        toolbar = self.build_tab_toolbar("龙虎榜", self.lbl_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        # 表格列配置
        self.columns = [
            "代码", "名称", "现价", "涨幅%", "市值", "买点",
            "上榜次数", "最近上榜", "上榜净买额(万)",
            "机构净买(万)", "外资净买入", "换手率%", "上榜原因",
        ]
        self.table = VCPTableView(default_row_height=30)
        self.model = StockTableModel(self.columns)
        self.proxy_model = RtSortFilterProxyModel(self.table)
        self.proxy_model.setSourceModel(self.model)
        self.table.setModel(self.proxy_model)
        self.delegate = StockItemDelegate(self.table)
        self.table.setItemDelegate(self.delegate)
        self.table_state = TableStateWrapper(self.table, empty_title="暂无龙虎榜数据", loading_title="加载中...")

        # 列宽配置
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        default_widths = [52, 60, 70, 60, 65, 80, 80, 60, 90, 100, 90, 220, 70, 200]
        for i, w in enumerate(default_widths):
            if i < len(self.model.headers):
                self.table.setColumnWidth(i, w)

        # 持久化表头（v9: 外资净买入列摘要+tooltip重构版）
        self.bind_header_persistence(self.table, "lhb_header_state_v9")
        # 清除 restoreState 带来的默认排序干扰
        self.table.sortByColumn(-1, Qt.SortOrder.AscendingOrder)

        # 交互：双击查看 K 线，右键菜单
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.table_state, 1)

    # ================================================================
    # 池加载与展示
    # ================================================================
    def _load_and_display_pool(self):
        """启动时执行：用缓存计算池 → 展示 → 检查缺失天数 → 后台回填"""
        trade_dates = MarketCalendar.get_recent_trade_dates(POOL_WINDOW)
        if not trade_dates:
            self._calendar_retry_count += 1
            if self._calendar_retry_count <= 3:
                self.lbl_status.setText(f"交易日历尚未就绪，第{self._calendar_retry_count}次重试...")
                QTimer.singleShot(5000, self._load_and_display_pool)
            else:
                self.lbl_status.setText("交易日历加载失败，请点击“刷新关注池”手动重试")
            return
        self._calendar_retry_count = 0

        # 裁剪掉超出窗口的历史数据
        self.pool_manager.prune(trade_dates)

        # 先用现有缓存展示
        pool = self.pool_manager.compute_pool(data_provider=self.data_provider, engine=self._get_engine())
        if pool:
            self._display_pool(pool)

        # 检查缺失天数，有缺失就后台回填
        missing = self.pool_manager.get_missing_dates(trade_dates)
        if missing:
            self._start_backfill(missing)
        elif not pool:
            self.lbl_status.setText("暂无数据，请点击“刷新关注池”抓取")
            if hasattr(self, "table_state"):
                self.table_state.show_empty("暂无龙虎榜数据")

    def _display_pool(self, pool: list[dict]):
        """将池数据渲染到表格"""
        row_data = []
        for rec in pool:
            row_dict = dict(rec)
            # "最近上榜" 格式化：yyyyMMdd -> MM-dd 更紧凑
            raw_date = str(row_dict.get("最近上榜", ""))
            if len(raw_date) == 8:
                row_dict["最近上榜"] = f"{raw_date[4:6]}-{raw_date[6:8]}"
            row_data.append(row_dict)

        self.model.update_data(row_data)

        cached_days = len(self.pool_manager.get_cached_dates())
        self.lbl_status.setText(
            f"{POOL_WINDOW}日关注池：{len(pool)} 只标的入池（已覆盖 {cached_days} 个交易日）"
        )
        if hasattr(self, "table_state"):
            if row_data:
                self.table_state.show_table()
            else:
                self.table_state.show_empty("暂无龙虎榜数据")

        # 触发全局通知，让关注池 Tab 能扫描到龙虎榜数据
        event_bus.sig_cache_loaded.emit()

        # 统一异步刷新市值
        self.async_update_market_caps()

    # ================================================================
    # 后台回填缺失天数
    # ================================================================
    def _start_backfill(self, missing_dates: list[str]):
        """后台逐日回填缺失的龙虎榜数据"""
        if self._backfill_in_progress:
            return
        self._backfill_in_progress = True
        self.btn_refresh.setEnabled(False)

        def _safe_log_emit(level: str, message: str):
            try:
                main_win = self.window()
                if main_win and getattr(main_win, "_is_closing", False):
                    return
                event_bus.sig_system_log.emit(level, message)
            except RuntimeError:
                pass

        # 从远到近排列，先拉最老的（这样最后拉到最新的，latest record 最准）
        missing_sorted = sorted(missing_dates)
        total = len(missing_sorted)
        self.lbl_status.setText(f"正在抓取 {total} 天龙虎榜数据...")
        _safe_log_emit("info", f"[龙虎榜池] 开始抓取 {total} 个交易日数据...")

        def _bg_backfill():
            """后台线程：逐日抓取缺失天数的龙虎榜数据"""
            from ui.workers.lhb_worker import fetch_lhb_pool_for_date

            results: dict[str, list[dict]] = {}
            for i, date_str in enumerate(missing_sorted):
                try:
                    records = fetch_lhb_pool_for_date(date_str)
                    results[date_str] = records
                    # 逐日进度广播到日志 Tab（在后台线程中 emit 是线程安全的）
                    _safe_log_emit(
                        "info",
                        f"[龙虎榜池] ({i+1}/{total}) {date_str} 抓取完成，{len(records)} 条记录"
                    )
                except Exception as e:
                    log.warning(f"[龙虎榜池] 回填 {date_str} 失败: {e}")
                    _safe_log_emit("warn", f"[龙虎榜池] {date_str} 抓取失败: {e}")
                    results[date_str] = []

                # 限速保护：避免短时间内大量请求被东方财富限流
                if i < total - 1:
                    time.sleep(0.8)
            return results

        def _on_backfill_done(results: dict):
            self._backfill_in_progress = False
            self.btn_refresh.setEnabled(True)

            if not results:
                self.lbl_status.setText("抓取失败，请稍后重试")
                event_bus.sig_system_log.emit("error", "[龙虎榜池] 全部交易日抓取失败")
                return

            # 写入池引擎
            for date_str, records in results.items():
                self.pool_manager.add_day(date_str, records)
            self.pool_manager.save()

            # 重新计算池并展示
            pool = self.pool_manager.compute_pool(data_provider=self.data_provider, engine=self._get_engine())
            self._display_pool(pool)
            event_bus.sig_system_log.emit(
                "info",
                f"[龙虎榜池] ✅ 抓取完成: {len(results)} 天数据, {len(pool)} 只标的入池"
            )

        def _on_backfill_error(error_message: str):
            self._backfill_in_progress = False
            self.btn_refresh.setEnabled(True)
            self.lbl_status.setText(f"抓取异常: {error_message}")
            event_bus.sig_system_log.emit("error", f"[龙虎榜池] 抓取任务异常: {error_message}")

        task_manager.run_in_background(
            _bg_backfill,
            on_success=_on_backfill_done,
            on_error=_on_backfill_error,
            task_id="lhb_pool_backfill",
        )

    # ================================================================
    # 手动刷新
    # ================================================================
    def _manual_refresh(self):
        """手动刷新：清空缓存，重新获取全新 20 个交易日的龙虎榜数据"""
        if self._backfill_in_progress:
            from ui.components.toast_widget import show_toast
            show_toast("正在抓取中，请稍候...", "warning", self)
            return

        trade_dates = MarketCalendar.get_recent_trade_dates(POOL_WINDOW)
        if not trade_dates:
            from ui.components.toast_widget import show_toast
            show_toast("交易日历尚未就绪", "warning", self)
            return

        # 清空全部缓存，强制全量重拉
        self.pool_manager.clear_all()
        self._start_backfill(trade_dates)

    # ================================================================
    # 每日 20:00 定时自动抓取
    # ================================================================
    def _start_auto_scheduler(self):
        """启动定时检查器：每 5 分钟检查是否到了自动抓取时间

        为什么用 5 分钟轮询而不是精确定时：
        精确定时需要计算到 20:00 的剩余秒数，且存在系统休眠/恢复后
        定时器失效的问题。5 分钟轮询简单可靠，CPU 开销可忽略。
        """
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._check_auto_fetch)
        # 每 5 分钟检查一次
        self._auto_timer.start(5 * 60 * 1000)
        # 启动后也立即检查一次
        QTimer.singleShot(10_000, self._check_auto_fetch)

    def _check_auto_fetch(self):
        """检查是否满足自动抓取条件：
        ① 当前时间 >= 20:00
        ② 今天是交易日
        ③ 今天的数据尚未抓取
        """
        if self._backfill_in_progress:
            return

        now = datetime.datetime.now()
        today_str = now.strftime("%Y%m%d")

        # 条件①：20:00 后
        if now.hour < 20:
            return

        # 条件③：今天已抓取则跳过
        if self._today_auto_fetched and today_str == self.pool_manager.last_auto_fetch_date:
            return

        # 条件②：今天是交易日
        if not MarketCalendar.is_trade_day(now.date()):
            return

        # 已经缓存了今天的数据也跳过
        if today_str in self.pool_manager.get_cached_dates():
            self._today_auto_fetched = True
            return

        event_bus.sig_system_log.emit("info", f"[龙虎榜池] 触发每日20:00自动抓取: {today_str}")
        self._fetch_single_day(today_str)

    def _fetch_single_day(self, date_str: str):
        """抓取单天数据并刷新池"""
        self.btn_refresh.setEnabled(False)
        self.lbl_status.setText(f"正在抓取 {date_str} 龙虎榜数据...")

        def _bg_fetch():
            from ui.workers.lhb_worker import fetch_lhb_pool_for_date
            return fetch_lhb_pool_for_date(date_str)

        def _on_done(records):
            self.btn_refresh.setEnabled(True)

            # 写入池引擎
            self.pool_manager.add_day(date_str, records if records else [])
            self.pool_manager.last_auto_fetch_date = date_str
            self._today_auto_fetched = True

            # 裁剪并重算
            trade_dates = MarketCalendar.get_recent_trade_dates(POOL_WINDOW)
            if trade_dates:
                self.pool_manager.prune(trade_dates)
            self.pool_manager.save()

            pool = self.pool_manager.compute_pool(data_provider=self.data_provider, engine=self._get_engine())
            self._display_pool(pool)
            event_bus.sig_system_log.emit(
                "info",
                f"[龙虎榜池] ✅ {date_str} 自动抓取完成, {len(records) if records else 0} 条记录, 池中 {len(pool)} 只标的"
            )

        def _on_error(error_message: str):
            self.btn_refresh.setEnabled(True)
            self.lbl_status.setText(f"抓取 {date_str} 失败: {error_message}")
            event_bus.sig_system_log.emit("error", f"[龙虎榜池] 自动抓取失败: {error_message}")

        task_manager.run_in_background(
            _bg_fetch,
            on_success=_on_done,
            on_error=_on_error,
            task_id="lhb_pool_daily_fetch",
        )

    # ================================================================
    # 搜索过滤
    # ================================================================
    def _filter_table(self):
        search_text = self.search_box.text().strip().lower()
        self.proxy_model.setFilterText(search_text)

    # ================================================================
    # 交互事件
    # ================================================================
    def _on_double_click(self, index):
        if not index.isValid():
            return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data):
            return

        code = self.model.row_data[row].get("代码", "")

        # 提取当前表格顺序以传递给 K 线窗口
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
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data):
            return

        code = self.model.row_data[row].get("代码", "")
        name = self.model.row_data[row].get("名称", "")
        row_data = self.model.row_data[row]
        if not code:
            return

        from ui.components.stock_context_menu import build_stock_context_menu
        build_stock_context_menu(self, code, name, vcp_data=row_data)
