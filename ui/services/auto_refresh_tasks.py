# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime

from app.services.ui_task_lifecycle_service import invoke_with_cancellation
from app.services.ui_task_lifecycle_service import raise_if_cancelled as _raise_if_cancelled
from core.logger import get_logger
from core.task_errors import UserFacingTaskError

log = get_logger(__name__)


def _fetch_foreign_block_records(*, days_to_fetch: int, cancellation_token=None) -> dict:
    from app.services.foreign_block_market_data_service import fetch_foreign_block_records

    return fetch_foreign_block_records(
        days_to_fetch=days_to_fetch,
        cancellation_token=cancellation_token,
    )


def _build_foreign_block_rows(records: list[dict], *, cancellation_token=None) -> list[dict]:
    from app.services.foreign_block_market_data_service import build_foreign_block_cache_rows

    return build_foreign_block_cache_rows(records, cancellation_token=cancellation_token)


class AutoRefreshTaskService:
    def __init__(
        self,
        data_provider=None,
        engine=None,
        *,
        na_daily_service=None,
        asian_market_service=None,
        earnings_service=None,
    ):
        self.data_provider = data_provider
        self.engine = engine
        self.na_daily_service = na_daily_service
        self.asian_market_service = asian_market_service
        self.earnings_service = earnings_service
        self._prepared_asian_target_codes: list[str] | None = None

    def _get_na_daily_service(self):
        if self.na_daily_service is None:
            from app.services.na_daily_service import NADailyRefreshService

            self.na_daily_service = NADailyRefreshService()
        return self.na_daily_service

    def _get_asian_market_service(self):
        if self.asian_market_service is None:
            from ui.services.asian_market_runtime_service import AsianMarketRuntimeService

            self.asian_market_service = AsianMarketRuntimeService()
        return self.asian_market_service

    def run_lhb_daily(self, trade_date: str, *, cancellation_token=None) -> dict:
        from app.services.lhb_market_data_service import fetch_lhb_pool_for_date
        from app.services.ui_lhb_pool_service import POOL_WINDOW, LhbPoolManager
        from app.services.ui_market_calendar_service import MarketCalendar

        date_text = str(trade_date or "").strip()
        if not date_text:
            raise ValueError("trade_date must not be blank")

        _raise_if_cancelled(cancellation_token)
        payload = fetch_lhb_pool_for_date(
            date_text,
            emit_success_log=False,
            return_meta=True,
            cancellation_token=cancellation_token,
        )
        _raise_if_cancelled(cancellation_token)
        records = payload.get("records", []) if isinstance(payload, dict) else payload
        status = payload.get("status", "ok") if isinstance(payload, dict) else "ok"
        records = list(records or [])
        records = _filter_lhb_rows_to_ai_chain(records)
        _raise_if_cancelled(cancellation_token)

        manager = LhbPoolManager()
        manager.add_day(date_text, records)
        manager.last_auto_fetch_date = date_text

        try:
            ref_date = datetime.datetime.strptime(date_text, "%Y%m%d").date()
            trade_dates = MarketCalendar.get_recent_trade_dates(POOL_WINDOW, ref_date=ref_date)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            trade_dates = []
        if trade_dates:
            manager.prune(trade_dates)
        _raise_if_cancelled(cancellation_token)
        manager.save()
        _raise_if_cancelled(cancellation_token)

        return {
            "job_key": "lhb_daily",
            "trade_date": date_text,
            "status": status,
            "records": len(records),
            "cached_trade_days": len(manager.get_cached_dates() or []),
        }

    def run_foreign_block_daily(
        self,
        trade_date: str,
        *,
        days_to_fetch: int = 30,
        cancellation_token=None,
    ) -> dict:
        payload = invoke_with_cancellation(
            _fetch_foreign_block_records,
            cancellation_token,
            days_to_fetch=days_to_fetch,
        )
        row_data = invoke_with_cancellation(
            _build_foreign_block_rows,
            cancellation_token,
            payload.get("records", []),
        )
        timeout_chunks = list(payload.get("timeout_chunks") or [])
        failed_chunks = list(payload.get("failed_chunks") or [])
        latest_trade_date = _latest_foreign_block_trade_date(row_data)
        if row_data:
            invoke_with_cancellation(
                _save_foreign_block_cache,
                cancellation_token,
                row_data,
                days_to_fetch=days_to_fetch,
                latest_trade_date=latest_trade_date,
            )
        if timeout_chunks or failed_chunks:
            return _foreign_incomplete_result(
                trade_date,
                row_data,
                latest_trade_date,
                timeout_chunks,
                failed_chunks,
            )
        if not row_data:
            invoke_with_cancellation(
                _save_foreign_block_cache,
                cancellation_token,
                row_data,
                days_to_fetch=days_to_fetch,
                latest_trade_date=latest_trade_date,
            )
        return {
            "job_key": "foreign_block_daily",
            "trade_date": str(trade_date or "").strip(),
            "records": len(row_data),
            "latest_trade_date": latest_trade_date,
        }

    def run_fund_holdings_daily(self, trade_date: str, *, cancellation_token=None) -> dict:
        from app.services.ui_fund_holdings_service import fund_holdings_sync_service

        _raise_if_cancelled(cancellation_token)
        if cancellation_token is None:
            result = fund_holdings_sync_service.sync_latest_all()
        else:
            result = fund_holdings_sync_service.sync_latest_all(cancellation_token=cancellation_token)
        _raise_if_cancelled(cancellation_token)
        message = ""
        if isinstance(result, dict):
            message = str(result.get("message") or "").strip()
        return {
            "job_key": "fund_holdings_daily",
            "trade_date": str(trade_date or "").strip(),
            "message": message,
            "result": result,
        }

    def run_na_daily_full_0925(self, trade_date: str, *, cancellation_token=None) -> dict:
        _raise_if_cancelled(cancellation_token)
        result = self._get_na_daily_service().refresh_full(emit_event=False)
        _raise_if_cancelled(cancellation_token)
        return {
            "job_key": "na_daily_full_0925",
            "trade_date": str(trade_date or "").strip(),
            "records": int(result.get("records") or 0),
            "cache_file": result.get("cache_file", ""),
        }

    def run_na_daily_incremental(self, trade_date: str, *, cancellation_token=None) -> dict:
        _raise_if_cancelled(cancellation_token)
        result = self._get_na_daily_service().refresh_incremental(emit_event=False)
        _raise_if_cancelled(cancellation_token)
        return {
            "job_key": "na_daily_incremental",
            "trade_date": str(trade_date or "").strip(),
            "records": int(result.get("records") or 0),
            "message": str(result.get("message") or result.get("status") or "").strip(),
            "cache_file": result.get("cache_file", ""),
        }

    def prepare_asian_market_runtime(self, *, cancellation_token=None) -> dict:
        _raise_if_cancelled(cancellation_token)
        if self._prepared_asian_target_codes is None:
            try:
                from ui.services.asian_market_runtime_service import filter_asian_tickers

                target_map = filter_asian_tickers() or {}
                target_codes = [str(code).strip() for code in target_map.values() if str(code).strip()]
                if target_codes:
                    self._prepared_asian_target_codes = target_codes
            except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.warning(f"[亚洲市场] 后台准备目标池失败: {exc}")
                target_codes = []
        else:
            target_codes = list(self._prepared_asian_target_codes)

        _raise_if_cancelled(cancellation_token)
        from ui.services.asian_market_runtime_service import is_asian_quote_refresh_time

        is_asian_quote_refresh_time(target_codes)
        _raise_if_cancelled(cancellation_token)
        return {"target_codes": target_codes}

    def sync_asian_market_runtime(self, prepared: dict | None = None, *, cancellation_token=None) -> dict:
        _raise_if_cancelled(cancellation_token)
        service = self._get_asian_market_service()
        target_codes = list((prepared or {}).get("target_codes") or [])
        worker_codes = list(getattr(getattr(service, "_worker", None), "codes", []) or [])
        service_codes = list(getattr(service, "_codes", []) or [])
        if target_codes and not worker_codes and not service_codes:
            service.set_target_codes(target_codes)
        elif prepared is not None and not target_codes and not worker_codes and not service_codes:
            stopped = service.stop(auto=True)
            return {
                "job_key": "asian_market_runtime",
                "status": "stopped" if stopped else "skipped",
            }
        result = service.sync_runtime_state()
        _raise_if_cancelled(cancellation_token)
        return {
            "job_key": "asian_market_runtime",
            "status": str(result or "skipped"),
        }

    def run_asian_market_cache_sync(self, trade_date: str, *, cancellation_token=None) -> dict:
        _raise_if_cancelled(cancellation_token)
        result = self._get_asian_market_service().run_cache_sync_if_stale(emit_event=False)
        _raise_if_cancelled(cancellation_token)
        result["trade_date"] = str(trade_date or "").strip()
        return result

    def run_earnings_startup_gap_fill(self, trade_date: str, *, cancellation_token=None) -> dict:
        from app.services.earnings_refresh_process_service import run_earnings_refresh

        result = invoke_with_cancellation(
            run_earnings_refresh,
            cancellation_token,
            "startup-gap-fill",
        )
        result["trade_date"] = str(trade_date or "").strip()
        return result

    def run_earnings_routine(self, trade_date: str, *, routine_time: str, cancellation_token=None) -> dict:
        from app.services.earnings_refresh_process_service import run_earnings_refresh

        result = invoke_with_cancellation(
            run_earnings_refresh,
            cancellation_token,
            "routine",
            routine_time=routine_time,
        )
        result["trade_date"] = str(trade_date or "").strip()
        result["routine_time"] = str(routine_time or "").strip()
        return result


