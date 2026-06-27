# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import time

import pandas as pd

from app.services.ui_fund_holdings_service import fund_holdings_sync_service
from app.services.ui_market_calendar_service import MarketCalendar
from core.ai_industry_chain_pool import filter_rows_to_ai_chain_codes
from core.lhb_pool_manager import POOL_WINDOW, LhbPoolManager
from core.logger import get_logger
from core.task_errors import UserFacingTaskError

log = get_logger(__name__)


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

    def _get_na_daily_service(self):
        if self.na_daily_service is None:
            from ui.services.na_daily_service import NADailyRefreshService

            self.na_daily_service = NADailyRefreshService()
        return self.na_daily_service

    def _get_asian_market_service(self):
        if self.asian_market_service is None:
            from ui.services.asian_market_runtime_service import AsianMarketRuntimeService

            self.asian_market_service = AsianMarketRuntimeService()
        return self.asian_market_service

    def _get_earnings_service(self):
        if self.earnings_service is None:
            from ui.services.earnings_refresh_service import EarningsRefreshService

            self.earnings_service = EarningsRefreshService()
        return self.earnings_service

    def run_lhb_daily(self, trade_date: str) -> dict:
        from ui.workers.lhb_worker import fetch_lhb_pool_for_date

        date_text = str(trade_date or "").strip()
        if not date_text:
            raise ValueError("trade_date must not be blank")

        payload = fetch_lhb_pool_for_date(date_text, emit_success_log=False, return_meta=True)
        records = payload.get("records", []) if isinstance(payload, dict) else payload
        status = payload.get("status", "ok") if isinstance(payload, dict) else "ok"
        records = list(records or [])
        records = _filter_lhb_rows_to_ai_chain(records)

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
        manager.save()

        return {
            "job_key": "lhb_daily",
            "trade_date": date_text,
            "status": status,
            "records": len(records),
            "cached_trade_days": len(manager.get_cached_dates() or []),
        }

    def run_foreign_block_daily(self, trade_date: str, *, days_to_fetch: int = 30) -> dict:
        payload = _fetch_foreign_block_records(days_to_fetch=days_to_fetch)
        row_data = _build_foreign_block_rows(payload.get("records", []))
        timeout_chunks = list(payload.get("timeout_chunks") or [])
        failed_chunks = list(payload.get("failed_chunks") or [])
        latest_trade_date = _latest_foreign_block_trade_date(row_data)
        if row_data:
            _save_foreign_block_cache(
                row_data,
                days_to_fetch=days_to_fetch,
                latest_trade_date=latest_trade_date,
            )
        if timeout_chunks or failed_chunks:
            if row_data:
                log.warning(
                    "[外资大宗] 自动刷新结果不完整，已先保存可用结果: "
                    f"timeout={timeout_chunks}, failed={failed_chunks}"
                )
                return {
                    "job_key": "foreign_block_daily",
                    "trade_date": str(trade_date or "").strip(),
                    "records": len(row_data),
                    "latest_trade_date": latest_trade_date,
                    "status": "partial",
                    "timeout_chunks": timeout_chunks,
                    "failed_chunks": failed_chunks,
                }
            raise UserFacingTaskError(
                "外资大宗自动刷新未完成，保留后续重试机会。",
                f"foreign block auto refresh incomplete: timeout={timeout_chunks}, failed={failed_chunks}",
            )
        if not row_data:
            _save_foreign_block_cache(row_data, days_to_fetch=days_to_fetch, latest_trade_date=latest_trade_date)
        return {
            "job_key": "foreign_block_daily",
            "trade_date": str(trade_date or "").strip(),
            "records": len(row_data),
            "latest_trade_date": latest_trade_date,
        }

    def run_fund_holdings_daily(self, trade_date: str) -> dict:
        result = fund_holdings_sync_service.sync_latest_all()
        message = ""
        if isinstance(result, dict):
            message = str(result.get("message") or "").strip()
        return {
            "job_key": "fund_holdings_daily",
            "trade_date": str(trade_date or "").strip(),
            "message": message,
            "result": result,
        }

    def run_na_daily_full_0925(self, trade_date: str) -> dict:
        result = self._get_na_daily_service().refresh_full(emit_event=False)
        return {
            "job_key": "na_daily_full_0925",
            "trade_date": str(trade_date or "").strip(),
            "records": int(result.get("records") or 0),
            "cache_file": result.get("cache_file", ""),
        }

    def run_na_daily_incremental(self, trade_date: str) -> dict:
        result = self._get_na_daily_service().refresh_incremental(emit_event=False)
        return {
            "job_key": "na_daily_incremental",
            "trade_date": str(trade_date or "").strip(),
            "records": int(result.get("records") or 0),
            "message": str(result.get("message") or result.get("status") or "").strip(),
            "cache_file": result.get("cache_file", ""),
        }

    def sync_asian_market_runtime(self) -> dict:
        result = self._get_asian_market_service().sync_runtime_state()
        return {
            "job_key": "asian_market_runtime",
            "status": str(result or "skipped"),
        }

    def run_asian_market_cache_sync(self, trade_date: str) -> dict:
        result = self._get_asian_market_service().run_cache_sync_if_stale(emit_event=False)
        result["trade_date"] = str(trade_date or "").strip()
        return result

    def run_earnings_startup_gap_fill(self, trade_date: str) -> dict:
        result = self._get_earnings_service().run_startup_gap_fill()
        result["trade_date"] = str(trade_date or "").strip()
        return result

    def run_earnings_routine(self, trade_date: str, *, routine_time: str) -> dict:
        result = self._get_earnings_service().run_routine_scan(reason=f"scheduled:{routine_time}")
        result["trade_date"] = str(trade_date or "").strip()
        result["routine_time"] = str(routine_time or "").strip()
        return result

    def run_earnings_email_digest(self, trade_date: str) -> dict:
        result = self._get_earnings_service().run_email_digest()
        result["trade_date"] = str(trade_date or "").strip()
        return result


