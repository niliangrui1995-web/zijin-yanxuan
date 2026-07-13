# -*- coding: utf-8 -*-
import datetime
import re

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QHeaderView, QLabel, QLineEdit, QPushButton, QVBoxLayout

from app.services.asian_market_cache_service import (
    load_latest_trade_dates,
    read_mapping_cache,
    write_realtime_quote_cache,
)
from app.services.asian_market_service import filter_asian_tickers, find_asian_track
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signals
from app.services.ui_task_lifecycle_service import (
    shutdown_task_lifecycle_for_owner,
    task_lifecycle_for,
)
from app.services.ui_task_service import background_job_runner as task_manager
from app.services.ui_task_service import task_registry
from core.logger import get_logger
from ui.components import TableStateWrapper, VCPTableView
from ui.components.thread_shutdown import request_thread_shutdown
from ui.models.table_model_helpers import _emit_model_row_ranges
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.services.asian_market_runtime_service import AsianMarketRuntimeService
from ui.tabs.asian_market_meta import (
    format_market_display,
    get_ch_names_mapping,
    get_market_status,
    get_role_mapping,
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
)
from ui.tabs.base_stock_tab import BaseStockTab

log = get_logger(__name__)
_ASIAN_MARKET_LOCAL_CACHE_TASK = task_registry.workspace("asian_market_local_cache")
ASIAN_PINNED_CODES_SETTINGS_KEY = "pinned_codes_v1"
_DEFERRED_REPAINT_COUNT_RE = re.compile(r"cached\s+(\d+)\s+updates", re.IGNORECASE)


def _safe_float(value) -> float:
    try:
        return float(str(value).replace("%", ""))
    except (TypeError, ValueError):
        return 0.0


def _round_pct(value) -> float:
    return round(_safe_float(value), 2)


def _parse_deferred_repaint_message(message: str) -> tuple[str, str, str, str] | None:
    text = str(message or "").strip()
    lower_text = text.lower()
    if "deferred ui repaint" not in lower_text or "cached" not in lower_text:
        return None

    match = _DEFERRED_REPAINT_COUNT_RE.search(text)
    cached_segment = f"已缓存 {match.group(1)} 只部分更新" if match else "已缓存部分更新"
    if "source payload degraded" in lower_text:
        return (
            "替代源降级",
            "替代实时源返回异常，本轮未强制刷新表格",
            cached_segment,
            "替代源降级",
        )
    return (
        "刷新超时降级",
        "本轮达到时间预算，未强制刷新表格",
        cached_segment,
        "超时降级",
    )


