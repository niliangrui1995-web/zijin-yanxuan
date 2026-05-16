# -*- coding: utf-8 -*-
import datetime
import json
import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QCheckBox, QHeaderView, QLabel, QLineEdit, QPushButton, QVBoxLayout

from app.services.asian_market_service import filter_asian_tickers, find_asian_track
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signals
from core.logger import get_logger
from ui.components import TableStateWrapper, VCPTableView
from ui.components.thread_shutdown import request_thread_shutdown
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.tabs.asian_market_meta import (
    format_market_display,
    get_ch_names_mapping,
    get_market_status,
    get_role_mapping,
)
from ui.tabs.asian_market_runtime import (
    call_worker_method as asian_call_worker_method,
)
from ui.tabs.asian_market_runtime import (
    check_auto_cache as asian_check_auto_cache,
)
from ui.tabs.asian_market_runtime import (
    continue_auto_cache_sync as asian_continue_auto_cache_sync,
)
from ui.tabs.asian_market_runtime import (
    log_asian_health as asian_log_asian_health,
)
from ui.tabs.asian_market_runtime import (
    on_asian_klines_ready as asian_on_asian_klines_ready,
)
from ui.tabs.asian_market_runtime import (
    on_auto_cache_finished as asian_on_auto_cache_finished,
)
from ui.tabs.asian_market_runtime import (
    on_minute_tick as asian_on_minute_tick,
)
from ui.tabs.asian_market_runtime import (
    refresh_market_status_rows as asian_refresh_market_status_rows,
)
from ui.tabs.asian_market_runtime import (
    runtime_state_text as asian_runtime_state_text,
)
from ui.tabs.asian_market_runtime import (
    worker_pause_for_cache_sync as asian_worker_pause_for_cache_sync,
)
from ui.tabs.asian_market_runtime import (
    worker_resume_auto_refresh as asian_worker_resume_auto_refresh,
)
from ui.tabs.asian_market_runtime import (
    worker_trigger_refresh as asian_worker_trigger_refresh,
)
from ui.tabs.asian_market_workers import (
    GLOBAL_ASIAN_RT_CACHE,
    JSON_CACHE,
    RT_JSON_CACHE,
    AsianMarketWorker,
    is_asian_quote_refresh_time,
    is_cf_proxy_enabled,
    set_cf_proxy_enabled,
)
from ui.tabs.base_stock_tab import BaseStockTab

log = get_logger(__name__)


def _safe_float(value) -> float:
    try:
        return float(str(value).replace("%", ""))
    except (TypeError, ValueError):
        return 0.0


def _round_pct(value) -> float:
    return round(_safe_float(value), 2)


def _resolve_cached_rt_previous_close(info: dict, data_points: list[dict]) -> float | None:
    if not data_points:
        return None

    rt_date = str((info or {}).get("date") or "").strip()
    history_date = str(data_points[-1].get("date") or "").strip()
    history_close = _safe_float(data_points[-1].get("close"))
    history_prev = _safe_float(data_points[-2].get("close")) if len(data_points) >= 2 else 0.0

    if rt_date and history_date and rt_date > history_date and history_close > 0:
        return history_close
    if history_prev > 0:
        return history_prev
    if history_close > 0:
        return history_close
    return None


def _normalize_cached_rt_entry(info: dict, data_points: list[dict]) -> dict:
    normalized = dict(info or {})
    source = str(normalized.get("source") or "").strip().lower()
    if source != "yfinance":
        return normalized

    close_value = _safe_float(normalized.get("close"))
    prev_close = _resolve_cached_rt_previous_close(normalized, data_points)
    if prev_close is None or prev_close <= 0:
        return normalized

    normalized["previous_close"] = prev_close
    if close_value > 0:
        normalized["pct"] = _round_pct(((close_value / prev_close) - 1.0) * 100.0)
    return normalized


