import json
from dataclasses import dataclass

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
)

from app.services.scan_cache_service import load_scan_cache, save_scan_cache
from app.services.scan_runtime_service import VCPParams
from app.services.ui_config_service import app_config
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_event_service import ui_signals
from app.services.ui_market_calendar_service import MarketCalendar
from app.services.ui_task_lifecycle_service import TaskLifecycleGroup
from core.logger import get_logger
from ui.components import TableStateWrapper, VCPTableView
from ui.components.thread_shutdown import request_thread_shutdown
from ui.components.toast_widget import show_toast
from ui.models.table_models import RtSortFilterProxyModel, StockItemDelegate, StockTableModel
from ui.services.scan_f5_incremental_coordinator import build_scan_f5_incremental_coordinator
from ui.tabs.base_stock_tab import BaseStockTab
from ui.workspaces.background_preload_receipt import cancel_background_preload_tasks
from ui.workspaces.tab_registry import create_tab_lineage_service

log = get_logger(__name__)


@dataclass
class _ScanCachePreloadState:
    started: bool = False
    done: bool = False
    committed: bool = False
    deferred_payload: tuple[dict, list] | None = None
    prepared_rows: list | None = None
    committing: bool = False
    skip_visible_quote_prime: bool = False


def _format_worker_scan_date(value: str) -> str:
    compact = value.replace("-", "")
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}" if len(compact) == 8 else compact


def _load_scan_cache_snapshot(cancellation_token):
    """在 GUI 线程外只读取纯数据缓存快照。"""
    cancellation_token.raise_if_cancelled()
    cache_data, migrated = load_scan_cache()
    cancellation_token.raise_if_cancelled()
    results = cache_data.get("results", []) if isinstance(cache_data, dict) else []
    prepared_rows = _prepare_scan_cache_rows(results)
    cancellation_token.raise_if_cancelled()
    return cache_data, bool(migrated), prepared_rows


def _prepare_scan_cache_rows(results) -> list:
    """在后台线程完成扫描缓存的去重与排序，首次显示只需提交模型。"""

    def _date_key(row: dict) -> str:
        return "".join(ch for ch in str(row.get("触发日期", "") or "") if ch.isdigit())[:8]

    def _score_key(row: dict) -> float:
        try:
            return float(row.get("评分", float("-inf")))
        except (TypeError, ValueError):
            return float("-inf")

    rows_by_code: dict[str, dict] = {}
    rows_without_code: list[dict] = []
    for raw_row in results or ():
        if not isinstance(raw_row, dict):
            continue
        row = dict(raw_row)
        code = str(row.get("代码", "") or "").strip()
        if not code:
            rows_without_code.append(row)
            continue
        current = rows_by_code.get(code)
        if current is None or _date_key(row) >= _date_key(current):
            rows_by_code[code] = row

    prepared = [*rows_by_code.values(), *rows_without_code]
    prepared.sort(key=lambda row: (_date_key(row), _score_key(row)), reverse=True)
    return prepared


def _scan_worker_params(tab) -> VCPParams:
    return VCPParams(
        rps_threshold=tab.spn_scan_rps.value(),
        amp_threshold=tab.spn_scan_amp.value(),
        ma_bind_threshold=tab.spn_scan_ma_bind.value(),
        high_250_threshold=tab.spn_scan_high250.value(),
        min_amount_20d=tab.spn_scan_amount.value() * 1e8,
    )


def _begin_scan_lifecycle(tab):
    lifecycle = getattr(tab, "_task_lifecycle", None)
    if lifecycle is None:
        lifecycle = TaskLifecycleGroup()
        tab._task_lifecycle = lifecycle
    lifecycle.cancel("scan_cache_load", reason="scan_started")
    preload = getattr(tab, "_scan_cache_preload", None)
    if preload is not None:
        preload.deferred_payload = None
        preload.done = True
    tab._scan_token = lifecycle.begin("scan", timeout_sec=ScanTab.SCAN_TIMEOUT_SEC)
    return tab._scan_token


def _start_scan_worker(tab, sd: str, ed: str, params: VCPParams) -> None:
    from ui.workers.scan_worker import ScanWorker

    tab.worker = ScanWorker(
        tab.data_provider,
        tab.engine,
        sd,
        ed,
        params,
        cancellation_token=tab._scan_token,
        timeout_sec=ScanTab.SCAN_TIMEOUT_SEC,
    )
    tab.worker.progress.connect(lambda p, m: ui_signals.sig_task_progress.emit("scan", p, m))
    tab.worker.result_ready.connect(tab._on_scan_results)
    tab.worker.finished_scan.connect(tab._on_scan_finished)
    tab.worker.finished.connect(tab._on_worker_thread_finished)
    tab.worker.start()


def _render_empty_scan_table(owner) -> None:
    owner._current_results = []
    result = owner._describe_scan_rows([])
    owner._last_scan_result = result
    if result.signature != owner._last_scan_signature:
        owner.source_model.update_data(result.rows)
        owner._last_scan_signature = result.signature
    if hasattr(owner, "table_state"):
        owner.table_state.show_empty("暂无扫描结果")
    owner._refresh_scan_status("本次无结果")


def _normalize_scan_render_rows(results) -> tuple[object, bool]:
    import pandas as pd

    try:
        frame = pd.DataFrame(results).sort_values("触发日期").drop_duplicates(subset=["代码"], keep="last")
        if "评分" in frame.columns:
            frame["评分_tmp"] = pd.to_numeric(frame["评分"], errors="coerce")
            frame = frame.sort_values(by=["触发日期", "评分_tmp"], ascending=[False, False])
            frame = frame.drop(columns=["评分_tmp"])
        return frame.to_dict("records"), True
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        event_bus.sig_system_log.emit("error", f"数据整理失败: {exc}")
        return results, False


