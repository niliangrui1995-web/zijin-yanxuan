# -*- coding: utf-8 -*-
"""Market calendar utilities for multi-market trade-day and session checks."""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import threading
from typing import Any

from core.background_job_runner import background_job_runner as task_manager
from core.exceptions import (
    BusinessRuleError,
    CacheIOError,
    DataFormatError,
    NetworkServiceError,
)
from core.logger import get_logger
from core.market_calendar_holidays import (
    apply_market_holiday_supplements,
    ensure_holiday_table,
    fetch_public_holidays,
    load_holidays_from_store,
    normalize_holiday_days,
    save_holidays_to_store,
)
from infra.tasks import TaskLifecycleGroup, task_registry

log = get_logger(__name__)
_MARKET_CALENDAR_TASKS = TaskLifecycleGroup(task_manager)


def _fetch_asian_holiday_payload(calendar_cls, market: str, pending_years: list[int], cancellation_token) -> dict:
    fetched: dict[int, set[str]] = {}
    transient_failed: list[int] = []
    unpublished_years: list[int] = []
    try:
        for year in pending_years:
            cancellation_token.raise_if_cancelled()
            try:
                fetched[year] = calendar_cls._fetch_public_holidays(market, year)
            except BusinessRuleError as exc:
                log.warning(f"[交易日历][SOURCE] 亚洲节假日源不可用({market} {year}): {exc}")
                fetched[year] = set()
                unpublished_years.append(year)
            except DataFormatError as exc:
                log.warning(f"[交易日历][FORMAT] 亚洲节假日数据异常({market} {year}): {exc}")
                fetched[year] = set()
            except NetworkServiceError as exc:
                log.warning(f"[交易日历][NETWORK] 亚洲节假日拉取失败({market} {year}): {exc}")
                transient_failed.append(year)
        return {
            "fetched": fetched,
            "transient_failed": transient_failed,
            "unpublished_years": unpublished_years,
        }
    finally:
        if cancellation_token.cancelled:
            with calendar_cls._asian_lock:
                for year in pending_years:
                    calendar_cls._refresh_inflight.discard((market, year))


def _fetch_cn_trade_dates(calendar_cls, cancellation_token) -> set[str]:
    try:
        import akshare as ak

        frame = ak.tool_trade_date_hist_sina()
        if "trade_date" not in frame.columns:
            raise DataFormatError("akshare trade_date column missing")
        dates = [str(value)[:10] for value in frame["trade_date"]]
        return calendar_cls._normalize_holiday_days(dates)
    finally:
        if cancellation_token.cancelled:
            calendar_cls._trade_dates_loading = False