class AsianMarketTab(BaseStockTab):
    """亚洲寡头行情面板"""
    def __init__(self, data_provider=None, parent=None):
        super().__init__(data_provider, parent)
        self._asian_runtime_state = "running"
        self._is_fetching_cache = False
        self._pending_auto_cache_sync = False
        self._cache_sync_wait_deadline = None
        self._load_cache_in_progress = False
        self._load_cache_pending = False
        self._last_asian_success_at = None
        self._last_asian_error = ""
        self._status_primary = "系统初始化..."
        self._status_segments = ()
        self._status_freshness = ""
        self._status_next_step = ""
        self._last_health_log_at = 0.0
        self._last_health_signature = None
        self._runtime_started = False
        self.cache_thread = None
        self._init_ui()

        # 1. 冷开机瞬间加载本地 JSON (asian_klines_latest.json)
        self._load_local_cache()

        # 2. 启动后台 Worker, 进行 60 秒常态轮询
        codes = [item['代码'] for item in self.row_data]
        self.worker = AsianMarketWorker(codes)
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.result_ready.connect(self._on_rt_update)
        if self._is_quote_refresh_open():
            self._asian_runtime_state = "running"
            self._worker_resume_auto_refresh()
        else:
            self._asian_runtime_state = "paused_for_cache_sync"
            self._worker_pause_for_cache_sync()
        # 后台轮询延后到页面首次显示，避免冷启动阶段抢占首屏。

        # 3. 监听全局数据更新事件 (如被 deferred_load 静默更新完毕)
        event_bus.sig_asian_klines_ready.connect(self._on_asian_klines_ready)

        # 4. 自动缓存校验器：每分钟检查本地缓存是否需要更新
        self.auto_cache_timer = QTimer(self)
        self.auto_cache_timer.timeout.connect(self._on_minute_tick)

    def _ensure_runtime_started(self):
        if self._runtime_started:
            return
        self._runtime_started = True
        if hasattr(self, "worker") and self.worker is not None and not self.worker.isRunning():
            QTimer.singleShot(1000, self.worker.start)
        self.auto_cache_timer.start(60000)
        QTimer.singleShot(2000, self._on_minute_tick)

    def _set_runtime_state(self, state: str):
        self._asian_runtime_state = state

    def _runtime_state_text(self) -> str:
        return asian_runtime_state_text(self._asian_runtime_state)

    def _call_worker_method(self, method_name: str):
        return asian_call_worker_method(self, method_name)

    def _get_tracked_codes(self) -> list[str]:
        worker_codes = [
            str(code).strip()
            for code in getattr(getattr(self, "worker", None), "codes", []) or []
            if str(code).strip()
        ]
        if worker_codes:
            return list(dict.fromkeys(worker_codes))

        row_codes = [
            str(row.get("代码", "")).strip()
            for row in getattr(self, "row_data", []) or []
            if str(row.get("代码", "")).strip()
        ]
        return list(dict.fromkeys(row_codes))

    def _is_quote_refresh_open(self) -> bool:
        return is_asian_quote_refresh_time(self._get_tracked_codes())

    def _worker_resume_auto_refresh(self):
        return asian_worker_resume_auto_refresh(self)

    def _worker_pause_for_cache_sync(self):
        return asian_worker_pause_for_cache_sync(self)

    def _worker_trigger_refresh(self):
        return asian_worker_trigger_refresh(self)

    def _schedule_fit_columns(self):
        if not getattr(self, "_auto_fit_columns_pending", False):
            return
        if hasattr(self, "_fit_columns_timer"):
            self._fit_columns_timer.start()

    def _has_saved_asian_header_state(self, settings_key: str) -> bool:
        return self._settings_section().contains(settings_key)

    def _fit_asian_columns_to_viewport(self):
        if not hasattr(self, "asian_table"):
            return

        header = self.asian_table.horizontalHeader()
        column_count = header.count()
        if column_count <= 0:
            return

        viewport_width = self.asian_table.viewport().width()
        if viewport_width <= 0:
            return

        current_widths = [max(1, self.asian_table.columnWidth(i)) for i in range(column_count)]
        total_width = sum(current_widths)
        if total_width <= 0:
            return

        # 预留一点边缘冗余，避免 rounding 导致最后一列被横向滚动条挤爆。
        target_width = max(column_count * 40, viewport_width - 2)
        if abs(total_width - target_width) <= column_count:
            self._auto_fit_columns_pending = False
            return

        scale = target_width / total_width
        scaled_widths = [max(40, int(round(width * scale))) for width in current_widths]
        diff = target_width - sum(scaled_widths)

        if diff != 0:
            step = 1 if diff > 0 else -1
            remaining = abs(diff)
            ordered_columns = sorted(
                range(column_count),
                key=lambda idx: current_widths[idx],
                reverse=(diff > 0),
            )
            while remaining > 0 and ordered_columns:
                changed = False
                for column in ordered_columns:
                    next_width = scaled_widths[column] + step
                    if next_width < 40:
                        continue
                    scaled_widths[column] = next_width
                    remaining -= 1
                    changed = True
                    if remaining == 0:
                        break
                if not changed:
                    break

        for column, width in enumerate(scaled_widths):
            self.asian_table.setColumnWidth(column, width)

        # 仅在首次无历史配置时铺满一次，后续尊重用户手动调整/已恢复的列宽。
        self._auto_fit_columns_pending = False

    def _format_last_success_segment(self) -> str:
        if not self._last_asian_success_at:
            return ""
        return self._status_metric("上次成功 ", self._last_asian_success_at.strftime("%H:%M:%S"))

    def _set_asian_status(
        self,
        primary: str,
        *segments: str,
        freshness: str = "",
        next_step: str = "",
    ):
        self._status_primary = str(primary or "").strip() or "亚洲市场已就绪"
        self._status_segments = tuple(str(segment or "").strip() for segment in segments if str(segment or "").strip())
        self._status_freshness = str(freshness or "").strip()
        self._status_next_step = str(next_step or "").strip()
        self._refresh_asian_status()

    def _refresh_asian_status(self):
        total = len(getattr(self, "row_data", []) or [])
        visible = self.proxy_model.rowCount() if hasattr(self, "proxy_model") else total
        search_text = self.search_box.text().strip() if hasattr(self, "search_box") else ""
        extra_segments = list(self._status_segments)
        last_success = self._format_last_success_segment()
        if last_success:
            extra_segments.append(last_success)

        self.lbl_status.setText(
            self.format_workspace_status(
                self._status_primary,
                result=f"{visible}/{total}只" if total else "0只",
                freshness=self._status_freshness or ("本地缓存" if total else "待刷新"),
                current_filter=search_text or "全部",
                next_step=self._status_next_step or "",
                extra_segments=extra_segments,
            )
        )

    def _on_worker_progress(self, message: str):
        text = str(message or "").strip()
        if not text:
            return

        if "等待缓存同步完成" in text:
            cache_thread = getattr(self, "cache_thread", None)
            cache_syncing = bool(
                getattr(self, "_is_fetching_cache", False)
                or getattr(self, "_pending_auto_cache_sync", False)
                or (cache_thread is not None and cache_thread.isRunning())
            )
            if cache_syncing:
                return

            freshness = getattr(self, "_status_freshness", "").strip()
            if not freshness:
                freshness = "本地缓存" if getattr(self, "row_data", None) else "待刷新"
            self._set_asian_status(
                "盘后静默中",
                freshness=freshness,
                next_step="可点击刷新亚洲市场",
            )
            if hasattr(self, "table_state") and getattr(self, "row_data", None):
                self.table_state.show_table()
            return

        error_markers = ("失败", "异常", "429", "检查外网", "切换网络", "空响应")
        if any(marker in text for marker in error_markers):
            self._last_asian_error = text
            cached_hint = "当前保留本地缓存" if getattr(self, "row_data", None) else "当前没有可展示缓存"
            self._set_asian_status(
                "本次刷新失败",
                text,
                cached_hint,
                freshness="远端失败沿用" if getattr(self, "row_data", None) else "待刷新",
                next_step="请稍后重试",
            )
            if hasattr(self, "table_state"):
                if self.row_data:
                    self.table_state.show_table()
                else:
                    self.table_state.show_error(
                        "亚洲报价刷新失败",
                        text,
                        meta="当前没有可展示的本地缓存。",
                        action_text="立即重试",
                        action_callback=self._on_manual_refresh,
                    )
            return

        if "正在拉取亚洲市场最新报价" in text:
            self._set_asian_status("正在刷新亚洲市场", freshness="实时", next_step="等待报价同步")
            if hasattr(self, "table_state") and not self.row_data:
                self.table_state.show_loading("正在刷新亚洲市场...", "请稍候")
            return

        if "报价更新完成" in text:
            self._set_asian_status("最新数据已同步", freshness="实时")
            if hasattr(self, "table_state"):
                self.table_state.show_table()
            return

        self._set_asian_status(text)

    def _should_start_runtime_on_show(self) -> bool:
        return BaseStockTab._should_start_interactive_runtime_on_show(self)

    def showEvent(self, event):
        super().showEvent(event)
        if self._should_start_runtime_on_show():
            self._ensure_runtime_started()
        self._schedule_fit_columns()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_fit_columns()

    def _get_cache_latest_trade_dates(self):
        try:
            from app.services.ui_market_calendar_service import MarketCalendar

            if not os.path.exists(JSON_CACHE):
                return {}
            with open(JSON_CACHE, 'r', encoding='utf-8') as f:
                raw = json.load(f)

            latest_dates = {}
            for item in raw.get('stocks', []):
                ticker = str(item.get('ticker', '') or '').strip().upper()
                if "." not in ticker:
                    continue
                market = MarketCalendar.normalize_market(ticker.split(".")[-1])
                klines = item.get('klines', [])
                if not klines:
                    continue
                last_date_raw = str(klines[-1].get('date', '')).strip()
                if not last_date_raw:
                    continue
                try:
                    last_date = datetime.datetime.strptime(last_date_raw[:10], "%Y-%m-%d").date()
                except (TypeError, ValueError):
                    continue
                if latest_dates.get(market) is None or last_date > latest_dates[market]:
                    latest_dates[market] = last_date
            return latest_dates
        except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as e:
            log.warning(f"[亚洲页] 解析缓存最新交易日失败: {e}")
            return {}

    def _get_cache_latest_trade_date(self):
        latest_dates = self._get_cache_latest_trade_dates()
        return max(latest_dates.values()) if latest_dates else None
        try:
            if not os.path.exists(JSON_CACHE):
                return None
            with open(JSON_CACHE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            latest_date = None
            for item in raw.get('stocks', []):
                klines = item.get('klines', [])
                if not klines:
                    continue
                last_date_raw = str(klines[-1].get('date', '')).strip()
                if not last_date_raw:
                    continue
                try:
                    last_date = datetime.datetime.strptime(last_date_raw[:10], "%Y-%m-%d").date()
                except (TypeError, ValueError):
                    continue
                if latest_date is None or last_date > latest_date:
                    latest_date = last_date
            return latest_date
        except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as e:
            log.warning(f"[亚洲页] 解析缓存最新交易日失败: {e}")
            return None

    def _get_expected_latest_trade_dates(self):
        try:
            from datetime import timedelta

            from app.services.ui_market_calendar_service import MarketCalendar
            markets = set()
            for row in getattr(self, 'row_data', []) or []:
                code = str(row.get("\u4ee3\u7801", row.get("code", ""))).strip()
                if "." in code:
                    markets.add(MarketCalendar.normalize_market(code.split(".")[-1]))
            if not markets:
                markets = {"TW", "HK", "T", "KS"}

            close_cutoff_hhmm = {
                "TW": 1400,
                "HK": 1630,
                "T": 1530,
                "KS": 1600,
            }

            latest_expected = {}
            for mkt in markets:
                now_mkt = MarketCalendar.now(mkt)
                today_mkt = now_mkt.date()
                hhmm = now_mkt.hour * 100 + now_mkt.minute
                cutoff = close_cutoff_hhmm.get(mkt, 1630)

                if MarketCalendar.is_trade_day(today_mkt, market=mkt) and hhmm < cutoff:
                    ref_date = today_mkt - timedelta(days=1)
                else:
                    ref_date = today_mkt

                trade_date = MarketCalendar.get_latest_trade_date(market=mkt, ref_date=ref_date)
                if trade_date is not None:
                    latest_expected[mkt] = trade_date
            return latest_expected
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
            log.warning(f"[亚洲页] 计算期望最新交易日失败: {e}")
            return {}

    def _get_expected_latest_trade_date(self):
        latest_dates = self._get_expected_latest_trade_dates()
        return max(latest_dates.values()) if latest_dates else None
        try:
            from datetime import timedelta

            from app.services.ui_market_calendar_service import MarketCalendar
            markets = set()
            for row in getattr(self, 'row_data', []) or []:
                code = str(row.get("代码", "")).strip()
                if "." in code:
                    markets.add(code.split(".")[-1].upper())
            if not markets:
                markets = {"TW", "TWO", "T", "KS", "HK"}

            # 收盘缓冲时间：只有超过该时间，才把“当日”视作应落地到本地缓存的目标交易日。
            close_cutoff_hhmm = {
                "TW": 1400,
                "TWO": 1400,
                "HK": 1630,
                "T": 1530,
                "KS": 1600,
            }

            latest_expected = None
            for mkt in markets:
                now_mkt = MarketCalendar.now(mkt)
                today_mkt = now_mkt.date()
                hhmm = now_mkt.hour * 100 + now_mkt.minute
                cutoff = close_cutoff_hhmm.get(mkt, 1630)

                # 交易日但仍处于盘前/盘中：期望缓存仍是“上一交易日”。
                if MarketCalendar.is_trade_day(today_mkt, market=mkt) and hhmm < cutoff:
                    ref_date = today_mkt - timedelta(days=1)
                else:
                    ref_date = today_mkt

                trade_date = MarketCalendar.get_latest_trade_date(market=mkt, ref_date=ref_date)
                if trade_date is not None and (latest_expected is None or trade_date > latest_expected):
                    latest_expected = trade_date
            return latest_expected
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
            log.warning(f"[亚洲页] 计算期望最新交易日失败: {e}")
            return None

    def _check_auto_cache(self):
        return asian_check_auto_cache(self)

    def _continue_auto_cache_sync(self):
        return asian_continue_auto_cache_sync(self)

    def _log_asian_health(self):
        return asian_log_asian_health(self)

    def _on_minute_tick(self):
        return asian_on_minute_tick(self)

    def _refresh_market_status_rows(self):
        return asian_refresh_market_status_rows(self, get_market_status)

    def _on_auto_cache_finished(self, success, msg):
        return asian_on_auto_cache_finished(self, success, msg)

    def _on_asian_klines_ready(self):
        return asian_on_asian_klines_ready(self)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.lbl_status = QLabel("系统初始化...")
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码或名称...")
        self.search_box.setFixedWidth(180)
        self.search_box.textChanged.connect(self._on_search_text_changed)
        self.chk_cf_proxy = QCheckBox("优先使用稳定海外线路")
        self.chk_cf_proxy.setToolTip("开启后优先使用稳定海外出口访问报价源；关闭后使用当前系统网络环境。")
        self.chk_cf_proxy.setObjectName("successStatus")
        self.chk_cf_proxy.setChecked(is_cf_proxy_enabled())
        self.chk_cf_proxy.toggled.connect(self._on_cf_proxy_toggled)

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.setToolTip("刷新亚洲市场报价，并重新检查本地缓存状态。")
        self.btn_refresh.clicked.connect(self._on_manual_refresh)

        filter_widgets = [self.search_box, self.chk_cf_proxy]
        action_widgets = [self.btn_refresh]
        toolbar = self.build_tab_toolbar("亚洲寡头核心资产监控", self.lbl_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        self.asian_table = VCPTableView(default_row_height=30)
        self.table_state = TableStateWrapper(self.asian_table, empty_title="暂无亚洲数据", loading_title="加载中...")
        layout.addWidget(self.table_state)

        self.header_labels = ["代码", "名称", "现价", "涨幅%", "PE", "市场", "状态", "赛道", "角色定位", "货币", "5日涨跌%", "10日涨跌%", "20日涨跌%"]

        self.model = StockTableModel(self.header_labels)
        self.model.set_plain_style_headers(["状态"])
        self.model.set_plain_background_headers(["涨幅%"])
        self.proxy_model = RtSortFilterProxyModel(self.asian_table)
        self.proxy_model.setSourceModel(self.model)
        self.asian_table.setModel(self.proxy_model)

        self.delegate = StockItemDelegate(self.asian_table)
        self.asian_table.setItemDelegate(self.delegate)

        # Context menu
        self.asian_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.asian_table.customContextMenuRequested.connect(self._show_context_menu)

        # Double click to open Kline
        self.asian_table.doubleClicked.connect(self._on_double_click)

        # 列宽自定义并持久化
        header_view = self.asian_table.horizontalHeader()
        header_view.setStretchLastSection(False)

        self._fit_columns_timer = QTimer(self)
        self._fit_columns_timer.setSingleShot(True)
        self._fit_columns_timer.setInterval(0)
        self._fit_columns_timer.timeout.connect(self._fit_asian_columns_to_viewport)

        default_widths = [52, 70, 140, 90, 80, 90, 80, 80, 120, 250, 60, 80, 80, 80]
        for i, w in enumerate(default_widths):
            header_view.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.asian_table.setColumnWidth(i, w)

        # 绑定防抖自动保存与恢复配置。
        # 有历史列宽时不再自动铺满，避免恢复后的用户列宽又被二次覆盖。
        header_settings_key = "header_state_asian_v4"
        self._auto_fit_columns_pending = not self._has_saved_asian_header_state(header_settings_key)
        self.bind_header_persistence(self.asian_table, header_settings_key)
        header_view.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self._schedule_fit_columns()

    def _show_context_menu(self, pos):
        index = self.asian_table.indexAt(pos)
        if not index.isValid(): return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data): return

        code = self.model.row_data[row].get("代码", "")
        name = self.model.row_data[row].get("名称", "")
        if code and name:
            from ui.components.stock_context_menu import build_stock_context_menu
            build_stock_context_menu(self, code, name)

    def _on_cf_proxy_toggled(self, checked):
        set_cf_proxy_enabled(checked)
        if hasattr(self, 'lbl_status'):
            mode_text = "已启用稳定海外线路" if checked else "已切换为当前系统网络"
            self._set_asian_status(mode_text, "下次刷新生效", self._format_last_success_segment())

    def _on_search_text_changed(self, text):
        self.set_proxy_filter_text(self.proxy_model, text)
        self._refresh_asian_status()

    def _on_manual_refresh(self):
        """手动触发外网数据更新"""
        # 先重载本地缓存并补齐缺失标的，再触发实时刷新，确保 worker 不会长期只盯着旧的 33 只
        self._load_local_cache()
        if hasattr(self, 'worker') and self.worker.isRunning():
            self._set_runtime_state("manual_refresh_once")
            self._set_asian_status("刷新已触发", "正在请求最新亚洲报价...", freshness="实时", next_step="等待报价同步")
            self._worker_trigger_refresh()
        else:
            self._set_asian_status("后台刷新线程未就绪", "请稍后再试", freshness="待刷新", next_step="稍后重试")

    def _sync_worker_codes(self):
        """让后台 worker 的轮询列表始终跟随当前表格数据，避免长期停留在旧数量。"""
        if hasattr(self, 'worker') and self.worker is not None:
            try:
                self.worker.codes = [
                    str(r.get("代码", "")).strip()
                    for r in (self.row_data or [])
                    if str(r.get("代码", "")).strip()
                ]
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
                log.warning(f"[亚洲页] 同步 worker 股票池失败: {e}")

    def update_table_ui(self):
        self.model.update_data(self.row_data)
        if hasattr(self, "table_state"):
            if self.row_data:
                self.table_state.show_table()
            else:
                self.table_state.show_error(
                    "暂无亚洲市场数据",
                    "当前没有可展示的本地缓存。",
                    action_text="刷新",
                    action_callback=self._on_manual_refresh,
                )

    def _save_rt_cache(self):
        try:
            cache_friendly = {}
            for k, v in GLOBAL_ASIAN_RT_CACHE.items():
                cache_friendly[k] = {
                    "date": v.get("date", ""),
                    "close": v.get("close", 0.0),
                    "open": v.get("open", 0.0),
                    "high": v.get("high", 0.0),
                    "low": v.get("low", 0.0),
                    "volume": v.get("volume", 0.0),
                    "previous_close": v.get("previous_close", 0.0),
                    "pct": _round_pct(v.get("pct", 0.0)),
                    "pe": v.get("pe"),
                    "pe_source": v.get("pe_source", ""),
                    "pe_updated_at": v.get("pe_updated_at", 0.0),
                    "pct_5": _round_pct(v.get("pct_5", 0.0)),
                    "pct_10": _round_pct(v.get("pct_10", 0.0)),
                    "pct_20": _round_pct(v.get("pct_20", 0.0)),
                    "currency": v.get("currency", ""),
                    "source": v.get("source", ""),
                    "quote_quality": v.get("quote_quality", ""),
                }
            cache_dir = os.path.dirname(RT_JSON_CACHE)
            if cache_dir:
                os.makedirs(cache_dir, exist_ok=True)
            with open(RT_JSON_CACHE, 'w', encoding='utf-8') as f:
                json.dump(cache_friendly, f, ensure_ascii=False)
        except (PermissionError, OSError, TypeError, ValueError) as e:
            log.error(f"[亚洲页] 持久化 RT 缓存失败: {e}")

    def _load_local_cache(self):
        if self._load_cache_in_progress:
            self._load_cache_pending = True
            log.info("[亚洲页] 本地缓存重载进行中，已追加一次待执行重载")
            return

        self._load_cache_in_progress = True
        try:
            if hasattr(self, "table_state") and not getattr(self, "row_data", None):
                self.table_state.show_loading("正在加载本地缓存...", "请稍候")

            self.row_data = []
            history_points_by_code = {}
            if os.path.exists(JSON_CACHE):
                try:
                    with open(JSON_CACHE, "r", encoding="utf-8") as f:
                        raw = json.load(f)

                    roles_map = get_role_mapping()
                    ch_names_map = get_ch_names_mapping()
                    stocks_list = raw.get("stocks", [])

                    for item in stocks_list:
                        code = item.get("ticker")
                        if not code:
                            continue

                        data_points = item.get("klines", [])
                        history_points_by_code[code] = data_points
                        close_val = 0.0
                        pct_val = 0.0
                        if len(data_points) >= 2:
                            close_val = float(data_points[-1].get("close", 0))
                            prev_close = float(data_points[-2].get("close", 0))
                            if prev_close > 0:
                                pct_val = _round_pct(((close_val / prev_close) - 1.0) * 100.0)

                        def _safe_pct(cur, ref_val):
                            return _round_pct(((cur / ref_val) - 1.0) * 100.0) if ref_val > 0 and cur > 0 else 0.0

                        pct_5 = _safe_pct(close_val, float(data_points[-6].get("close", 0))) if len(data_points) >= 6 else 0.0
                        pct_10 = _safe_pct(close_val, float(data_points[-11].get("close", 0))) if len(data_points) >= 11 else 0.0
                        pct_20 = _safe_pct(close_val, float(data_points[-21].get("close", 0))) if len(data_points) >= 21 else 0.0

                        role_desc = roles_map.get(code, item.get("name", ""))
                        market_code = item.get("market", code.split(".")[-1] if "." in code else "")
                        market_display = format_market_display(market_code, code)
                        real_status = get_market_status(code.split(".")[-1] if "." in code else "")

                        history_quote = {
                            "date": data_points[-1].get("date") if data_points else None,
                            "close": close_val,
                            "open": float(data_points[-1].get("open", 0)) if data_points else 0.0,
                            "high": float(data_points[-1].get("high", 0)) if data_points else 0.0,
                            "low": float(data_points[-1].get("low", 0)) if data_points else 0.0,
                            "volume": float(data_points[-1].get("volume", 0)) if data_points else 0.0,
                            "previous_close": float(data_points[-2].get("close", 0)) if len(data_points) >= 2 else 0.0,
                            "pct": pct_val,
                            "pe": None,
                            "pe_source": "",
                            "pe_updated_at": 0.0,
                            "pct_5": pct_5,
                            "pct_10": pct_10,
                            "pct_20": pct_20,
                            "currency": item.get("currency", ""),
                            "source": "history_cache",
                            "quote_quality": "",
                            "df_today": None,
                        }
                        existing_rt = GLOBAL_ASIAN_RT_CACHE.get(code)
                        if not existing_rt or (_safe_float(existing_rt.get("close")) <= 0 and close_val > 0):
                            GLOBAL_ASIAN_RT_CACHE[code] = history_quote

                        close_number = float(close_val) if close_val else 0.0
                        fmt_close = f"{close_number:.3f}" if 0 < close_number < 10 else (f"{close_number:.2f}" if close_number > 0 else "--")
                        display_name = item.get("name", "")
                        if ch_names_map.get(code):
                            display_name = f"{display_name}  ({ch_names_map.get(code, '未录入')})"

                        self.row_data.append(
                            {
                                "代码": code,
                                "名称": display_name,
                                "现价": fmt_close,
                                "涨幅%": pct_val,
                                "PE": "--",
                                "市场": market_display,
                                "状态": real_status,
                                "赛道": item.get("track", ""),
                                "角色定位": role_desc,
                                "货币": item.get("currency", "---"),
                                "5日涨跌%": pct_5,
                                "10日涨跌%": pct_10,
                                "20日涨跌%": pct_20,
                            }
                        )

                    try:
                        target_map = filter_asian_tickers() or {}
                    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as fetch_exc:
                        target_map = {}
                        log.warning(f"[亚洲页] 读取亚洲目标池失败，跳过缺失补齐: {fetch_exc}")

                    if target_map:
                        existing_codes = {
                            str(row.get("代码", "")).strip()
                            for row in self.row_data
                            if str(row.get("代码", "")).strip()
                        }
                        missing_codes = []

                        for en_name, ticker in target_map.items():
                            ticker = str(ticker).strip()
                            if not ticker or ticker in existing_codes:
                                continue

                            missing_codes.append(ticker)
                            market_code = ticker.split(".")[-1] if "." in ticker else ""
                            self.row_data.append(
                                {
                                    "代码": ticker,
                                    "名称": f"{en_name}  ({ch_names_map.get(ticker, '未录入')})" if ch_names_map.get(ticker) else en_name,
                                    "现价": "--",
                                    "涨幅%": 0.0,
                                    "PE": "--",
                                    "市场": format_market_display(market_code, ticker),
                                    "状态": get_market_status(market_code),
                                    "赛道": find_asian_track(ticker),
                                    "角色定位": roles_map.get(ticker, en_name),
                                    "货币": "---",
                                    "5日涨跌%": 0.0,
                                    "10日涨跌%": 0.0,
                                    "20日涨跌%": 0.0,
                                }
                            )

                            if ticker not in GLOBAL_ASIAN_RT_CACHE:
                                GLOBAL_ASIAN_RT_CACHE[ticker] = {
                                    "date": None,
                                    "close": 0.0,
                                    "open": 0.0,
                                    "high": 0.0,
                                    "low": 0.0,
                                    "volume": 0.0,
                                    "previous_close": 0.0,
                                    "pct": 0.0,
                                    "pe": None,
                                    "pe_source": "",
                                    "pe_updated_at": 0.0,
                                    "pct_5": 0.0,
                                    "pct_10": 0.0,
                                    "pct_20": 0.0,
                                    "currency": "",
                                    "source": "",
                                    "quote_quality": "",
                                    "df_today": None,
                                }

                        if missing_codes:
                            log.warning(
                                f"[亚洲页] 本地缓存缺失 {len(missing_codes)} 只，已补齐占位行: {sorted(missing_codes)}"
                            )
                except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                    log.error(f"[亚洲页] JSON 历史缓存加载失败: {exc}")

            if os.path.exists(RT_JSON_CACHE):
                try:
                    with open(RT_JSON_CACHE, "r", encoding="utf-8") as f:
                        rt_cache = json.load(f)

                    for row_dict in self.row_data:
                        code = row_dict.get("代码")
                        if code not in rt_cache:
                            continue

                        info = _normalize_cached_rt_entry(
                            rt_cache[code],
                            history_points_by_code.get(code, []),
                        )
                        close_number = float(info.get("close", 0.0))
                        if close_number <= 0 and history_points_by_code.get(code):
                            continue
                        row_dict["现价"] = f"{close_number:.3f}" if 0 < close_number < 10 else (f"{close_number:.2f}" if close_number > 0 else "--")
                        row_dict["涨幅%"] = _round_pct(info.get("pct", 0.0))
                        row_dict["PE"] = info.get("pe") if info.get("pe") is not None else "--"
                        row_dict["5日涨跌%"] = _round_pct(info.get("pct_5", 0.0))
                        row_dict["10日涨跌%"] = _round_pct(info.get("pct_10", 0.0))
                        row_dict["20日涨跌%"] = _round_pct(info.get("pct_20", 0.0))
                        if info.get("currency"):
                            row_dict["货币"] = info["currency"]

                        if code not in GLOBAL_ASIAN_RT_CACHE:
                            GLOBAL_ASIAN_RT_CACHE[code] = {}
                        GLOBAL_ASIAN_RT_CACHE[code].update(info)
                except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                    log.error(f"[亚洲页] 恢复 RT 盘口缓存失败: {exc}")

            self._sync_worker_codes()
            self.update_table_ui()
            if self.row_data:
                self._last_asian_success_at = datetime.datetime.now()
                self._set_asian_status(
                    "已载入本地缓存",
                    self._status_metric("标的 ", len(self.row_data), "只"),
                    "等待最新报价同步",
                    freshness="本地缓存",
                )
            else:
                self._set_asian_status("本地缓存为空", "可点击刷新获取最新数据", freshness="待刷新", next_step="点击刷新获取最新报价")
        finally:
            pending_reload = self._load_cache_pending
            self._load_cache_pending = False
            self._load_cache_in_progress = False
            if pending_reload:
                log.info("[亚洲页] 执行排队中的一次本地缓存重载")
                QTimer.singleShot(0, self._load_local_cache)

    def _on_rt_update(self, updates: dict):
        if not updates:
            return

        for row_idx, row_dict in enumerate(self.model.row_data):
            code = row_dict.get("代码")
            if code not in updates:
                continue

            info = updates[code]
            market_code = code.split(".")[-1] if "." in code else ""
            row_dict["状态"] = get_market_status(market_code)

            close_number = float(info["close"]) if info.get("close") else 0.0
            row_dict["现价"] = f"{close_number:.3f}" if 0 < close_number < 10 else (f"{close_number:.2f}" if close_number > 0 else "--")
            row_dict["涨幅%"] = _round_pct(info.get("pct", 0.0))
            row_dict["PE"] = info.get("pe") if info.get("pe") is not None else "--"
            row_dict["5日涨跌%"] = _round_pct(info.get("pct_5", 0.0))
            row_dict["10日涨跌%"] = _round_pct(info.get("pct_10", 0.0))
            row_dict["20日涨跌%"] = _round_pct(info.get("pct_20", 0.0))
            row_dict["货币"] = info.get("currency", row_dict.get("货币", "---"))

            self.model.dataChanged.emit(
                self.model.index(row_idx, 0),
                self.model.index(row_idx, len(self.model._headers) - 1),
            )

        self._last_asian_success_at = datetime.datetime.now()
        self._save_rt_cache()
        self._last_asian_error = ""
        self._set_asian_status(
            "最新数据已同步",
            self._status_metric("覆盖 ", len(updates), "只"),
            freshness="实时",
        )
        if hasattr(self, "table_state"):
            self.table_state.show_table()

        if self._asian_runtime_state == "manual_refresh_once":
            if self._is_quote_refresh_open():
                self._set_runtime_state("running")
                if hasattr(self, "worker") and self.worker is not None:
                    self._worker_resume_auto_refresh()
            else:
                self._set_runtime_state("paused_for_cache_sync")
                if hasattr(self, "worker") and self.worker is not None:
                    self._worker_pause_for_cache_sync()

    def _on_double_click(self, index):
        if not index.isValid(): return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data): return

        code = self.model.row_data[row].get("代码", "")
        # 按当前表格视觉排序顺序构建列表，让 K 线窗口的"上一只/下一只"跟随用户排序
        code_list = []
        clicked_visual_row = index.row()
        for r in range(self.proxy_model.rowCount()):
            s_idx = self.proxy_model.mapToSource(self.proxy_model.index(r, 0))
            if s_idx.row() < len(self.model.row_data):
                rd = dict(self.model.row_data[s_idx.row()] or {})
                rd.setdefault("代码", rd.get("代码", ""))
                rd.setdefault("名称", rd.get("名称", ""))
                code_list.append(rd)

        current_idx = 0
        if 0 <= clicked_visual_row < len(code_list):
            current_idx = clicked_visual_row

        # 触发全局画图事件
        ui_signals.sig_show_kline_with_list.emit(code, code_list, current_idx)

    def shutdown(self) -> None:
        if hasattr(self, "auto_cache_timer") and self.auto_cache_timer is not None:
            self.auto_cache_timer.stop()
        cache_thread = getattr(self, "cache_thread", None)
        if cache_thread is not None:
            request_thread_shutdown(
                cache_thread,
                label="Asian cache sync",
                stop=getattr(cache_thread, "requestInterruption", None),
                timeout_ms=5000,
                logger=log,
            )
        worker = getattr(self, "worker", None)
        if worker is not None:
            request_thread_shutdown(
                worker,
                label="Asian market worker",
                stop=getattr(worker, "stop", None),
                timeout_ms=3000,
                logger=log,
            )

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)