def _filter_lhb_rows_to_ai_chain(row_data: list[dict]) -> list[dict]:
    try:
        return filter_rows_to_ai_chain_codes(
            row_data,
            code_keys=("代码", "股票代码", "证券代码", "stock_code", "code"),
        )
    except (FileNotFoundError, RuntimeError, OSError, TypeError, ValueError) as exc:
        log.warning(f"[龙虎榜池] AI产业链股票池不可用，自动缓存按空股票池处理: {exc}")
        return []


def _fetch_foreign_block_records(*, days_to_fetch: int) -> dict:
    block_module = _foreign_block_module()
    end_dt = datetime.datetime.now()
    deadline = time.monotonic() + block_module._BLOCK_TRADE_TOTAL_TIMEOUT
    try:
        remaining = max(5, int(deadline - time.monotonic()))
        dates = [
            pd.to_datetime(d).date()
            for d in block_module._run_domestic_akshare(
                "calendar",
                timeout=min(block_module._BLOCK_TRADE_CALENDAR_TIMEOUT, remaining),
            )
        ]
        today_date = end_dt.date()
        past_dates = [d for d in dates if d <= today_date]
        if len(past_dates) >= days_to_fetch:
            start_date_val = past_dates[-days_to_fetch]
        else:
            start_date_val = past_dates[0] if past_dates else (today_date - datetime.timedelta(days=days_to_fetch))
        start_dt = datetime.datetime.combine(start_date_val, datetime.time())
    except Exception as exc:
        log.warning(f"[外资大宗] 获取交易日历失败，使用自然日估算: {exc}")
        start_dt = end_dt - datetime.timedelta(days=int(days_to_fetch * 1.5))

    results = []
    timeout_chunks = []
    failed_chunks = []
    finished_chunks = 0
    chunk_days = 15
    chunk_windows = []
    cursor = start_dt
    while cursor < end_dt:
        chunk_end = min(cursor + datetime.timedelta(days=chunk_days), end_dt)
        chunk_windows.append((cursor, chunk_end))
        cursor = chunk_end + datetime.timedelta(days=1)

    chunk_windows = list(reversed(chunk_windows))
    for chunk_index, (cursor, chunk_end) in enumerate(chunk_windows):
        start_text = cursor.strftime("%Y%m%d")
        end_text = chunk_end.strftime("%Y%m%d")
        chunk_key = f"{start_text}-{end_text}"
        chunk_done = False
        chunk_timed_out = False

        if time.monotonic() >= deadline:
            timeout_chunks.extend(
                f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}" for start, end in chunk_windows[chunk_index:]
            )
            break

        for attempt in range(block_module._BLOCK_TRADE_MAX_RETRIES):
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                chunk_timed_out = True
                break
            try:
                records = block_module._run_domestic_akshare(
                    "block_trade",
                    start_text,
                    end_text,
                    timeout=min(block_module._BLOCK_TRADE_CHUNK_TIMEOUT, remaining),
                )
                df = pd.DataFrame(records) if records else pd.DataFrame()
                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        if _should_include_foreign_row(row.get("买方营业部"), row.get("卖方营业部")):
                            results.append(row.to_dict())
                chunk_done = True
                finished_chunks += 1
                break
            except block_module.ProcessTimeoutError:
                chunk_timed_out = True
                log.warning(f"[外资大宗] {chunk_key} 请求超时")
                if attempt < block_module._BLOCK_TRADE_MAX_RETRIES - 1:
                    time.sleep(1)
            except Exception as exc:
                log.warning(f"[外资大宗] {chunk_key} 第{attempt + 1}次失败: {exc}")
                if attempt < block_module._BLOCK_TRADE_MAX_RETRIES - 1:
                    time.sleep(1)

        if not chunk_done:
            if chunk_timed_out:
                timeout_chunks.append(chunk_key)
            else:
                failed_chunks.append(chunk_key)
            if time.monotonic() >= deadline:
                timeout_chunks.extend(
                    f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
                    for start, end in chunk_windows[chunk_index + 1 :]
                )
                break

    if not results and failed_chunks and finished_chunks == 0:
        raise UserFacingTaskError(
            "外资大宗自动刷新失败：本轮未拿到有效结果。",
            "foreign block auto refresh failed: all chunks returned no valid result",
        )

    return {
        "records": results,
        "timeout_chunks": timeout_chunks,
        "failed_chunks": failed_chunks,
    }


