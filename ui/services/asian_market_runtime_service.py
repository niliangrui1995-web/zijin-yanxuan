# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime as dt
import json
import os
import time

from PyQt6.QtCore import QObject, pyqtSignal

from app.services.asian_market_service import filter_asian_tickers, sync_asian_kline_cache
from app.services.ui_event_service import domain_events as event_bus
from app.services.ui_market_calendar_service import MarketCalendar
from core.logger import get_logger
from ui.components.thread_shutdown import request_thread_shutdown
from ui.tabs.asian_market_workers import (
    GLOBAL_ASIAN_RT_CACHE,
    JSON_CACHE,
    RT_JSON_CACHE,
    AsianMarketWorker,
    is_asian_quote_refresh_time,
)

log = get_logger(__name__)


def _safe_float(value) -> float:
    try:
        return float(str(value).replace("%", ""))
    except (TypeError, ValueError):
        return 0.0


def _round_pct(value) -> float:
    return round(_safe_float(value), 2)


class AsianMarketRuntimeService(QObject):
    sig_progress = pyqtSignal(str)
    sig_rt_update = pyqtSignal(dict)
    sig_runtime_state_changed = pyqtSignal(object)

    def __init__(self, parent=None, *, worker_factory=None):
        super().__init__(parent)
        self._worker_factory = worker_factory or AsianMarketWorker
        self._worker = None
        self._runtime_state = "idle"
        self._codes: list[str] = []
        self._last_success_at: dt.datetime | None = None
        self._last_error = ""
        self._auto_refresh_deferred_until = 0.0
        self._auto_refresh_defer_reason = ""

    @property
    def runtime_state(self) -> str:
        return self._runtime_state

    @property
    def last_success_at(self) -> dt.datetime | None:
        return self._last_success_at

    @property
    def last_error(self) -> str:
        return self._last_error

    def current_worker(self):
        return self._worker

    def target_codes(self) -> list[str]:
        worker_codes = [
            str(code).strip()
            for code in getattr(getattr(self, "_worker", None), "codes", []) or []
            if str(code).strip()
        ]
        if worker_codes:
            return list(dict.fromkeys(worker_codes))
        if self._codes:
            return list(dict.fromkeys(self._codes))
        try:
            target_map = filter_asian_tickers() or {}
            self._codes = [str(code).strip() for code in target_map.values() if str(code).strip()]
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning(f"[亚洲市场] 读取目标池失败: {exc}")
            self._codes = []
        return list(dict.fromkeys(self._codes))

    def set_target_codes(self, codes) -> None:
        normalized = [str(code).strip() for code in (codes or []) if str(code).strip()]
        if normalized:
            self._codes = list(dict.fromkeys(normalized))
        worker = self._worker
        if worker is not None:
            try:
                worker.codes = self.target_codes()
            except RuntimeError:
                pass

    def is_running(self) -> bool:
        worker = self._worker
        try:
            return bool(worker is not None and worker.isRunning())
        except RuntimeError:
            return False

    def _auto_refresh_defer_remaining(self) -> float:
        remaining = float(self._auto_refresh_deferred_until or 0.0) - time.time()
        if remaining <= 0:
            self._auto_refresh_deferred_until = 0.0
            self._auto_refresh_defer_reason = ""
            return 0.0
        return remaining

    def defer_auto_refresh(self, seconds: float, reason: str = "") -> None:
        duration = max(0.0, float(seconds or 0.0))
        if duration <= 0:
            return
        self._auto_refresh_deferred_until = max(
            float(self._auto_refresh_deferred_until or 0.0),
            time.time() + duration,
        )
        self._auto_refresh_defer_reason = str(reason or "").strip()
        worker = self._worker
        if worker is not None:
            try:
                defer_worker = getattr(worker, "defer_auto_refresh", None)
                if callable(defer_worker):
                    defer_worker(duration, self._auto_refresh_defer_reason)
            except RuntimeError:
                self._worker = None
        self._set_runtime_state("deferred", self._auto_refresh_defer_reason)

    def clear_auto_refresh_defer(self) -> None:
        self._auto_refresh_deferred_until = 0.0
        self._auto_refresh_defer_reason = ""

    def _set_runtime_state(self, state: str, message: str = "") -> None:
        self._runtime_state = str(state or "idle").strip()
        self.sig_runtime_state_changed.emit(
            {
                "state": self._runtime_state,
                "message": str(message or "").strip(),
                "running": self.is_running(),
                "last_success_at": self._last_success_at.isoformat(timespec="seconds") if self._last_success_at else "",
                "last_error": self._last_error,
            }
        )

    def _ensure_worker(self):
        worker = self._worker
        if worker is not None:
            try:
                worker.codes = self.target_codes()
                return worker
            except RuntimeError:
                self._worker = None

        worker = self._worker_factory(self.target_codes())
        self._worker = worker
        worker.progress.connect(self.sig_progress.emit)
        worker.result_ready.connect(self._on_rt_update)
        worker.finished.connect(self._on_worker_finished)
        return worker

    def resume_auto_refresh(self) -> None:
        self.clear_auto_refresh_defer()
        worker = self._ensure_worker()
        try:
            worker.resume_auto_refresh()
        except (AttributeError, RuntimeError):
            pass
        self._set_runtime_state("running")

    def pause_for_cache_sync(self) -> None:
        worker = self._worker
        if worker is not None:
            try:
                worker.pause_for_cache_sync()
            except (AttributeError, RuntimeError):
                pass
        self._set_runtime_state("paused_for_cache_sync")

    def trigger_refresh_once(self) -> bool:
        worker = self._ensure_worker()
        self._set_runtime_state("manual_refresh_once")
        try:
            if not worker.isRunning():
                worker.start()
            worker.trigger_refresh()
            return True
        except RuntimeError as exc:
            self._last_error = str(exc)
            self._set_runtime_state("error", str(exc))
            return False

    def sync_runtime_state(self) -> str:
        codes = self.target_codes()
        quote_open = is_asian_quote_refresh_time(codes)
        defer_remaining = self._auto_refresh_defer_remaining()
        if quote_open and defer_remaining > 0:
            worker = self._worker
            if worker is not None:
                try:
                    defer_worker = getattr(worker, "defer_auto_refresh", None)
                    if callable(defer_worker):
                        defer_worker(defer_remaining, self._auto_refresh_defer_reason)
                except RuntimeError:
                    self._worker = None
            self._set_runtime_state("deferred", self._auto_refresh_defer_reason)
            return "deferred"
        if quote_open:
            worker = self._ensure_worker()
            try:
                worker.resume_auto_refresh()
                if not worker.isRunning():
                    worker.start()
                    self._set_runtime_state("running", "started")
                    return "started"
                self._set_runtime_state("running")
                return "running"
            except RuntimeError as exc:
                self._last_error = str(exc)
                self._set_runtime_state("error", str(exc))
                raise

        if self.is_running():
            self.stop(auto=True)
            return "stopped"
        self._set_runtime_state("paused_for_cache_sync")
        return "skipped"

    def stop(self, *, auto: bool = False) -> bool:
        worker = self._worker
        if worker is None:
            self._set_runtime_state("paused_for_cache_sync" if auto else "idle")
            return False
        request_thread_shutdown(
            worker,
            label="Asian market worker",
            stop=getattr(worker, "stop", None),
            timeout_ms=3000,
            logger=log,
        )
        self._worker = None
        self._set_runtime_state("paused_for_cache_sync" if auto else "idle")
        return True

    def shutdown(self) -> None:
        self.stop(auto=False)

    def _on_worker_finished(self) -> None:
        worker = self._worker
        if worker is not None:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
        self._worker = None
        if self._runtime_state != "manual_refresh_once":
            self._set_runtime_state("idle")

    def _on_rt_update(self, updates: dict) -> None:
        if not updates:
            return
        self._last_success_at = dt.datetime.now()
        self._last_error = ""
        self.sig_rt_update.emit(dict(updates or {}))
        self._set_runtime_state(self._runtime_state or "running", f"updates={len(updates or {})}")

    @staticmethod
    def _save_rt_cache() -> None:
        try:
            cache_friendly = {}
            for key, value in GLOBAL_ASIAN_RT_CACHE.items():
                cache_friendly[key] = {
                    "date": value.get("date", ""),
                    "close": value.get("close", 0.0),
                    "open": value.get("open", 0.0),
                    "high": value.get("high", 0.0),
                    "low": value.get("low", 0.0),
                    "volume": value.get("volume", 0.0),
                    "previous_close": value.get("previous_close", 0.0),
                    "pct": _round_pct(value.get("pct", 0.0)),
                    "pe": value.get("pe"),
                    "pe_source": value.get("pe_source", ""),
                    "pe_updated_at": value.get("pe_updated_at", 0.0),
                    "pct_5": _round_pct(value.get("pct_5", 0.0)),
                    "pct_10": _round_pct(value.get("pct_10", 0.0)),
                    "pct_20": _round_pct(value.get("pct_20", 0.0)),
                    "currency": value.get("currency", ""),
                    "source": value.get("source", ""),
                    "quote_quality": value.get("quote_quality", ""),
                }
            cache_dir = os.path.dirname(RT_JSON_CACHE)
            if cache_dir:
                os.makedirs(cache_dir, exist_ok=True)
            with open(RT_JSON_CACHE, "w", encoding="utf-8") as file_obj:
                json.dump(cache_friendly, file_obj, ensure_ascii=False)
        except (PermissionError, OSError, TypeError, ValueError) as exc:
            log.error(f"[亚洲市场] 持久化 RT 缓存失败: {exc}")

    @staticmethod
    def _cache_latest_trade_dates() -> dict:
        try:
            if not os.path.exists(JSON_CACHE):
                return {}
            with open(JSON_CACHE, "r", encoding="utf-8") as file_obj:
                raw = json.load(file_obj)

            latest_dates = {}
            for item in raw.get("stocks", []):
                ticker = str(item.get("ticker", "") or "").strip().upper()
                if "." not in ticker:
                    continue
                market = MarketCalendar.normalize_market(ticker.split(".")[-1])
                klines = item.get("klines", [])
                if not klines:
                    continue
                last_date_raw = str(klines[-1].get("date", "")).strip()
                if not last_date_raw:
                    continue
                try:
                    last_date = dt.datetime.strptime(last_date_raw[:10], "%Y-%m-%d").date()
                except (TypeError, ValueError):
                    continue
                if latest_dates.get(market) is None or last_date > latest_dates[market]:
                    latest_dates[market] = last_date
            return latest_dates
        except (
            FileNotFoundError,
            PermissionError,
            OSError,
            TypeError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            log.warning(f"[亚洲市场] 解析缓存最新交易日失败: {exc}")
            return {}

    def _expected_latest_trade_dates(self) -> dict:
        from datetime import timedelta

        markets = set()
        for code in self.target_codes():
            code = str(code or "").strip()
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
        for market in markets:
            now_market = MarketCalendar.now(market)
            today_market = now_market.date()
            hhmm = now_market.hour * 100 + now_market.minute
            cutoff = close_cutoff_hhmm.get(market, 1630)

            if MarketCalendar.is_trade_day(today_market, market=market) and hhmm < cutoff:
                ref_date = today_market - timedelta(days=1)
            else:
                ref_date = today_market

            trade_date = MarketCalendar.get_latest_trade_date(market=market, ref_date=ref_date)
            if trade_date is not None:
                latest_expected[market] = trade_date
        return latest_expected

    def cache_staleness(self) -> dict:
        now = MarketCalendar.now("CN")
        target_dt = now.replace(hour=16, minute=30, second=0, microsecond=0)
        if now < target_dt:
            target_dt -= dt.timedelta(days=1)
        while target_dt.weekday() >= 5:
            target_dt -= dt.timedelta(days=1)

        mtime = os.path.getmtime(JSON_CACHE) if os.path.exists(JSON_CACHE) else 0
        cache_dt = MarketCalendar.from_timestamp(mtime, "CN") if mtime else dt.datetime.min
        cache_latest = self._cache_latest_trade_dates()
        expected_latest = self._expected_latest_trade_dates()
        cache_latest_date = max(cache_latest.values()) if cache_latest else None
        expected_latest_date = max(expected_latest.values()) if expected_latest else None

        stale_by_mtime = cache_dt < target_dt
        stale_markets = []
        for market, expected_date in expected_latest.items():
            cache_date = cache_latest.get(market)
            if expected_date is not None and (cache_date is None or cache_date < expected_date):
                stale_markets.append((market, cache_date, expected_date))
        stale_by_trade_date = bool(
            stale_markets
            or (
                expected_latest_date is not None
                and (cache_latest_date is None or cache_latest_date < expected_latest_date)
            )
        )
        return {
            "stale": bool(stale_by_mtime or stale_by_trade_date),
            "stale_by_mtime": stale_by_mtime,
            "stale_by_trade_date": stale_by_trade_date,
            "stale_markets": stale_markets,
            "cache_dt": cache_dt,
            "target_dt": target_dt,
            "cache_latest_trade_date": cache_latest_date,
            "expected_latest_trade_date": expected_latest_date,
        }

    def run_cache_sync_if_stale(self, *, emit_event: bool = True) -> dict:
        staleness = self.cache_staleness()
        if not staleness.get("stale"):
            return {
                "job_key": "asian_market_cache_sync",
                "status": "skipped",
                "message": "cache fresh",
                "records": 0,
                **staleness,
            }

        success, message, report = sync_asian_kline_cache(
            max_workers=3,
            period="1y",
        )
        records = 0
        if isinstance(report, dict):
            try:
                records = int(report.get("written_count") or len(report.get("rows") or []) or 0)
            except (TypeError, ValueError):
                records = 0

        result = {
            "job_key": "asian_market_cache_sync",
            "status": "success" if success else "degraded",
            "message": str(message or ""),
            "records": records,
            **staleness,
        }
        if isinstance(report, dict):
            result["sync_report"] = report
            result["missing"] = list(report.get("missing") or [])
        if success:
            self._last_success_at = dt.datetime.now()
            self._last_error = ""
            if emit_event:
                event_bus.sig_asian_klines_ready.emit()
            return result
        self._last_error = str(message or "asian cache sync failed")
        result["error"] = self._last_error
        log.warning("[亚洲市场] K 线缓存同步降级: %s", self._last_error)
        return result