def _normalize_asian_pinned_codes(value) -> list[str]:
    if isinstance(value, str):
        raw_items = value.replace(";", ",").replace("|", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = value
    elif value is None:
        raw_items = []
    else:
        raw_items = [value]

    codes = []
    for item in raw_items:
        code = str(item or "").strip()
        if code and code not in codes:
            codes.append(code)
    return codes


def _asian_row_is_trading(row: dict) -> bool:
    return "交易中" in str(row.get("状态", "") or "")


def _asian_row_is_closed(row: dict) -> bool:
    status = str(row.get("状态", "") or "")
    return "休市" in status and "午间休市" not in status


def _order_asian_rows_by_pinned_codes(rows, pinned_codes) -> list[dict]:
    row_list = [row for row in rows or [] if isinstance(row, dict)]
    pinned_order = {code: idx for idx, code in enumerate(_normalize_asian_pinned_codes(pinned_codes))}
    apply_closed_bottom = any(_asian_row_is_trading(row) for row in row_list) and any(
        _asian_row_is_closed(row) for row in row_list
    )

    def _sort_key(indexed_row):
        index, row = indexed_row
        code = str(row.get("代码", "") or "").strip()
        if code in pinned_order:
            return 0, pinned_order[code], index
        closed_rank = 1 if apply_closed_bottom and _asian_row_is_closed(row) else 0
        return 1, closed_rank, index

    return [row for _index, row in sorted(enumerate(row_list), key=_sort_key)]


class AsianPinnedSortFilterProxyModel(RtSortFilterProxyModel):
    def __init__(self, parent=None, pinned_codes_getter=None):
        super().__init__(parent)
        self._pinned_codes_getter = pinned_codes_getter

    def _source_row(self, source_index):
        if not source_index.isValid():
            return None

        model = self.sourceModel()
        rows = getattr(model, "row_data", []) or []
        row = source_index.row()
        if row < 0 or row >= len(rows):
            return None
        return rows[row]

    def _should_group_closed_rows(self) -> bool:
        model = self.sourceModel()
        rows = getattr(model, "row_data", []) or []
        return any(_asian_row_is_trading(row) for row in rows) and any(_asian_row_is_closed(row) for row in rows)

    def _closed_rank(self, source_index) -> int:
        if not self._should_group_closed_rows():
            return 0

        row = self._source_row(source_index)
        return 1 if isinstance(row, dict) and _asian_row_is_closed(row) else 0

    def _pinned_rank(self, source_index):
        if not callable(self._pinned_codes_getter):
            return None

        row = self._source_row(source_index)
        if not isinstance(row, dict):
            return None

        code = str(row.get("代码", "") or "").strip()
        pinned_order = {
            pinned_code: index for index, pinned_code in enumerate(_normalize_asian_pinned_codes(self._pinned_codes_getter()))
        }
        return pinned_order.get(code)

    def lessThan(self, left, right):
        left_rank = self._pinned_rank(left)
        right_rank = self._pinned_rank(right)
        if left_rank is not None or right_rank is not None:
            descending = self.sortOrder() == Qt.SortOrder.DescendingOrder
            if left_rank is None:
                return descending
            if right_rank is None:
                return not descending
            if left_rank != right_rank:
                return left_rank > right_rank if descending else left_rank < right_rank
        left_closed_rank = self._closed_rank(left)
        right_closed_rank = self._closed_rank(right)
        if left_closed_rank != right_closed_rank:
            descending = self.sortOrder() == Qt.SortOrder.DescendingOrder
            return left_closed_rank > right_closed_rank if descending else left_closed_rank < right_closed_rank
        return super().lessThan(left, right)


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

    close_value = _safe_float(normalized.get("close"))
    if data_points:
        rt_date = str(normalized.get("date") or "").strip()
        history_date = str(data_points[-1].get("date") or "").strip()
        history_close = _safe_float(data_points[-1].get("close"))
        prefer_history_quote = bool(
            history_date
            and history_close > 0
            and (
                not rt_date
                or rt_date < history_date
                or (source == "tencent_hk" and rt_date <= history_date)
            )
        )
        if prefer_history_quote:
            close_value = history_close
            normalized["date"] = history_date
            normalized["close"] = history_close
            for key in ("open", "high", "low", "volume"):
                history_value = _safe_float(data_points[-1].get(key))
                if history_value > 0 or key == "volume":
                    normalized[key] = history_value

    prev_close = _resolve_cached_rt_previous_close(normalized, data_points)
    if prev_close is None or prev_close <= 0:
        return normalized

    normalized["previous_close"] = prev_close
    if close_value > 0:
        normalized["pct"] = _round_pct(((close_value / prev_close) - 1.0) * 100.0)
        for days_ago in (5, 10, 20):
            if len(data_points) >= days_ago + 1:
                past_close = _safe_float(data_points[-(days_ago + 1)].get("close"))
                if past_close > 0:
                    normalized[f"pct_{days_ago}"] = _round_pct(((close_value / past_close) - 1.0) * 100.0)
    return normalized


def _format_asian_close(close_number: float) -> str:
    if 0 < close_number < 10:
        return f"{close_number:.3f}"
    if close_number > 0:
        return f"{close_number:.2f}"
    return "--"


def _empty_asian_rt_entry() -> dict:
    return {
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


def build_asian_market_local_cache_payload(
    *,
    json_cache: str | None = None,
    rt_json_cache: str | None = None,
    existing_rt_cache: dict | None = None,
) -> dict:
    json_cache = json_cache or JSON_CACHE
    rt_json_cache = rt_json_cache or RT_JSON_CACHE
    existing_rt_cache = dict(existing_rt_cache or {})
    row_data = []
    rt_updates: dict[str, dict] = {}
    history_points_by_code = {}

    raw = read_mapping_cache(json_cache)
    if raw:
        try:
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
                existing_rt = existing_rt_cache.get(code)
                if not existing_rt or (_safe_float(existing_rt.get("close")) <= 0 and close_val > 0):
                    rt_updates[code] = history_quote

                display_name = item.get("name", "")
                if ch_names_map.get(code):
                    display_name = f"{display_name}  ({ch_names_map.get(code, '未录入')})"

                row_data.append(
                    {
                        "代码": code,
                        "名称": display_name,
                        "现价": _format_asian_close(float(close_val) if close_val else 0.0),
                        "涨幅%": pct_val,
                        "PE": "--",
                        "市场": format_market_display(market_code, code),
                        "状态": get_market_status(code.split(".")[-1] if "." in code else ""),
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
                existing_codes = {str(row.get("代码", "")).strip() for row in row_data if str(row.get("代码", "")).strip()}
                missing_codes = []

                for en_name, ticker in target_map.items():
                    ticker = str(ticker).strip()
                    if not ticker or ticker in existing_codes:
                        continue

                    missing_codes.append(ticker)
                    market_code = ticker.split(".")[-1] if "." in ticker else ""
                    row_data.append(
                        {
                            "代码": ticker,
                            "名称": f"{en_name}  ({ch_names_map.get(ticker, '未录入')})"
                            if ch_names_map.get(ticker)
                            else en_name,
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

                    if ticker not in existing_rt_cache:
                        rt_updates[ticker] = _empty_asian_rt_entry()

                if missing_codes:
                    log.warning(f"[亚洲页] 本地缓存缺失 {len(missing_codes)} 只，已补齐占位行: {sorted(missing_codes)}")
        except (
            TypeError,
            ValueError,
            KeyError,
        ) as exc:
            log.error(f"[亚洲页] JSON 历史缓存加载失败: {exc}")

    rt_cache = read_mapping_cache(rt_json_cache)
    if rt_cache:
        try:
            for row_dict in row_data:
                code = row_dict.get("代码")
                if code not in rt_cache:
                    continue

                info = _normalize_cached_rt_entry(rt_cache[code], history_points_by_code.get(code, []))
                close_number = float(info.get("close", 0.0))
                if close_number <= 0 and history_points_by_code.get(code):
                    continue
                row_dict["现价"] = _format_asian_close(close_number)
                row_dict["涨幅%"] = _round_pct(info.get("pct", 0.0))
                row_dict["PE"] = info.get("pe") if info.get("pe") is not None else "--"
                row_dict["5日涨跌%"] = _round_pct(info.get("pct_5", 0.0))
                row_dict["10日涨跌%"] = _round_pct(info.get("pct_10", 0.0))
                row_dict["20日涨跌%"] = _round_pct(info.get("pct_20", 0.0))
                if info.get("currency"):
                    row_dict["货币"] = info["currency"]

                updated_info = dict(rt_updates.get(code) or {})
                updated_info.update(info)
                rt_updates[code] = updated_info
        except (
            TypeError,
            ValueError,
            KeyError,
        ) as exc:
            log.error(f"[亚洲页] 恢复 RT 盘口缓存失败: {exc}")

    return {"rows": row_data, "rt_updates": rt_updates}


class AsianMarketTab(BaseStockTab):
    """亚洲寡头行情面板"""

    def __init__(self, data_provider=None, parent=None, *, local_cache_delay_ms: int = 0):
        super().__init__(data_provider, parent)
        try:
            self._local_cache_delay_ms = max(0, int(local_cache_delay_ms))
        except (TypeError, ValueError):
            self._local_cache_delay_ms = 0
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
        self._asian_shutting_down = False
        self._pending_hidden_rt_update = False
        self.row_data = []
        self._pinned_asian_codes = self._load_pinned_asian_codes()
        self.cache_thread = None
        self._asian_market_service = self._resolve_asian_market_service()
        self._owns_asian_market_service = self._asian_market_service.parent() is self
        self._init_ui()

        # 1. 冷开机瞬间加载本地 JSON (asian_klines_latest.json)
        if self._local_cache_delay_ms > 0:
            QTimer.singleShot(self._local_cache_delay_ms, self._load_local_cache)
        else:
            self._load_local_cache()

        # 后台轮询由全局 AsianMarketRuntimeService 接管，Tab 只渲染缓存和服务事件。

        # 2. 监听全局数据更新事件 (如被 deferred_load 静默更新完毕)
        event_bus.sig_asian_klines_ready.connect(self._on_asian_klines_ready)
        self._connect_asian_market_service()

    def _ensure_runtime_started(self):
        if self._runtime_started:
            return
        self._runtime_started = True
        service = getattr(self, "_asian_market_service", None)
        sync_runtime_state = getattr(service, "sync_runtime_state", None)
        if callable(sync_runtime_state):
            QTimer.singleShot(1000, sync_runtime_state)

    def _resolve_asian_market_service(self):
        parent = self.parent()
        host = None
        try:
            host = parent.window() if parent is not None else self.window()
        except RuntimeError:
            host = None
        service = getattr(host, "asian_market_service", None)
        if isinstance(service, AsianMarketRuntimeService):
            return service
        return AsianMarketRuntimeService(parent=self, worker_factory=AsianMarketWorker)

    def _connect_asian_market_service(self) -> None:
        service = getattr(self, "_asian_market_service", None)
        if service is None:
            return
        service.sig_progress.connect(self._on_worker_progress)
        service.sig_rt_update.connect(self._on_rt_update)
        service.sig_runtime_state_changed.connect(self._on_service_runtime_state_changed)

    def _on_service_runtime_state_changed(self, payload) -> None:
        data = payload if isinstance(payload, dict) else {}
        state = str(data.get("state") or "").strip()
        if state:
            self._set_runtime_state(state)

    def _set_runtime_state(self, state: str):
        self._asian_runtime_state = state

    def _runtime_state_text(self) -> str:
        return asian_runtime_state_text(self._asian_runtime_state)

    @property
    def worker(self):
        service = getattr(self, "_asian_market_service", None)
        current_worker = getattr(service, "current_worker", None)
        if callable(current_worker):
            return current_worker()
        return None

    def _call_worker_method(self, method_name: str):
        service = getattr(self, "_asian_market_service", None)
        worker = service.current_worker() if service is not None else None
        if worker is None:
            return None
        method = getattr(worker, method_name, None)
        if callable(method):
            return method()
        return None

    def _get_tracked_codes(self) -> list[str]:
        service = getattr(self, "_asian_market_service", None)
        target_codes = getattr(service, "target_codes", None)
        if callable(target_codes):
            codes = [str(code).strip() for code in target_codes() or [] if str(code).strip()]
            if codes:
                return list(dict.fromkeys(codes))

        worker_codes = [
            str(code).strip() for code in getattr(getattr(self, "worker", None), "codes", []) or [] if str(code).strip()
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
        service = getattr(self, "_asian_market_service", None)
        if service is not None:
            return service.resume_auto_refresh()
        return asian_worker_resume_auto_refresh(self)

    def _worker_pause_for_cache_sync(self):
        service = getattr(self, "_asian_market_service", None)
        if service is not None:
            return service.pause_for_cache_sync()
        return asian_worker_pause_for_cache_sync(self)

    def _worker_trigger_refresh(self):
        service = getattr(self, "_asian_market_service", None)
        if service is not None:
            return service.trigger_refresh_once()
        return asian_worker_trigger_refresh(self)

    def _schedule_fit_columns(self):
        if not getattr(self, "_auto_fit_columns_pending", False):
            return
        if hasattr(self, "_fit_columns_timer"):
            self._fit_columns_timer.start()

    def _has_saved_asian_header_state(self, settings_key: str) -> bool:
        return self._settings_section().contains(settings_key)

    def _load_pinned_asian_codes(self) -> list[str]:
        try:
            settings = self._settings_section()
            return _normalize_asian_pinned_codes(settings.value(ASIAN_PINNED_CODES_SETTINGS_KEY, []))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return []

    def _save_pinned_asian_codes(self) -> None:
        try:
            settings = self._settings_section()
            settings.setValue(ASIAN_PINNED_CODES_SETTINGS_KEY, list(self._pinned_asian_codes))
            sync = getattr(settings, "sync", None)
            if callable(sync):
                sync()
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            log.debug(f"[亚洲页] 保存置顶标的失败: {exc}")

    def _ordered_asian_rows(self) -> list[dict]:
        return _order_asian_rows_by_pinned_codes(self.row_data, self._pinned_asian_codes)

    def _is_asian_code_pinned(self, code: str) -> bool:
        normalized = _normalize_asian_pinned_codes([code])
        return bool(normalized and normalized[0] in self._pinned_asian_codes)

    def _pin_asian_code_to_top(self, code: str) -> None:
        normalized = _normalize_asian_pinned_codes([code])
        if not normalized:
            return

        target = normalized[0]
        self._pinned_asian_codes = [target] + [item for item in self._pinned_asian_codes if item != target]
        self._save_pinned_asian_codes()
        self.update_table_ui()
        self._set_asian_status("已置顶", f"{target} 已移到顶部", freshness=self._status_freshness)

    def _unpin_asian_code(self, code: str) -> None:
        normalized = _normalize_asian_pinned_codes([code])
        if not normalized:
            return

        target = normalized[0]
        self._pinned_asian_codes = [item for item in self._pinned_asian_codes if item != target]
        self._save_pinned_asian_codes()
        self.update_table_ui()
        self._set_asian_status("已取消置顶", f"{target} 恢复默认顺序", freshness=self._status_freshness)

    def _build_asian_pin_action(self, code: str):
        if self._is_asian_code_pinned(code):
            return "取消置顶", lambda code=code: self._unpin_asian_code(code)
        return "置顶", lambda code=code: self._pin_asian_code_to_top(code)

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

        deferred_repaint = _parse_deferred_repaint_message(text)
        if deferred_repaint is not None:
            primary, reason_segment, cached_segment, freshness = deferred_repaint
            cache_hint = "当前表格沿用可用缓存" if getattr(self, "row_data", None) else "当前没有可展示缓存"
            self._set_runtime_state("degraded")
            self._set_asian_status(
                primary,
                reason_segment,
                cached_segment,
                cache_hint,
                freshness=freshness,
                next_step="等待短暂退避后自动重试",
            )
            if hasattr(self, "table_state"):
                if self.row_data:
                    self.table_state.show_table()
                else:
                    self.table_state.show_error(
                        "亚洲报价刷新降级",
                        reason_segment,
                        meta="当前没有可展示的本地缓存。",
                        action_text="立即重试",
                        action_callback=self._on_manual_refresh,
                    )
            return

        lower_text = text.lower()
        if "后台刷新已短暂降级" in text or "low time budget" in lower_text or "timeout degraded markets" in lower_text:
            cache_hint = "当前表格沿用可用缓存" if getattr(self, "row_data", None) else "当前没有可展示缓存"
            self._set_runtime_state("degraded")
            self._set_asian_status(
                "刷新短暂降级",
                text,
                cache_hint,
                freshness="超时降级",
                next_step="等待自动重试",
            )
            if hasattr(self, "table_state") and getattr(self, "row_data", None):
                self.table_state.show_table()
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
        if getattr(self, "_pending_hidden_rt_update", False):
            self._pending_hidden_rt_update = False
            self.update_table_ui()
        self._schedule_fit_columns()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_fit_columns()

    def _get_cache_latest_trade_dates(self):
        return load_latest_trade_dates(JSON_CACHE)

    def _get_cache_latest_trade_date(self):
        latest_dates = self._get_cache_latest_trade_dates()
        return max(latest_dates.values()) if latest_dates else None

    def _get_expected_latest_trade_dates(self):
        try:
            from datetime import timedelta

            from app.services.ui_market_calendar_service import MarketCalendar

            markets = set()
            for row in getattr(self, "row_data", []) or []:
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

    def _check_auto_cache(self):
        return asian_check_auto_cache(self)

    def _continue_auto_cache_sync(self):
        return asian_continue_auto_cache_sync(self)

    def _log_asian_health(self):
        return asian_log_asian_health(self)

    def _on_minute_tick(self):
        return asian_on_minute_tick(self)

    def _refresh_asian_row_order(self):
        current_rows = list(getattr(self.model, "row_data", []) or [])
        if not current_rows:
            return

        ordered_rows = _order_asian_rows_by_pinned_codes(current_rows, self._pinned_asian_codes)
        self.row_data = ordered_rows
        current_codes = [str(row.get("代码", "") or "").strip() for row in current_rows if isinstance(row, dict)]
        ordered_codes = [str(row.get("代码", "") or "").strip() for row in ordered_rows if isinstance(row, dict)]
        if current_codes != ordered_codes:
            self.model.update_data(ordered_rows)

    def _refresh_market_status_rows(self):
        result = asian_refresh_market_status_rows(self, get_market_status)
        self._refresh_asian_row_order()
        return result

    def _on_auto_cache_finished(self, success, msg):
        return asian_on_auto_cache_finished(self, success, msg)

    def _on_asian_klines_ready(self):
        return asian_on_asian_klines_ready(self)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.lbl_status = QLabel("系统初始化...")
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("筛选代码或名称...")
        self.search_box.setFixedWidth(180)
        self.search_box.textChanged.connect(self._on_search_text_changed)

        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.setToolTip("刷新亚洲市场报价，并重新检查本地缓存状态。")
        self.btn_refresh.clicked.connect(self._on_manual_refresh)

        filter_widgets = [self.search_box]
        action_widgets = [self.btn_refresh]
        toolbar = self.build_tab_toolbar("亚洲寡头核心资产监控", self.lbl_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)

        self.asian_table = VCPTableView(default_row_height=30)
        self.asian_table.setProperty("suppressLeftRails", True)
        self.asian_table.setProperty("simpleCellPaint", True)
        self.asian_table.set_ambient_repaint_enabled(False)
        self.table_state = TableStateWrapper(self.asian_table, empty_title="暂无亚洲数据", loading_title="加载中...")
        layout.addWidget(self.table_state)

        self.header_labels = [
            "代码",
            "名称",
            "现价",
            "涨幅%",
            "PE",
            "市场",
            "状态",
            "赛道",
            "角色定位",
            "货币",
            "5日涨跌%",
            "10日涨跌%",
            "20日涨跌%",
        ]

        self.model = StockTableModel(self.header_labels)
        self.model.set_plain_style_headers(["状态"])
        self.model.set_plain_background_headers(["涨幅%"])
        self.proxy_model = AsianPinnedSortFilterProxyModel(
            self.asian_table,
            pinned_codes_getter=lambda: self._pinned_asian_codes,
        )
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
        if not index.isValid():
            return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data):
            return

        code = self.model.row_data[row].get("代码", "")
        name = self.model.row_data[row].get("名称", "")
        if code and name:
            from ui.components.stock_context_menu import build_stock_context_menu

            build_stock_context_menu(self, code, name, extra_actions=[self._build_asian_pin_action(code)])

    def _on_search_text_changed(self, text):
        self.set_proxy_filter_text(self.proxy_model, text)
        self._refresh_asian_status()

    def _on_manual_refresh(self):
        """手动触发外网数据更新"""
        # 先重载本地缓存并补齐缺失标的，再触发实时刷新，确保 worker 不会长期只盯着旧的 33 只
        self._load_local_cache()
        service = getattr(self, "_asian_market_service", None)
        worker = getattr(self, "worker", None)
        worker_running = False
        if worker is not None:
            try:
                worker_running = bool(worker.isRunning())
            except RuntimeError:
                worker_running = False

        if service is not None or worker_running:
            self._set_runtime_state("manual_refresh_once")
            self._set_asian_status("刷新已触发", "正在请求最新亚洲报价...", freshness="实时", next_step="等待报价同步")
            if self._worker_trigger_refresh() is False:
                self._set_asian_status("后台刷新线程未就绪", "请稍后再试", freshness="待刷新", next_step="稍后重试")
        else:
            self._set_asian_status("后台刷新线程未就绪", "请稍后再试", freshness="待刷新", next_step="稍后重试")

    def _sync_worker_codes(self):
        """让后台 worker 的轮询列表始终跟随当前表格数据，避免长期停留在旧数量。"""
        service = getattr(self, "_asian_market_service", None)
        set_target_codes = getattr(service, "set_target_codes", None)
        if callable(set_target_codes):
            set_target_codes(
                [str(r.get("代码", "")).strip() for r in (self.row_data or []) if str(r.get("代码", "")).strip()]
            )
            return
        if hasattr(self, "worker") and self.worker is not None:
            try:
                self.worker.codes = [
                    str(r.get("代码", "")).strip() for r in (self.row_data or []) if str(r.get("代码", "")).strip()
                ]
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
                log.warning(f"[亚洲页] 同步 worker 股票池失败: {e}")

    def update_table_ui(self):
        self.model.update_data(self._ordered_asian_rows())
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
            write_realtime_quote_cache(GLOBAL_ASIAN_RT_CACHE, RT_JSON_CACHE)
        except (PermissionError, OSError, TypeError, ValueError) as e:
            log.error(f"[亚洲页] 持久化 RT 缓存失败: {e}")

    def _load_local_cache(self):
        if self._asian_shutting_down or getattr(self, "_runtime_cleanup_done", False):
            return
        if self._load_cache_in_progress:
            self._load_cache_pending = True
            log.info("[亚洲页] 本地缓存重载进行中，已追加一次待执行重载")
            return

        self._load_cache_in_progress = True
        if hasattr(self, "table_state") and not getattr(self, "row_data", None):
            self.table_state.show_loading("正在加载本地缓存...", "请稍候")

        existing_rt_cache = dict(GLOBAL_ASIAN_RT_CACHE or {})
        task_lifecycle_for(self, runner=task_manager).run_background(
            "local_cache_load",
            lambda _cancellation_token: build_asian_market_local_cache_payload(
                json_cache=JSON_CACHE,
                rt_json_cache=RT_JSON_CACHE,
                existing_rt_cache=existing_rt_cache,
            ),
            task_id=_ASIAN_MARKET_LOCAL_CACHE_TASK,
            timeout_sec=60.0,
            on_success=self._apply_local_cache_payload,
            on_error=self._on_local_cache_failed,
            runner=task_manager,
        )

    def _finish_local_cache_load(self):
        pending_reload = self._load_cache_pending
        self._load_cache_pending = False
        self._load_cache_in_progress = False
        if pending_reload and not self._asian_shutting_down:
            log.info("[亚洲页] 执行排队中的一次本地缓存重载")
            QTimer.singleShot(0, self._load_local_cache)

    def _apply_local_cache_payload(self, payload: dict):
        try:
            payload = payload or {}
            for code, info in dict(payload.get("rt_updates") or {}).items():
                if code not in GLOBAL_ASIAN_RT_CACHE:
                    GLOBAL_ASIAN_RT_CACHE[code] = {}
                GLOBAL_ASIAN_RT_CACHE[code].update(dict(info or {}))

            self.row_data = list(payload.get("rows") or [])
            self._sync_worker_codes()
            QTimer.singleShot(0, lambda: AsianMarketTab._finish_apply_local_cache_payload(self))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._on_local_cache_failed(str(exc))

    def _finish_apply_local_cache_payload(self):
        try:
            if getattr(self, "_runtime_cleanup_done", False):
                return
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
                self._set_asian_status(
                    "本地缓存为空", "可点击刷新获取最新数据", freshness="待刷新", next_step="点击刷新获取最新报价"
                )
        finally:
            self._finish_local_cache_load()

    def _on_local_cache_failed(self, error_message: str):
        try:
            log.error(f"[亚洲页] 本地缓存后台加载失败: {error_message}")
            self._set_asian_status("本地缓存加载失败", str(error_message or ""), freshness="待刷新", next_step="点击刷新重试")
            if hasattr(self, "table_state") and not getattr(self, "row_data", None):
                self.table_state.show_empty("暂无亚洲市场缓存")
        finally:
            self._finish_local_cache_load()

    def _on_rt_update(self, updates: dict):
        if not updates:
            return

        update_roles = [
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
            Qt.ItemDataRole.ForegroundRole,
            Qt.ItemDataRole.BackgroundRole,
            Qt.ItemDataRole.FontRole,
            Qt.ItemDataRole.TextAlignmentRole,
            Qt.ItemDataRole.UserRole,
            Qt.ItemDataRole.UserRole + 2,
            Qt.ItemDataRole.UserRole + 4,
            Qt.ItemDataRole.UserRole + 5,
        ]

        changed_rows = []
        for row_idx, row_dict in enumerate(self.model.row_data):
            code = row_dict.get("代码")
            if code not in updates:
                continue

            info = updates[code]
            market_code = code.split(".")[-1] if "." in code else ""
            row_dict["状态"] = get_market_status(market_code)

            close_number = float(info["close"]) if info.get("close") else 0.0
            row_dict["现价"] = (
                f"{close_number:.3f}"
                if 0 < close_number < 10
                else (f"{close_number:.2f}" if close_number > 0 else "--")
            )
            row_dict["涨幅%"] = _round_pct(info.get("pct", 0.0))
            row_dict["PE"] = info.get("pe") if info.get("pe") is not None else "--"
            row_dict["5日涨跌%"] = _round_pct(info.get("pct_5", 0.0))
            row_dict["10日涨跌%"] = _round_pct(info.get("pct_10", 0.0))
            row_dict["20日涨跌%"] = _round_pct(info.get("pct_20", 0.0))
            row_dict["货币"] = info.get("currency", row_dict.get("货币", "---"))

            changed_rows.append(row_idx)

        self._last_asian_success_at = datetime.datetime.now()
        self._last_asian_error = ""
        if not changed_rows:
            return

        if not self.isVisible():
            self._pending_hidden_rt_update = True
            return

        clear_sort_cache = getattr(self.model, "_clear_sort_value_cache_for_rows", None)
        if callable(clear_sort_cache):
            clear_sort_cache(changed_rows)
        _emit_model_row_ranges(
            self.model,
            changed_rows,
            0,
            len(self.model._headers) - 1,
            update_roles,
        )
        self._set_asian_status(
            "最新数据已同步",
            self._status_metric("覆盖 ", len(updates), "只"),
            freshness="实时",
        )
        if hasattr(self, "table_state"):
            self.table_state.show_table()
        self._refresh_asian_row_order()

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
        if not index.isValid():
            return
        source_idx = self.proxy_model.mapToSource(index)
        row = source_idx.row()
        if row >= len(self.model.row_data):
            return

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
        if self._asian_shutting_down:
            return
        self._asian_shutting_down = True
        shutdown_task_lifecycle_for_owner(self, timeout_ms=1_000)
        try:
            event_bus.sig_asian_klines_ready.disconnect(self._on_asian_klines_ready)
        except (TypeError, RuntimeError):
            pass
        service = getattr(self, "_asian_market_service", None)
        if service is not None:
            for signal, callback in (
                (getattr(service, "sig_progress", None), self._on_worker_progress),
                (getattr(service, "sig_rt_update", None), self._on_rt_update),
                (getattr(service, "sig_runtime_state_changed", None), self._on_service_runtime_state_changed),
            ):
                try:
                    if signal is not None:
                        signal.disconnect(callback)
                except (TypeError, RuntimeError):
                    pass
            if getattr(self, "_owns_asian_market_service", False):
                service.shutdown()
        cache_thread = getattr(self, "cache_thread", None)
        if cache_thread is not None:
            request_thread_shutdown(
                cache_thread,
                label="Asian cache sync",
                stop=getattr(cache_thread, "cancel", None),
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