def _should_include_foreign_row(buyer, seller) -> bool:
    keywords = _foreign_block_module().FOREIGN_KEYWORDS
    buyer_text = str(buyer) if pd.notna(buyer) else ""
    seller_text = str(seller) if pd.notna(seller) else ""
    return any(keyword in buyer_text or keyword in seller_text for keyword in keywords)


def _determine_foreign_direction(buyer, seller) -> str:
    keywords = _foreign_block_module().FOREIGN_KEYWORDS
    buyer_text = str(buyer) if pd.notna(buyer) else ""
    seller_text = str(seller) if pd.notna(seller) else ""
    buy_foreign = any(keyword in buyer_text for keyword in keywords)
    sell_foreign = any(keyword in seller_text for keyword in keywords)
    if buy_foreign and sell_foreign:
        return "外资对倒"
    if buy_foreign:
        return "外资买入"
    if sell_foreign:
        return "外资卖出"
    return "--"


def _build_foreign_block_rows(records: list[dict]) -> list[dict]:
    if not records:
        return []

    df = pd.DataFrame(records)
    if "交易日期" in df.columns:
        df["交易日期"] = _foreign_block_module()._normalize_trade_date_series(df["交易日期"])

    required = ["交易日期", "证券代码", "买方营业部", "卖方营业部", "证券简称"]
    missing = [name for name in required if name not in df.columns]
    if missing:
        raise ValueError(f"foreign block records missing columns: {missing}")

    agg_map = {}
    for source, method in (
        ("收盘价", "first"),
        ("成交价格", "mean"),
        ("折溢率", "mean"),
        ("成交量", "sum"),
        ("成交金额", "sum"),
    ):
        if source in df.columns:
            agg_map[source] = method
    if not agg_map:
        raise ValueError("foreign block records missing numeric columns")

    grouped = df.groupby(["交易日期", "证券代码", "买方营业部", "卖方营业部", "证券简称"], as_index=False).agg(agg_map)
    grouped = grouped.sort_values(by=["交易日期", "证券代码"], ascending=[False, True])

    row_data = []
    for _, record in grouped.iterrows():
        trade_date = str(record.get("交易日期", ""))
        code = str(record.get("证券代码", "")).zfill(6)
        name = str(record.get("证券简称", ""))
        close_price = _safe_float(record.get("收盘价", 0))
        trade_price = _safe_float(record.get("成交价格", 0))
        premium = _safe_float(record.get("折溢率", 0))
        volume = _safe_float(record.get("成交量", 0))
        amount = _safe_float(record.get("成交金额", 0))
        buyer = str(record.get("买方营业部", ""))
        seller = str(record.get("卖方营业部", ""))
        premium_pct = premium * 100
        row_data.append(
            {
                "代码": code,
                "名称": name,
                "现价": "--",
                "涨幅%": "--",
                "市值": "--",
                "交易日期": trade_date,
                "交易详情": _determine_foreign_direction(buyer, seller),
                "当日收盘价": f"{close_price:.2f}" if close_price else "--",
                "成交价格": f"{trade_price:.2f}" if trade_price else "--",
                "折/溢价率(%)": f"{premium_pct:.2f}%",
                "成交数量(万股)": f"{volume / 10000.0:.2f}",
                "成交金额(万元)": f"{amount:.2f}",
                "买方营业部": buyer,
                "卖方营业部": seller,
            }
        )
    try:
        return filter_rows_to_ai_chain_codes(row_data, code_keys=("代码",))
    except (FileNotFoundError, RuntimeError, OSError, ValueError) as exc:
        log.warning(f"[外资大宗] AI产业链股票池不可用，自动缓存按空股票池处理: {exc}")
        return []


def _safe_float(value) -> float:
    try:
        if pd.isna(value):
            return 0.0
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _latest_foreign_block_trade_date(row_data: list[dict]) -> str:
    dates = [
        str(row.get("交易日期", "")).strip()
        for row in (row_data or [])
        if isinstance(row, dict) and str(row.get("交易日期", "")).strip()
    ]
    return max(dates) if dates else ""


def _save_foreign_block_cache(row_data: list[dict], *, days_to_fetch: int, latest_trade_date: str) -> None:
    from core.json_cache import save_json_file

    block_module = _foreign_block_module()
    payload = {
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "days_to_fetch": int(days_to_fetch),
        "latest_trade_date": str(latest_trade_date or "").strip(),
        "rows": row_data or [],
    }
    save_json_file(block_module._BLOCK_TRADE_CACHE_FILE, payload)


def _foreign_block_module():
    from ui.tabs import foreign_block_trade_tab

    return foreign_block_trade_tab