class MarketCalendar:
    _trade_dates: set[str] | None = None
    _trade_dates_loading = False

    _ASIAN_MARKETS = ("TW", "HK", "T", "KS")
    _MARKET_ALIASES = {
        "CN": {"CN", "SH", "SZ", "SS", "A"},
        "HK": {"HK", "HKG"},
        "TW": {"TW", "TWO", "TAI", "TPE"},
        "T": {"T", "JP", "JPN", "TYO"},
        "KS": {"KS", "KQ", "KR", "KOR"},
        "US": {"US", "NASDAQ", "NYSE", "AMEX"},
    }
    _MARKET_TIMEZONE = {
        "CN": "Asia/Shanghai",
        "HK": "Asia/Hong_Kong",
        "TW": "Asia/Taipei",
        "T": "Asia/Tokyo",
        "KS": "Asia/Seoul",
        "US": "America/New_York",
    }
    _MARKET_PHASES = {
        # 中国内地现货市场：开盘集合竞价、连续竞价、午休、收盘集合竞价。
        "CN": (
            (915, 926, "开盘集合竞价"),
            (926, 930, "开市前时段"),
            (930, 1131, "交易中"),
            (1131, 1300, "午休"),
            (1300, 1457, "交易中"),
            (1457, 1501, "收盘集合竞价"),
        ),
        # 港股：盘前竞价 + 连续交易 + 收市竞价。
        "HK": (
            (900, 930, "开市前时段"),
            (930, 1201, "交易中"),
            (1201, 1300, "午休"),
            (1300, 1600, "交易中"),
            (1600, 1611, "收市竞价"),
        ),
        # 台股：常规交易 + 盘后定价交易。
        "TW": (
            (830, 900, "盘前委托"),
            (900, 1331, "交易中"),
            (1400, 1430, "盘后定价申报"),
            (1430, 1431, "盘后定价"),
        ),
        # 东证：午休后延长至 15:30，15:25 起进入收盘集合竞价。
        "T": (
            (900, 1131, "交易中"),
            (1131, 1230, "午休"),
            (1230, 1525, "交易中"),
            (1525, 1531, "收盘集合竞价"),
        ),
        # 韩股：08:30-09:00 开盘集合竞价，15:20-15:30 收盘集合竞价。
        "KS": (
            (830, 900, "开盘集合竞价"),
            (900, 1520, "交易中"),
            (1520, 1531, "收盘集合竞价"),
        ),
        "US": ((930, 1601, "交易中"),),
    }
    _MARKET_SESSIONS = {
        "CN": ((930, 1130), (1300, 1500)),
        "HK": ((930, 1200), (1300, 1600)),
        "TW": ((900, 1330),),
        "T": ((900, 1130), (1230, 1530)),
        "KS": ((900, 1530),),
        "US": ((930, 1600),),
    }
    _MARKET_ACTIVE_STATUSES = frozenset(
        {
            "交易中",
            "开盘集合竞价",
            "收盘集合竞价",
            "收市竞价",
            "开市前时段",
            "盘前委托",
            "盘后定价申报",
            "盘后定价",
        }
    )
    _MARKET_QUOTE_REFRESH_STATUSES = frozenset(_MARKET_ACTIVE_STATUSES | {"午休"})
    _NAGER_COUNTRY = {
        "HK": "HK",
        "T": "JP",
        "KS": "KR",
    }

    _asian_holidays: dict[str, dict[int, set[str]]] = {
        "TW": {},
        "HK": {},
        "T": {},
        "KS": {},
    }
    _asian_holiday_updated_at: dict[tuple[str, int], datetime.datetime] = {}
    # 这里必须使用可重入锁：初始化流程中会出现同线程“持锁调用持锁方法”的嵌套路径，
    # 普通 Lock 会导致主线程自锁，表现为 UI 点击后假死。
    _asian_lock = threading.RLock()
    _asian_bootstrapped = False
    _coverage_check_year: int | None = None
    _refresh_inflight: set[tuple[str, int]] = set()
    _holiday_unpublished_until: dict[tuple[str, int], datetime.datetime] = {}
    _holiday_table_ready = False
    _TW_FUTURE_YEAR_REFRESH_MONTH = 10
    _SOURCE_UNPUBLISHED_RETRY_DAYS = 30

    _TWSE_INCLUDE_KEYWORDS = (
        "\u653e\u5047",  # 放假
        "\u4f11\u5e02",  # 休市
        "\u7121\u4ea4\u6613",  # 無交易
        "\u65e0\u4ea4\u6613",  # 无交易
        "\u88dc\u5047",  # 補假
        "\u8865\u5047",  # 补假
        "\u6625\u7bc0",  # 春節
        "\u6625\u8282",  # 春节
        "\u9664\u5915",  # 除夕
        "\u7aef\u5348",  # 端午
        "\u4e2d\u79cb",  # 中秋
        "\u6e05\u660e",  # 清明
        "\u52de\u52d5\u7bc0",  # 勞動節
        "\u52b3\u52a8\u8282",  # 劳动节
        "\u570b\u6176",  # 國慶
        "\u56fd\u5e86",  # 国庆
        "\u958b\u570b\u7d00\u5ff5\u65e5",  # 開國紀念日
        "\u5143\u65e6",  # 元旦
    )
    _TWSE_EXCLUDE_KEYWORDS = (
        "\u958b\u59cb\u4ea4\u6613",  # 開始交易
        "\u5f00\u59cb\u4ea4\u6613",  # 开始交易
        "\u6700\u5f8c\u4ea4\u6613\u65e5",  # 最後交易日
        "\u6700\u540e\u4ea4\u6613\u65e5",  # 最后交易日
        "\u6062\u5fa9\u4ea4\u6613",  # 恢復交易
        "\u6062\u590d\u4ea4\u6613",  # 恢复交易
        "\u88dc\u884c\u4e0a\u73ed",  # 補行上班
        "\u8865\u884c\u4e0a\u73ed",  # 补行上班
    )

    @staticmethod
    def _project_root() -> str:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    @classmethod
    def _ensure_holiday_table(cls) -> None:
        if cls._holiday_table_ready:
            return
        with cls._asian_lock:
            if cls._holiday_table_ready:
                return
            ensure_holiday_table(cls._project_root())
            cls._holiday_table_ready = True

    @classmethod
    def _normalize_holiday_days(cls, days: Any) -> set[str]:
        return normalize_holiday_days(days)

    @classmethod
    def _load_holidays_from_store(cls, market: str, target: dict[int, set[str]]) -> None:
        cls._ensure_holiday_table()
        rows = load_holidays_from_store(cls._project_root(), market)
        for year, normalized_days, updated_at in rows:
            # keep empty-year entries so coverage checker knows the year has been handled
            target.setdefault(year, set()).update(normalized_days)
            if updated_at is not None:
                cls._asian_holiday_updated_at[(market, year)] = updated_at

    @classmethod
    def _save_holidays_to_store(cls, market: str, year: int, days: set[str]) -> None:
        cls._ensure_holiday_table()
        save_holidays_to_store(cls._project_root(), market, year, days)

    @classmethod
    def normalize_market(cls, market: str | None) -> str:
        raw = str(market or "CN").strip().upper()
        if not raw:
            return "CN"
        for canonical, aliases in cls._MARKET_ALIASES.items():
            if raw == canonical or raw in aliases:
                return canonical
        return raw

    @classmethod
    def infer_market(cls, code: str | None) -> str:
        text = str(code or "").strip().upper()
        if not text:
            return "CN"
        if "." in text:
            suffix = text.split(".")[-1]
            return cls.normalize_market(suffix)
        if text.isdigit() and len(text) in (5, 6):
            return "CN"
        return "US"

    @classmethod
    def _get_market_now(cls, market: str = "CN") -> datetime.datetime:
        canonical = cls.normalize_market(market)
        tz_name = cls._MARKET_TIMEZONE.get(canonical, "Asia/Shanghai")
        try:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            utc_now = datetime.datetime.now(datetime.timezone.utc)
            try:
                return utc_now.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None)
            except ZoneInfoNotFoundError:
                pass
        except ImportError:
            pass
        # safe fallback without timezone db
        utc_now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        offset_hours = {
            "CN": 8,
            "HK": 8,
            "TW": 8,
            "T": 9,
            "KS": 9,
            "US": -5,
        }.get(canonical, 8)
        return utc_now + datetime.timedelta(hours=offset_hours)

    @classmethod
    def now(cls, market: str = "CN") -> datetime.datetime:
        return cls._get_market_now(market)

    @classmethod
    def today(cls, market: str = "CN") -> datetime.date:
        return cls._get_market_now(market).date()

    @classmethod
    def from_timestamp(cls, ts: float | int, market: str = "CN") -> datetime.datetime:
        canonical = cls.normalize_market(market)
        tz_name = cls._MARKET_TIMEZONE.get(canonical, "Asia/Shanghai")
        try:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            utc_dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
            try:
                return utc_dt.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None)
            except ZoneInfoNotFoundError:
                pass
        except ImportError:
            pass
        offset_hours = {
            "CN": 8,
            "HK": 8,
            "TW": 8,
            "T": 9,
            "KS": 9,
            "US": -5,
        }.get(canonical, 8)
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(
            hours=offset_hours
        )

    @classmethod
    def _coerce_date(cls, day: Any, market: str = "CN") -> datetime.date:
        if day is None:
            return cls.today(market)
        if isinstance(day, datetime.datetime):
            return day.date()
        if isinstance(day, datetime.date):
            return day
        if isinstance(day, str):
            txt = day.strip()[:10]
            try:
                return datetime.datetime.strptime(txt, "%Y-%m-%d").date()
            except ValueError as e:
                raise DataFormatError(f"invalid date text: {day}") from e
        raise DataFormatError(f"unsupported date type: {type(day)}")

    @classmethod
    def _bootstrap_asian_holidays(cls) -> None:
        should_validate = False
        with cls._asian_lock:
            if not cls._asian_bootstrapped:
                for market in cls._ASIAN_MARKETS:
                    bucket = cls._asian_holidays.setdefault(market, {})
                    try:
                        cls._load_holidays_from_store(market, bucket)
                    except CacheIOError as e:
                        log.warning(f"[交易日历][I/O] 读取亚洲节假日缓存失败({market}): {e}")
                cls._asian_bootstrapped = True
                should_validate = True

        if should_validate:
            cls._validate_asian_year_coverage()
            cls._retry_empty_future_years()

    @classmethod
    def _required_years(cls, market: str) -> list[int]:
        year = cls._get_market_now(market).year
        return [year - 1, year, year + 1]

    @classmethod
    def _should_skip_holiday_refresh(cls, market: str, year: int, now: datetime.datetime | None = None) -> bool:
        canonical = cls.normalize_market(market)
        target_year = int(year)
        now = now or cls._get_market_now(canonical)
        unpublished_until = cls._holiday_unpublished_until.get((canonical, target_year))
        if unpublished_until is not None and now < unpublished_until:
            return True
        if (
            canonical == "TW"
            and target_year > now.year
            and now.month < cls._TW_FUTURE_YEAR_REFRESH_MONTH
        ):
            return True
        return False

    @classmethod
    def _validate_asian_year_coverage(cls) -> None:
        marker = cls._get_market_now("TW").year
        if cls._coverage_check_year == marker:
            return
        cls._coverage_check_year = marker

        for market in cls._ASIAN_MARKETS:
            now = cls._get_market_now(market)
            required = set(cls._required_years(market))
            with cls._asian_lock:
                existing = set(cls._asian_holidays.get(market, {}).keys())
            missing = sorted(
                year for year in required - existing if not cls._should_skip_holiday_refresh(market, year, now)
            )
            if not missing:
                continue
            log.warning(f"[交易日历] {market} 节假日年份覆盖不足，缺失 {missing}，已启动后台补齐")
            cls._schedule_asian_holiday_refresh(market, missing)

    @classmethod
    def _retry_empty_future_years(cls) -> None:
        now = cls._get_market_now("TW")
        for market in cls._ASIAN_MARKETS:
            market_now = cls._get_market_now(market)
            retry_years: list[int] = []
            with cls._asian_lock:
                year_map = dict(cls._asian_holidays.get(market, {}))
            for year, days in year_map.items():
                if days:
                    continue
                if year < now.year:
                    continue
                if cls._should_skip_holiday_refresh(market, year, market_now):
                    continue
                updated_at = cls._asian_holiday_updated_at.get((market, year))
                if updated_at is None:
                    retry_years.append(year)
                    continue
                if not days and (now - updated_at).days >= 7:
                    retry_years.append(year)
                    continue
                if days and (now - updated_at).days >= 14:
                    retry_years.append(year)
            if retry_years:
                cls._schedule_asian_holiday_refresh(market, retry_years)

    @classmethod
    def _ensure_market_year(cls, market: str, year: int) -> None:
        if market not in cls._ASIAN_MARKETS:
            return
        cls._bootstrap_asian_holidays()
        with cls._asian_lock:
            present = year in cls._asian_holidays.get(market, {})
        if not present and not cls._should_skip_holiday_refresh(market, year):
            cls._schedule_asian_holiday_refresh(market, [year])

    @classmethod
    def _schedule_asian_holiday_refresh(cls, market: str, years: list[int]) -> None:
        market = cls.normalize_market(market)
        if market not in cls._ASIAN_MARKETS:
            return

        now = cls._get_market_now(market)
        years = sorted({int(y) for y in years if not cls._should_skip_holiday_refresh(market, int(y), now)})
        if not years:
            return

        with cls._asian_lock:
            pending_years = [y for y in years if (market, y) not in cls._refresh_inflight]
            for y in pending_years:
                cls._refresh_inflight.add((market, y))
        if not pending_years:
            return

        def _on_success(result: Any) -> None:
            if not isinstance(result, dict):
                result = {}
            fetched = result.get("fetched", {})
            transient_failed = set(result.get("transient_failed", []))
            unpublished_years = {int(y) for y in result.get("unpublished_years", [])}
            now = cls.now(market)

            with cls._asian_lock:
                bucket = cls._asian_holidays.setdefault(market, {})
                for year in pending_years:
                    cls._refresh_inflight.discard((market, year))

                for year, days in fetched.items():
                    y = int(year)
                    normalized_days = cls._normalize_holiday_days(days)
                    bucket[y] = normalized_days
                    cls._asian_holiday_updated_at[(market, y)] = now
                    if y in unpublished_years:
                        cls._holiday_unpublished_until[(market, y)] = now + datetime.timedelta(
                            days=cls._SOURCE_UNPUBLISHED_RETRY_DAYS
                        )
                    try:
                        cls._save_holidays_to_store(market, y, normalized_days)
                    except CacheIOError as e:
                        log.warning(f"[交易日历][I/O] 节假日缓存写入失败({market} {y}): {e}")

            done_years = sorted(int(y) for y in fetched.keys())
            if done_years:
                log.info(f"[交易日历] {market} 节假日补齐完成: {done_years}")
            if transient_failed:
                retry_list = sorted(int(y) for y in transient_failed)
                log.info(f"[交易日历] {market} 节假日将在后续自动重试: {retry_list}")

        def _on_error(error_message: str) -> None:
            with cls._asian_lock:
                for year in pending_years:
                    cls._refresh_inflight.discard((market, year))
            log.warning(f"[交易日历][TASK] 亚洲节假日后台补齐任务失败({market}): {error_message}")

        _MARKET_CALENDAR_TASKS.run_background(
            f"holiday_refresh_{market}",
            lambda token: _fetch_asian_holiday_payload(cls, market, pending_years, token),
            on_success=_on_success,
            on_error=_on_error,
            task_id=task_registry.startup(
                f"holiday_refresh_{market}_{min(pending_years)}_{max(pending_years)}"
            ).task_id,
            timeout_sec=120.0,
            runner=task_manager,
        )

    @classmethod
    def _fetch_public_holidays(cls, market: str, year: int) -> set[str]:
        return fetch_public_holidays(
            market=cls.normalize_market(market),
            year=year,
            nager_country=cls._NAGER_COUNTRY,
            twse_include_keywords=cls._TWSE_INCLUDE_KEYWORDS,
            twse_exclude_keywords=cls._TWSE_EXCLUDE_KEYWORDS,
        )

    @classmethod
    def _extract_cached_trade_dates(
        cls,
        payload: Any,
        *,
        cur_month: str,
    ) -> tuple[set[str] | None, bool]:
        if not isinstance(payload, dict):
            return None, False

        dates = cls._normalize_holiday_days(payload.get("dates", []))
        if not dates:
            return None, False

        payload_month = str(payload.get("month", "")).strip()
        return dates, payload_month == cur_month

    @classmethod
    def _schedule_trade_dates_refresh(cls, cur_month: str) -> None:
        if cls._trade_dates_loading:
            return
        cls._trade_dates_loading = True

        def _task_runner_is_shutting_down() -> bool:
            return bool(getattr(task_manager, "is_shutting_down", False))

        def _save_trade_dates(cleaned: set[str]) -> None:
            if _task_runner_is_shutting_down():
                return
            from infra.storage import DataStore

            store = DataStore()
            if bool(getattr(store, "is_closed", False)):
                return
            try:
                store.save_json("trade_dates", {"month": cur_month, "dates": sorted(cleaned)})
            except (OSError, sqlite3.Error) as e:
                log.debug(f"[交易日历][I/O] trade_dates persist skipped: {e}")

        def _on_success(dates: Any) -> None:
            cls._trade_dates_loading = False
            cleaned = cls._normalize_holiday_days(dates)
            cls._trade_dates = cleaned
            _save_trade_dates(cleaned)

        def _on_error(error_message: str) -> None:
            cls._trade_dates_loading = False
            log.warning(f"[交易日历][NETWORK] 后台同步失败: {error_message}")

        _MARKET_CALENDAR_TASKS.run_background(
            "cn_trade_calendar_refresh",
            lambda token: _fetch_cn_trade_dates(cls, token),
            on_success=_on_success,
            on_error=_on_error,
            task_id=task_registry.startup("cn_trade_calendar_refresh").task_id,
            timeout_sec=120.0,
            runner=task_manager,
        )

    @classmethod
    def load_trade_dates(cls) -> set[str] | None:
        from infra.storage import DataStore

        now = cls.now("CN")
        cur_month = now.strftime("%Y-%m")

        try:
            data = DataStore().load_json("trade_dates")
            cached_dates, is_current_month = cls._extract_cached_trade_dates(data, cur_month=cur_month)
            if cached_dates:
                if not is_current_month:
                    cls._schedule_trade_dates_refresh(cur_month)
                return cached_dates
        except (OSError, sqlite3.Error, TypeError, ValueError) as e:
            log.debug(f"[交易日历][I/O] DataStore 读取失败: {e}")

        cache_file = os.path.join(cls._project_root(), "data", "Cache", "trade_dates.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cached_dates, is_current_month = cls._extract_cached_trade_dates(data, cur_month=cur_month)
                if cached_dates:
                    DataStore().save_json("trade_dates", data)
                    os.rename(cache_file, cache_file + ".migrated")
                    if not is_current_month:
                        cls._schedule_trade_dates_refresh(cur_month)
                    return cached_dates
            except (OSError, json.JSONDecodeError, TypeError) as e:
                log.debug(f"[交易日历][I/O] 旧 JSON 迁移失败: {e}")

        if cls._trade_dates_loading:
            return None
        cls._schedule_trade_dates_refresh(cur_month)
        return None

    @classmethod
    def is_trade_day(cls, day: Any = None, market: str = "CN") -> bool:
        market = cls.normalize_market(market)
        try:
            target_day = cls._coerce_date(day, market)
        except DataFormatError as e:
            log.warning(f"[交易日历][FORMAT] 交易日参数异常，已回退到今日: {e}")
            target_day = cls._get_market_now(market).date()

        if target_day.weekday() >= 5:
            return False

        if market == "CN":
            if cls._trade_dates is None:
                loaded_dates = cls.load_trade_dates()
                if loaded_dates is not None:
                    cls._trade_dates = loaded_dates
            if cls._trade_dates:
                return target_day.isoformat() in cls._trade_dates
            # 交易日历尚未确认时，对“今天”采用保守策略，避免节假日误触发盘中刷新。
            if target_day == cls.today("CN"):
                return False
            return True

        if market in cls._ASIAN_MARKETS:
            cls._ensure_market_year(market, target_day.year)
            with cls._asian_lock:
                year_holidays = cls._asian_holidays.get(market, {}).get(target_day.year)
            if year_holidays is None:
                return True
            year_holidays = apply_market_holiday_supplements(market, target_day.year, year_holidays)
            return target_day.isoformat() not in year_holidays

        return True

    @classmethod
    def get_latest_trade_date(cls, market: str = "CN", ref_date: Any = None) -> datetime.date:
        market = cls.normalize_market(market)
        cursor = cls._coerce_date(ref_date, market)
        if market == "CN":
            if cls._trade_dates is None:
                loaded_dates = cls.load_trade_dates()
                if loaded_dates is not None:
                    cls._trade_dates = loaded_dates
            if not cls._trade_dates:
                while cursor.weekday() >= 5:
                    cursor -= datetime.timedelta(days=1)
                return cursor
        for _ in range(40):
            if cls.is_trade_day(cursor, market):
                return cursor
            cursor -= datetime.timedelta(days=1)
        return cursor

    @classmethod
    def get_recent_trade_dates(cls, n: int = 20, ref_date: Any = None) -> list[str]:
        """获取最近 n 个交易日（含 ref_date 当天如果是交易日），返回 yyyyMMdd 格式列表（从近到远）。

        为什么要独立实现而不循环调用 is_trade_day：
        批量查询时直接在已排序的交易日历集合上切片，比逐日判断快得多。
        """
        market = "CN"
        try:
            today = cls._coerce_date(ref_date, market)
        except DataFormatError:
            today = cls._get_market_now(market).date()

        # 优先使用精确交易日历
        if cls._trade_dates is None:
            cls._trade_dates = cls.load_trade_dates()

        if cls._trade_dates:
            # _trade_dates 里是 ISO 格式 "YYYY-MM-DD"
            candidates = sorted(
                [d for d in cls._trade_dates if d <= today.isoformat()],
                reverse=True,
            )
            return [d.replace("-", "") for d in candidates[:n]]

        # 回退方案：跳过周末，近似估算（不含节假日修正）
        result: list[str] = []
        cursor = today
        # 安全上限：最多回溯 n*3 天，避免无限循环
        for _ in range(n * 3):
            if cursor.weekday() < 5:
                result.append(cursor.strftime("%Y%m%d"))
                if len(result) >= n:
                    break
            cursor -= datetime.timedelta(days=1)
        return result

    @classmethod
    def get_market_status(cls, market: str = "CN") -> str:
        market = cls.normalize_market(market)
        now = cls._get_market_now(market)
        if not cls.is_trade_day(now.date(), market):
            return "休市"

        hhmm = now.hour * 100 + now.minute
        phases = cls._MARKET_PHASES.get(market)
        if phases:
            for start, end, status in phases:
                if start <= hhmm < end:
                    return status
            if hhmm < phases[0][0]:
                return "盘前"
            return "盘后"

        sessions = cls._MARKET_SESSIONS.get(market, cls._MARKET_SESSIONS["CN"])
        first_start = sessions[0][0]
        last_end = sessions[-1][1]

        for start, end in sessions:
            if start <= hhmm <= end:
                return "交易中"

        if first_start < hhmm < last_end:
            return "午休"
        if hhmm < first_start:
            return "盘前"
        return "盘后"

    @classmethod
    def is_market_active(cls, market: str = "CN") -> bool:
        return cls.get_market_status(market) in cls._MARKET_ACTIVE_STATUSES

    @classmethod
    def is_quote_refresh_time(cls, market: str = "CN") -> bool:
        """是否允许刷新报价快照。

        对 A 股来说，午休虽然不是连续成交时段，但主流行情源仍会返回
        上午收盘后的最新快照，因此这里将“午休”也视为可刷新报价的时段。
        """
        return cls.get_market_status(market) in cls._MARKET_QUOTE_REFRESH_STATUSES


def shutdown_market_calendar_tasks(*, timeout_ms: int = 750) -> bool:
    """Cancel process-lifetime calendar refreshes before the shared runner exits."""
    completed = _MARKET_CALENDAR_TASKS.shutdown(timeout_ms=timeout_ms)
    with MarketCalendar._asian_lock:
        MarketCalendar._refresh_inflight.clear()
    MarketCalendar._trade_dates_loading = False
    return completed