def _safe_scan_float_text(value, fmt: str = "{:.2f}") -> str:
    try:
        return fmt.format(float(value))
    except (TypeError, ValueError):
        return str(value)


def _format_scan_row(row_data: dict) -> dict:
    formatted = {
        "代码": str(row_data.get("代码", "")),
        "名称": str(row_data.get("名称", "")),
        "现价": _safe_scan_float_text(row_data.get("收盘", 0)),
        "涨幅%": "--",  # Historical static scan lacks intraday % change originally
        "触发日期": str(row_data.get("触发日期", "")),
        "评分": str(row_data.get("评分", "")),
        "RPS强度": str(row_data.get("RPS强度", "")),
        "市值": str(row_data.get("市值", "")),
        "距突破": str(row_data.get("距突破", "")),
        "突破状态": str(row_data.get("突破状态", "")),
        "区间振幅": str(row_data.get("区间振幅", "")),
        "热门板块": str(row_data.get("热点板块", "-")),
        "_suppress_accent_rail": True,
    }
    formatted.update({key: value for key, value in row_data.items() if key not in formatted})
    return formatted


def _commit_scan_render(owner, formatted_rows: list[dict]) -> None:
    result = owner._describe_scan_rows(formatted_rows)
    owner._last_scan_result = result
    if result.signature != owner._last_scan_signature:
        owner.source_model.update_data(result.rows)
        owner._last_scan_signature = result.signature
    owner._apply_latest_quotes_from_store()
    if not owner._scan_cache_preload.committing:
        owner._prime_visible_local_quote_snapshot(owner.source_model)
    if hasattr(owner, "table_state"):
        owner.table_state.show_table()
    owner._refresh_scan_status()


class _ScanCacheLifecycleMixin:
    """扫描缓存的后台读取、延迟呈现与持久化边界。"""

    def _save_scan_cache(self, results: list):
        try:
            params_snapshot = {
                "rps": self.spn_scan_rps.value(),
                "amp": self.spn_scan_amp.value(),
                "ma_bind": self.spn_scan_ma_bind.value(),
                "amount": self.spn_scan_amount.value(),
                "high250": self.spn_scan_high250.value(),
            }
            status = save_scan_cache(results, params_snapshot)
            if status == "cleared":
                log.info("[扫描缓存] 本次扫描无结果，已清空旧缓存")
                return
            log.info(f"[扫描缓存] 已保存 {len(results)} 条结果至 SQLite")
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.error(f"[扫描缓存] 保存失败: {exc}")

    def _load_scan_cache(self, *, force: bool = False) -> bool:
        if getattr(self, "_shutting_down", False):
            return False
        preload = self._scan_cache_preload
        if preload.started and not preload.done:
            return False
        if preload.done and not force:
            return False

        from app.services.ui_task_service import background_job_runner as task_manager
        from app.services.ui_task_service import task_registry

        lifecycle = getattr(self, "_task_lifecycle", None)
        if lifecycle is None:
            lifecycle = TaskLifecycleGroup()
            self._task_lifecycle = lifecycle
        preload.started = True
        preload.done = False
        preload.committed = False
        preload.deferred_payload = None
        preload.prepared_rows = None
        preload.committing = False
        task_key = task_registry.workspace("scan_cache_load", description="Load cached scan results")
        lifecycle.run_background(
            "scan_cache_load",
            _load_scan_cache_snapshot,
            task_id=task_key,
            timeout_sec=15.0,
            on_success=self._on_scan_cache_loaded,
            on_error=self._on_scan_cache_load_error,
            runner=task_manager,
        )
        return True

    def _on_scan_cache_loaded(self, payload) -> None:
        if getattr(self, "_shutting_down", False):
            return
        try:
            cache_data, migrated, *prepared_payload = payload
            if migrated:
                log.info("[扫描缓存] 旧版的 scan_cache.json 已自动迁移入 SQLite")
            if not isinstance(cache_data, dict):
                self._commit_empty_scan_cache()
                return
            results = cache_data.get("results", [])
            if not isinstance(results, list) or not results:
                self._commit_empty_scan_cache()
                return

            prepared_rows = prepared_payload[0] if prepared_payload else _prepare_scan_cache_rows(results)
            plain_results = self._refresh_scan_result_names(prepared_rows, refresh_missing=False)
            preload = self._scan_cache_preload
            preload.prepared_rows = plain_results
            preload.skip_visible_quote_prime = not self.isVisible()
            self._apply_scan_cache_payload(cache_data, plain_results)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._on_scan_cache_load_error(str(exc))
        finally:
            self._scan_cache_preload.done = True

    def _on_scan_cache_load_error(self, error_message: str) -> None:
        self._scan_cache_preload.done = True
        if not getattr(self, "_shutting_down", False):
            event_bus.sig_system_log.emit("error", f"[扫描缓存] 加载失败: {error_message}")

    def _commit_empty_scan_cache(self) -> None:
        preload = self._scan_cache_preload
        preload.committing = True
        try:
            _render_empty_scan_table(self)
            preload.committed = True
        finally:
            preload.committing = False

    def _apply_deferred_scan_cache(self) -> None:
        if getattr(self, "_shutting_down", False) or not self.isVisible():
            return
        preload = self._scan_cache_preload
        payload = preload.deferred_payload
        if payload is None:
            return
        preload.deferred_payload = None
        self._apply_scan_cache_payload(*payload)

    def showEvent(self, event):
        super().showEvent(event)
        if self._scan_cache_preload.deferred_payload is not None:
            QTimer.singleShot(0, self._apply_deferred_scan_cache)

    def _apply_scan_cache_payload(self, cache_data: dict, results: list):
        if getattr(self, "_shutting_down", False):
            return
        try:
            saved_at = cache_data.get("saved_at", "未知")
            self._current_results = results
            preload = self._scan_cache_preload
            preload.committed = False
            preload.committing = True
            try:
                if self._render_scan_table(results) is False:
                    return
                preload.committed = True
            finally:
                preload.committing = False

            params_info = cache_data.get("params")
            params_hint = ""
            if params_info and isinstance(params_info, dict):
                params_hint = (
                    f" | RPS≥{params_info.get('rps', '?')}"
                    f" 振幅≤{int(params_info.get('amp', 0) * 100)}%"
                    f" 均线粘合≤{int(params_info.get('ma_bind', 0) * 100)}%"
                )
            event_bus.sig_system_log.emit(
                "info", f"[扫描缓存] 已加载 {len(results)} 条记录 (保存于 {saved_at[:16]}){params_hint}"
            )
            ui_signals.sig_task_progress.emit("scan", 100, f"已加载 {len(results)} 条扫描缓存")
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            event_bus.sig_system_log.emit("error", f"[扫描缓存] 加载失败: {exc}")

    def prime_background_load(self) -> bool:
        """只读取本地扫描缓存；与构造器延时任务共享同一次加载状态。"""
        return self._load_scan_cache()

    def is_background_preload_complete(self) -> bool:
        preload = self._scan_cache_preload
        return bool(
            preload.done
            and preload.committed
            and preload.deferred_payload is None
            and preload.prepared_rows is None
            and not preload.committing
        )

    def cancel_background_preload(self, *, reason: str):
        def _reset() -> None:
            initial_timer = getattr(self, "_initial_cache_load_timer", None)
            if initial_timer is not None:
                initial_timer.stop()
            preload = self._scan_cache_preload
            preload.started = False
            preload.done = False
            preload.committed = False
            preload.deferred_payload = None
            preload.prepared_rows = None
            preload.committing = False

        from app.services.ui_task_service import background_job_runner as task_manager
        from app.services.ui_task_service import task_registry

        return cancel_background_preload_tasks(
            self,
            lifecycle_names=("scan_cache_load",),
            task_ids=(task_registry.workspace("scan_cache_load"),),
            reason=reason,
            reset_state=_reset,
            local_settled=lambda: not self._scan_cache_preload.committing,
            runner=task_manager,
        )

    def on_workspace_tab_activated(self) -> None:
        preload = self._scan_cache_preload
        if not preload.started and not preload.done:
            self._load_scan_cache()
        self._apply_deferred_scan_cache()