def _filter_lhb_rows_to_ai_chain(row_data: list[dict]) -> list[dict]:
    try:
        from app.services.ui_industry_chain_service import (
            filter_rows_to_ai_chain_codes,
            load_cached_ai_industry_chain_stock_codes,
        )

        return filter_rows_to_ai_chain_codes(
            row_data,
            code_keys=("代码", "股票代码", "证券代码", "stock_code", "code"),
            stock_codes=load_cached_ai_industry_chain_stock_codes(),
        )
    except (FileNotFoundError, RuntimeError, OSError, TypeError, ValueError) as exc:
        log.warning(f"[龙虎榜池] AI产业链股票池不可用，自动缓存按空股票池处理: {exc}")
        return []


def _foreign_incomplete_result(trade_date, rows, latest_trade_date, timeout_chunks, failed_chunks) -> dict:
    if not rows:
        raise UserFacingTaskError(
            "外资大宗自动刷新未完成，保留后续重试机会。",
            f"foreign block auto refresh incomplete: timeout={timeout_chunks}, failed={failed_chunks}",
        )
    log.warning(
        "[外资大宗] 自动刷新结果不完整，已先保存可用结果: "
        f"timeout={timeout_chunks}, failed={failed_chunks}"
    )
    return {
        "job_key": "foreign_block_daily",
        "trade_date": str(trade_date or "").strip(),
        "records": len(rows),
        "latest_trade_date": latest_trade_date,
        "status": "partial",
        "timeout_chunks": timeout_chunks,
        "failed_chunks": failed_chunks,
    }


def _latest_foreign_block_trade_date(row_data: list[dict]) -> str:
    dates = [
        str(row.get("交易日期", "")).strip()
        for row in (row_data or [])
        if isinstance(row, dict) and str(row.get("交易日期", "")).strip()
    ]
    return max(dates) if dates else ""


def _save_foreign_block_cache(row_data: list[dict], *, days_to_fetch: int, latest_trade_date: str) -> None:
    from app.services.foreign_block_cache_service import save_foreign_block_cache

    save_foreign_block_cache(
        row_data,
        days_to_fetch=days_to_fetch,
        latest_trade_date=latest_trade_date,
    )