class ScanTab(_ScanCacheLifecycleMixin, BaseStockTab):
    """
    静态扫描 (VCP 区间扫描) 独立组件
    包含扫描渲染、策略表格、本地JSON缓存，并通过事件总线驱动进度。
    """

    AUTO_F5_INCREMENTAL_SCAN_DATE_KEY = "last_auto_incremental_after_f5_date"
    F5_AUTO_INCREMENTAL_DELAY_MS = 8000
    SCAN_TIMEOUT_SEC = 30 * 60

    def __init__(self, data_provider, engine, parent=None, *, initial_cache_load_delay_ms: int = 300):
        super().__init__(data_provider=data_provider, parent=parent)
        self.engine = engine
        try:
            self._initial_cache_load_delay_ms = max(0, int(initial_cache_load_delay_ms))
        except (TypeError, ValueError):
            self._initial_cache_load_delay_ms = 300
        self._current_results = []
        self.worker = None
        self._task_lifecycle = TaskLifecycleGroup()
        self._scan_token = None
        self._scan_cancel_requested = False
        self._scan_mode = "full"
        self._scan_target_date = ""
        self._last_incremental_stats = None
        self._scan_lineage_service = create_tab_lineage_service(
            "scan",
            provider_status_reader=self._read_provider_status,
        )
        self._last_scan_result = None
        self._last_scan_signature = ""
        self._shutting_down = False
        self._scan_cache_preload = _ScanCachePreloadState()
        self._f5_incremental = build_scan_f5_incremental_coordinator(
            self,
            delay_ms=self.F5_AUTO_INCREMENTAL_DELAY_MS,
            settings_key=self.AUTO_F5_INCREMENTAL_SCAN_DATE_KEY,
        )
        self._f5_auto_incremental_timer = self._f5_incremental.timer

        self._init_settings_widgets()
        self._init_ui()

        # 启动时自动加载上次缓存的扫描结果；owned timer 可在超时/退出时取消。
        self._initial_cache_load_timer = QTimer(self)
        self._initial_cache_load_timer.setSingleShot(True)
        self._initial_cache_load_timer.timeout.connect(self._load_scan_cache)
        self._initial_cache_load_timer.start(self._initial_cache_load_delay_ms)

        # 情报源只消费 F5/本地快照，不加入盘中实时行情轮询。
        event_bus.sig_cache_reload_completed.connect(self._on_cache_reload_completed)

    def _init_settings_widgets(self):
        """初始化扫描策略的内部存储控件，从 QSettings 恢复上次参数 (#8)"""
        self._settings = app_config.section("scan", legacy_scope="ScanTab")

        self.spn_scan_rps = QSpinBox()
        self.spn_scan_rps.setRange(50, 99)
        self.spn_scan_rps.setValue(self._settings.value("rps_threshold", 80, type=int))

        self.spn_scan_amp = QDoubleSpinBox()
        self.spn_scan_amp.setRange(0.1, 1.5)
        self.spn_scan_amp.setSingleStep(0.05)
        self.spn_scan_amp.setValue(self._settings.value("amp_threshold", 0.45, type=float))

        self.spn_scan_ma_bind = QDoubleSpinBox()
        self.spn_scan_ma_bind.setRange(0.01, 0.2)
        self.spn_scan_ma_bind.setSingleStep(0.01)
        self.spn_scan_ma_bind.setValue(self._settings.value("ma_bind_threshold", 0.05, type=float))

        self.spn_scan_amount = QDoubleSpinBox()
        self.spn_scan_amount.setRange(0.1, 50.0)
        self.spn_scan_amount.setSingleStep(0.5)
        self.spn_scan_amount.setValue(self._settings.value("min_amount", 0.8, type=float))

        self.spn_scan_high250 = QDoubleSpinBox()
        self.spn_scan_high250.setRange(0.01, 1.0)
        self.spn_scan_high250.setSingleStep(0.05)
        self.spn_scan_high250.setValue(self._settings.value("high_250_threshold", 0.10, type=float))

    def _get_scan_params(self) -> dict:
        return {
            "rps": int(self.spn_scan_rps.value()),
            "amp": float(self.spn_scan_amp.value()),
            "ma_bind": float(self.spn_scan_ma_bind.value()),
            "amount": float(self.spn_scan_amount.value()),
            "high250": float(self.spn_scan_high250.value()),
        }

    def _apply_scan_params(self, params: dict):
        self.spn_scan_rps.setValue(int(params.get("rps", 80)))
        self.spn_scan_amp.setValue(float(params.get("amp", 0.45)))
        self.spn_scan_ma_bind.setValue(float(params.get("ma_bind", 0.05)))
        self.spn_scan_amount.setValue(float(params.get("amount", 0.8)))
        self.spn_scan_high250.setValue(float(params.get("high250", 0.10)))

    def _save_scan_params(self):
        params = self._get_scan_params()
        self._settings.setValue("rps_threshold", params["rps"])
        self._settings.setValue("amp_threshold", params["amp"])
        self._settings.setValue("ma_bind_threshold", params["ma_bind"])
        self._settings.setValue("min_amount", params["amount"])
        self._settings.setValue("high_250_threshold", params["high250"])
        self._settings.sync()

    def _load_user_presets(self) -> dict:
        user_presets_raw = self._settings.value("user_presets", "{}")
        try:
            return json.loads(user_presets_raw) if isinstance(user_presets_raw, str) else {}
        except (json.JSONDecodeError, TypeError) as exc:
            log.debug(f"[扫描参数] 用户预设解析失败: {exc}")
            return {}

    def _save_user_presets(self, user_presets: dict):
        self._settings.setValue("user_presets", json.dumps(user_presets, ensure_ascii=False))
        self._settings.sync()

    def _scan_param_segments(self) -> tuple[str, str, str]:
        params = self._get_scan_params()
        return (
            f"RPS≥{params['rps']}",
            f"振幅≤{int(params['amp'] * 100)}%",
            f"均线≤{int(params['ma_bind'] * 100)}%",
        )

    def _latest_scan_trigger_date(self) -> str:
        dates = [
            str(item.get("触发日期", "")).strip() for item in (self._current_results or []) if isinstance(item, dict)
        ]
        dates = [item for item in dates if item]
        return max(dates) if dates else ""

    def _describe_scan_rows(self, rows: list[dict]):
        warnings = []
        if not rows:
            warnings.append("scan_rows_empty")
        return self._scan_lineage_service.describe(
            rows,
            trade_date=self._latest_scan_trigger_date(),
            triggered_network=False,
            warnings=warnings,
            extra={
                "scan_mode": self._scan_mode,
                "scan_target_date": self._scan_target_date,
            },
        )

    def get_data_lineage(self) -> dict:
        result = self._last_scan_result
        if result is None:
            rows = list(getattr(self.source_model, "row_data", None) or [])
            result = self._describe_scan_rows(rows)
            self._last_scan_result = result
            self._last_scan_signature = result.signature
        return result.lineage.as_dynamic_dict()

    @staticmethod
    def _normalize_scan_date(value) -> str:
        text = str(value or "").strip().replace("-", "").replace("/", "")
        return text[:8] if len(text) >= 8 else text

    def _infer_cache_latest_trade_date(self) -> str:
        latest_date = ""
        cache_data = getattr(self.data_provider, "cache_data", {}) or {}
        for df in cache_data.values():
            if df is None:
                continue
            try:
                if len(df) <= 0:
                    continue
            except (AttributeError, TypeError, ValueError):
                continue

            try:
                index = getattr(df, "index", None)
                if index is None or len(index) <= 0:
                    continue
                last_value = index[-1]
            except (AttributeError, IndexError, KeyError, TypeError, ValueError):
                continue

            try:
                if hasattr(last_value, "strftime"):
                    date_str = last_value.strftime("%Y%m%d")
                else:
                    date_str = str(last_value).strip().replace("-", "")[:8]
                if len(date_str) == 8 and date_str.isdigit() and date_str > latest_date:
                    latest_date = date_str
            except (AttributeError, TypeError, ValueError):
                continue
        return latest_date

    def _resolve_incremental_scan_date(self) -> str:
        latest_cache_date = self._infer_cache_latest_trade_date()
        if latest_cache_date:
            return f"{latest_cache_date[:4]}-{latest_cache_date[4:6]}-{latest_cache_date[6:]}"

        trade_date = MarketCalendar.get_latest_trade_date("CN")
        if trade_date is None:
            trade_date = MarketCalendar.today("CN")
        return trade_date.isoformat()

    def _merge_scan_results(self, base_results: list, incoming_results: list) -> tuple[list, dict]:
        merged: dict[str, dict] = {}
        for row in base_results or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("代码", "")).strip()
            if code:
                merged[code] = dict(row)

        stats = {"原始命中": len(incoming_results or []), "新增": 0, "更新": 0, "刷新": 0, "忽略": 0}
        for row in incoming_results or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("代码", "")).strip()
            if not code:
                continue

            candidate = dict(row)
            current = merged.get(code)
            if current is None:
                merged[code] = candidate
                stats["新增"] += 1
                continue

            current_date = self._normalize_scan_date(current.get("触发日期", ""))
            candidate_date = self._normalize_scan_date(candidate.get("触发日期", ""))
            if candidate_date >= current_date:
                merged[code] = candidate
                if candidate_date > current_date:
                    stats["更新"] += 1
                else:
                    stats["刷新"] += 1
            else:
                stats["忽略"] += 1

        return list(merged.values()), stats

    def _refresh_scan_result_names(self, results: list, *, refresh_missing: bool = True) -> list:
        normalized_rows = []
        codes = []
        for row in results or []:
            if not isinstance(row, dict):
                normalized_rows.append(row)
                continue
            cloned = dict(row)
            normalized_rows.append(cloned)
            code = str(cloned.get("代码", "")).strip()
            if code:
                codes.append(code)

        if not normalized_rows or not self.data_provider or not codes:
            return normalized_rows

        try:
            name_map = self.data_provider.ensure_code_name_map(
                list(dict.fromkeys(codes)),
                refresh_missing=refresh_missing,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.debug(f"[扫描结果] 名称回填失败: {exc}")
            name_map = getattr(self.data_provider, "code2name", {}) or {}

        refreshed = 0
        for row in normalized_rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("代码", "")).strip()
            resolved = str((name_map or {}).get(code, "") or "").strip()
            if code and resolved and resolved != code and str(row.get("名称", "")).strip() != resolved:
                row["名称"] = resolved
                refreshed += 1

        if refreshed:
            log.info(f"[扫描结果] 已回填 {refreshed} 条名称")
        return normalized_rows

    def _build_incremental_finish_message(self) -> str:
        stats = self._last_incremental_stats or {}
        target_date = self._scan_target_date or self._resolve_incremental_scan_date()
        current_count = len(self._pending_scan_results or [])
        raw_hits = int(stats.get("原始命中", 0) or 0)
        if raw_hits <= 0:
            return f"新增补扫完成: {target_date} 无新增信号，当前 {current_count} 只"

        updated = int(stats.get("更新", 0) or 0) + int(stats.get("刷新", 0) or 0)
        return (
            f"新增补扫完成: {target_date} 命中 {raw_hits} 条，"
            f"新增 {int(stats.get('新增', 0) or 0)} 只，更新 {updated} 只，当前 {current_count} 只"
        )

    def _refresh_scan_status(self, primary: str | None = None):
        search_text = self.scan_search.text().strip() if hasattr(self, "scan_search") else ""
        extra_segments = list(self._scan_param_segments())
        if self._current_results:
            latest_date = self._latest_scan_trigger_date()
            self.lbl_scan_status.setText(
                self.format_workspace_status(
                    primary or "扫描结果已就绪",
                    result=f"{self.proxy_model.rowCount()}/{len(self._current_results)}只",
                    freshness=f"快照 {latest_date}" if latest_date else "快照待更新",
                    current_filter=search_text or "全部",
                    next_step="",
                    extra_segments=extra_segments,
                )
            )
            return

        self.lbl_scan_status.setText(
            self.format_workspace_status(
                primary or "等待扫描",
                result="0只",
                freshness=self._scan_target_date or "待扫描",
                current_filter=search_text or "全部",
                next_step="点击开始扫描或新增补扫",
                extra_segments=extra_segments,
            )
        )

    def _set_scan_action_state(self, state: str):
        is_incremental = self._scan_mode == "incremental"
        if state == "running":
            if hasattr(self, "btn_scan_increment"):
                self.btn_scan_increment.setText("停止补扫" if is_incremental else "新增补扫")
                self.btn_scan_increment.setEnabled(is_incremental)
            self.btn_scan_action.setText("开始扫描" if is_incremental else "停止扫描")
            self.btn_scan_action.setEnabled(not is_incremental)
            self.lbl_scan_status.setText(
                self.format_workspace_status(
                    "新增补扫中" if is_incremental else "扫描进行中",
                    result=f"{self.proxy_model.rowCount()}/{len(self._current_results)}只",
                    freshness=f"手动回补 {self._scan_target_date}" if is_incremental else "实时计算",
                    current_filter=self.scan_search.text().strip() or "全部",
                    next_step="等待结果落表",
                    extra_segments=self._scan_param_segments(),
                )
            )
            if hasattr(self, "table_state"):
                if is_incremental and self.source_model.rowCount() > 0:
                    self.table_state.show_table()
                else:
                    self.table_state.show_loading(
                        "新增补扫中..." if is_incremental else "扫描中...",
                        f"正在补扫 {self._scan_target_date}"
                        if is_incremental and self._scan_target_date
                        else "正在计算候选信号",
                    )
        elif state == "stopping":
            if hasattr(self, "btn_scan_increment"):
                self.btn_scan_increment.setText("正在停止补扫..." if is_incremental else "新增补扫")
                self.btn_scan_increment.setEnabled(False)
            self.btn_scan_action.setText("开始扫描" if is_incremental else "正在停止...")
            self.btn_scan_action.setEnabled(False)
            self.lbl_scan_status.setText(
                self.format_workspace_status(
                    "正在停止扫描",
                    result=f"{self.proxy_model.rowCount()}/{len(self._current_results)}只",
                    freshness=f"手动回补 {self._scan_target_date}" if is_incremental else "扫描中断",
                    current_filter=self.scan_search.text().strip() or "全部",
                    next_step="保留已完成结果",
                )
            )
            if hasattr(self, "table_state"):
                if is_incremental and self.source_model.rowCount() > 0:
                    self.table_state.show_table()
                else:
                    self.table_state.show_loading("正在终止...", "正在收尾")
        else:
            self.btn_scan_action.setText("开始扫描")
            self.btn_scan_action.setEnabled(True)
            if hasattr(self, "btn_scan_increment"):
                self.btn_scan_increment.setText("新增补扫")
                self.btn_scan_increment.setEnabled(True)
            self._refresh_scan_status()
            if hasattr(self, "table_state"):
                if self.source_model.rowCount() > 0:
                    self.table_state.show_table()
                else:
                    self.table_state.show_empty("暂无扫描结果")

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 统一工具条：标题 + 副标题 + 过滤区 + 主操作
        self.lbl_scan_status = QLabel()

        self.scan_search = QLineEdit()
        self.scan_search.setPlaceholderText("筛选代码或名称...")
        self.scan_search.setAccessibleName("VCP 扫描筛选")
        self.scan_search.setAccessibleDescription("按代码或名称筛选扫描结果")
        self.scan_search.setMinimumWidth(180)
        self.scan_search.setMaximumWidth(280)
        self.scan_search.textChanged.connect(self._on_search_text_changed)

        filter_widgets = [self.scan_search]

        self.btn_scan_action = QPushButton("开始扫描")
        self.btn_scan_action.setObjectName("primaryButton")
        self.btn_scan_action.setProperty("toolbarWidthHints", ["开始扫描", "停止扫描", "正在停止..."])
        self.btn_scan_action.clicked.connect(self._on_scan_action_clicked)

        self.btn_scan_increment = QPushButton("新增补扫")
        self.btn_scan_increment.setProperty("class", "secondary")
        self.btn_scan_increment.setProperty("toolbarWidthHints", ["新增补扫", "停止补扫", "正在停止补扫..."])
        self.btn_scan_increment.setToolTip("只扫描最近可用交易日，并将结果追加/刷新到当前表格")
        self.btn_scan_increment.clicked.connect(self.run_incremental_scan)

        # 扫描参数设置按钮
        self.btn_scan_settings = QToolButton()
        self.btn_scan_settings.setText("参数")
        self.btn_scan_settings.setAccessibleName("VCP 扫描参数设置")
        self.btn_scan_settings.setProperty("class", "toolbarGhost")
        self.btn_scan_settings.setProperty("toolbarOverflow", True)
        self.btn_scan_settings.setMinimumWidth(56)
        self.btn_scan_settings.setAutoRaise(False)
        self.btn_scan_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_scan_settings.setToolTip("VCP扫描参数设置")
        self.btn_scan_settings.clicked.connect(self.open_scan_settings)

        action_widgets = [self.btn_scan_action, self.btn_scan_increment, self.btn_scan_settings]
        toolbar = self.build_tab_toolbar("VCP 扫描", self.lbl_scan_status, filter_widgets, action_widgets)
        layout.addWidget(toolbar)
        self._refresh_scan_status()

        # 表格控件 (MVC)
        self.columns = [
            "代码",
            "名称",
            "现价",
            "涨幅%",
            "市值",
            "触发日期",
            "评分",
            "RPS强度",
            "距突破",
            "突破状态",
            "区间振幅",
            "热门板块",
        ]
        self.source_model = StockTableModel(self.columns)
        self.source_model.set_plain_style_headers(["触发日期"])
        self.source_model.set_muted_text_headers(
            ["触发日期", "评分", "RPS强度", "距突破", "突破状态", "区间振幅", "热门板块"]
        )
        self.proxy_model = RtSortFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.source_model)

        self.table_scan = VCPTableView(default_row_height=30)
        self.table_scan.setModel(self.proxy_model)
        self.table_scan.setItemDelegate(StockItemDelegate(self.table_scan))
        self.table_state = TableStateWrapper(self.table_scan, empty_title="暂无扫描结果", loading_title="扫描中...")

        # 绑定双击事件:广播调取K线图信号，带上前后文以便K线图能够「上一只」「下一只」滑动
        self.table_scan.doubleClicked.connect(self._handle_show_kline)

        # 右键菜单
        self.table_scan.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_scan.customContextMenuRequested.connect(self._show_context_menu)

        # 挂载快捷键: 拦截回车与空格模拟双击
        original_keypress = self.table_scan.keyPressEvent

        def table_key_press(event):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                curr_idx = self.table_scan.currentIndex()
                if curr_idx.isValid():
                    self._handle_show_kline(curr_idx)
                event.accept()
            else:
                original_keypress(event)

        self.table_scan.keyPressEvent = table_key_press

        # 列宽策略 (QSettings 持久化)
        header = self.table_scan.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionsClickable(True)
        self.table_scan.setSortingEnabled(True)

        self.apply_table_column_preset(
            self.table_scan,
            [64, 78, 72, 72, 88, 92, 76, 88, 90, 96, 100, 172],
            stretch_last=False,
        )
        header.setSectionResizeMode(self.source_model.columnCount() - 1, QHeaderView.ResizeMode.Stretch)

        # 绑定防抖自动保存与恢复配置；没有历史排序时才走默认触发日期降序
        restored_sort = self.bind_header_persistence(self.table_scan, "header_state_scan_v3")
        if not restored_sort:
            self.table_scan.sortByColumn(self.source_model.headers.index("触发日期"), Qt.SortOrder.DescendingOrder)

        layout.addWidget(self.table_state)

    def _handle_show_kline(self, index=None):
        if index is None or not index.isValid():
            return
        model = index.model()
        row = index.row()
        code_col = self.source_model.headers.index("代码")
        name_col = self.source_model.headers.index("名称")
        code_idx = model.index(row, code_col)
        current_code = model.data(code_idx, Qt.ItemDataRole.DisplayRole)
        if not current_code:
            return

        code_list = []
        visual_rows = []

        # 构建当前经过筛选后(未隐藏)的股票列表，支持在K线中翻页
        for r in range(model.rowCount()):
            c_code = model.data(model.index(r, code_col), Qt.ItemDataRole.DisplayRole)
            c_name = model.data(model.index(r, name_col), Qt.ItemDataRole.DisplayRole)
            if c_code:
                # Pull full user dict for that row
                source_idx = self.proxy_model.mapToSource(model.index(r, code_col))
                row_data = self.source_model.get_row_data(source_idx.row()) if source_idx.isValid() else None
                if isinstance(row_data, dict):
                    row_data = dict(row_data)
                    row_data.setdefault("代码", c_code)
                    row_data.setdefault("名称", c_name)
                if not isinstance(row_data, dict):
                    row_data = {"代码": c_code, "名称": c_name}

                code_list.append(row_data)
                visual_rows.append(r)

        try:
            current_idx = visual_rows.index(row)
            ui_signals.sig_show_kline_with_list.emit(current_code, code_list, current_idx)
        except ValueError:
            ui_signals.sig_show_kline.emit(current_code)

    def _on_search_text_changed(self, text):
        self.set_proxy_filter_text(self.proxy_model, text)

    def _on_scan_action_clicked(self):
        if self.worker and self.worker.isRunning():
            self.cancel_scan()
            return

        from ui.components.scan_dialogs import VCPScanRangeDialog

        dlg = VCPScanRangeDialog(self)
        if dlg.exec() != VCPScanRangeDialog.DialogCode.Accepted:
            return

        start_date, end_date = dlg.selected_range()
        self.start_scan(start_date, end_date, merge_mode=False)

    def _on_incremental_scan_clicked(self) -> bool:
        if self.worker and self.worker.isRunning():
            return self.cancel_scan()

        target_date = self._resolve_incremental_scan_date()
        return self.start_scan(target_date, target_date, merge_mode=True)

    def _show_scan_settings(self):
        from ui.components.scan_dialogs import VCPScanSettingsDialog

        dlg = VCPScanSettingsDialog(self._get_scan_params(), self._load_user_presets(), self)
        if dlg.exec() != VCPScanSettingsDialog.DialogCode.Accepted:
            return

        self._apply_scan_params(dlg.values())
        self._save_scan_params()
        self._save_user_presets(dlg.user_presets())
        show_toast("VCP 扫描参数已保存", "success", self)
        if not (self.worker and self.worker.isRunning()):
            self._refresh_scan_status()

    def get_scan_results(self) -> list[dict]:
        return list(self._current_results or [])

    def run_incremental_scan(self) -> bool:
        return self._on_incremental_scan_clicked()

    def run_auto_incremental_scan_after_f5(self) -> bool:
        return self._f5_incremental.run_now()

    def schedule_auto_incremental_scan_after_f5(self) -> bool:
        return self._f5_incremental.schedule()

    def _run_pending_auto_incremental_scan_after_f5(self) -> bool:
        return self._f5_incremental.run_pending()

    def refresh_data_after_f5(self) -> bool:
        if getattr(self, "_shutting_down", False):
            return False
        self._scan_cache_preload.started = False
        self._scan_cache_preload.done = False
        self._load_scan_cache()
        self.refresh_table_from_latest_snapshot(current_model=self.source_model, async_local=True)
        return self.schedule_auto_incremental_scan_after_f5()

    def open_scan_settings(self) -> bool:
        self._show_scan_settings()
        return True

    def get_realtime_quote_codes(self, current_model=None) -> set[str]:
        """VCP 扫描不向中央报价站贡献代码，避免盘中触发联网补价。"""
        return set()

    def _apply_latest_quotes_from_store(self):
        self._apply_quote_store_snapshot(current_model=self.source_model)

    def _prime_visible_local_quote_snapshot(self, current_model=None) -> bool:
        if self._scan_cache_preload.skip_visible_quote_prime:
            self._scan_cache_preload.skip_visible_quote_prime = False
            return False
        return super()._prime_visible_local_quote_snapshot(current_model)

    def _on_cache_reload_completed(self):
        self._apply_latest_quotes_from_store()

    # ==========================
    # 核心引擎调度与任务生命周期
    # ==========================
    def start_scan(self, sd: str, ed: str, merge_mode: bool = False) -> bool:
        if getattr(self, "_shutting_down", False):
            return False
        if self.worker is not None and self.worker.isRunning():
            return False

        sd = _format_worker_scan_date(sd)
        ed = _format_worker_scan_date(ed)

        self._scan_mode = "incremental" if merge_mode else "full"
        self._scan_target_date = ed if merge_mode else f"{sd} ~ {ed}"
        self._last_incremental_stats = None
        self._scan_cancel_requested = False
        self._set_scan_action_state("running")
        ui_signals.sig_task_progress.emit("scan", 1, "准备新增补扫..." if merge_mode else "准备扫描...")
        self._pending_scan_results = None

        params = _scan_worker_params(self)
        self._save_scan_params()
        _begin_scan_lifecycle(self)
        _start_scan_worker(self, sd, ed, params)
        return True

    def cancel_scan(self):
        if self.worker and self.worker.isRunning() and not self._scan_cancel_requested:
            self._scan_cancel_requested = True
            self._set_scan_action_state("stopping")
            lifecycle = getattr(self, "_task_lifecycle", None)
            if lifecycle is not None:
                lifecycle.cancel("scan", reason="user_cancelled")
            self.worker.cancel()
            return True
        return False

    def shutdown(self) -> None:
        if getattr(self, "_shutting_down", False):
            return
        self._shutting_down = True
        initial_timer = getattr(self, "_initial_cache_load_timer", None)
        if initial_timer is not None:
            initial_timer.stop()
        self._scan_cache_preload.done = True
        self._scan_cache_preload.deferred_payload = None
        self._scan_cache_preload.prepared_rows = None
        self._f5_incremental.shutdown()
        if self.worker is not None:
            request_thread_shutdown(
                self.worker,
                label="Scan worker",
                stop=self.cancel_scan,
                timeout_ms=2000,
                logger=log,
            )
        lifecycle = getattr(self, "_task_lifecycle", None)
        if lifecycle is not None:
            lifecycle.shutdown(timeout_ms=2000)

    def _cleanup_runtime_state(self):
        self.shutdown()
        super()._cleanup_runtime_state()

    def _on_scan_finished(self, success, msg):
        if success:
            self._save_scan_cache(self._pending_scan_results or [])
        final_msg = self._build_incremental_finish_message() if success and self._scan_mode == "incremental" else msg
        ui_signals.sig_task_progress.emit("scan", 100 if success else 0, final_msg)

    def _on_worker_thread_finished(self):
        lifecycle = getattr(self, "_task_lifecycle", None)
        token = getattr(self, "_scan_token", None)
        if lifecycle is not None and token is not None:
            lifecycle.complete("scan", token)
        self._scan_token = None
        if self.worker:
            self.worker.deleteLater()
            self.worker = None
        self._scan_cancel_requested = False
        self._set_scan_action_state("idle")
        self._scan_mode = "full"
        self._scan_target_date = ""

    def _on_scan_results(self, results):
        incoming_results = results or []
        if self._scan_mode == "incremental":
            merged_results, merge_stats = self._merge_scan_results(self._current_results, incoming_results)
            self._last_incremental_stats = merge_stats
            self._pending_scan_results = self._refresh_scan_result_names(merged_results)
        else:
            self._pending_scan_results = self._refresh_scan_result_names(incoming_results)
        self._current_results = self._pending_scan_results
        self._render_scan_table(self._pending_scan_results)
        event_bus.sig_scan_updated.emit()

    # ==========================
    # 数据渲染逻辑
    # ==========================
    def _render_scan_table(self, results):
        if not results:
            _render_empty_scan_table(self)
            return True

        preload = self._scan_cache_preload
        if results is preload.prepared_rows:
            final_list = list(results)
            preload.prepared_rows = None
            self._current_results = final_list
        else:
            final_list, normalized = _normalize_scan_render_rows(results)
            if normalized:
                self._current_results = final_list

        try:
            formatted_rows = [_format_scan_row(row_data) for row_data in final_list]
            _commit_scan_render(self, formatted_rows)
            return True
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            event_bus.sig_system_log.emit("error", f"渲染表格错误: {exc}")
            return False

    # ==========================
    # 右键菜单
    # ==========================
    def _show_context_menu(self, pos):
        """扫描结果表格右键菜单 — 委托给统一菜单工厂"""
        index = self.table_scan.indexAt(pos)
        if not index.isValid():
            return

        model = index.model()
        row = index.row()
        code_col = self.source_model.headers.index("代码")
        name_col = self.source_model.headers.index("名称")
        code = model.data(model.index(row, code_col), Qt.ItemDataRole.DisplayRole)
        name = model.data(model.index(row, name_col), Qt.ItemDataRole.DisplayRole)
        if not code or not name:
            return

        # 提取 VCP 数据用于关注池附带信息
        source_idx = self.proxy_model.mapToSource(model.index(row, code_col))
        vcp_data = self.source_model.get_row_data(source_idx.row()) if source_idx.isValid() else None
        if not isinstance(vcp_data, dict):
            vcp_data = None

        from ui.components.stock_context_menu import build_stock_context_menu

        build_stock_context_menu(
            self,
            code,
            name,
            vcp_data=vcp_data,
        )

    # _launch_tdx 已迁移至 BaseStockTab 基类
