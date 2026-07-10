# -*- coding: utf-8 -*-
"""
core/lhb_pool_manager.py
龙虎榜 30 日滚动关注池 — 数据引擎

负责：
- 多日龙虎榜数据的持久化存储（JSON 缓存）
- 从 30 个交易日的全量记录中筛选符合条件的标的
- 淘汰超出 30 日窗口的历史数据
- 迁移旧的单日缓存（lhb_cache.json）到新池

筛选条件：30 个交易日内至少有一天同时满足：
  ① 上榜净买额 > 0
  ② 机构净买额 >= 0
"""

import copy
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import uuid
from contextlib import contextmanager

from core.ai_industry_chain_pool import load_cached_ai_industry_chain_stock_codes, normalize_ai_chain_code
from core.buy_point import BUY_POINT_STYLE_TEXT, calculate_buy_point_from_history
from core.logger import get_logger

log = get_logger(__name__)

# 用户定义的滚动窗口长度。
POOL_WINDOW = 30


@contextmanager
def _serialized_cache_write(cache_path: str):
    """Use a tiny sidecar SQLite transaction as a cross-process write mutex."""
    lock_dir = os.path.join(tempfile.gettempdir(), "vcp_hunter_write_locks")
    os.makedirs(lock_dir, exist_ok=True)
    lock_key = hashlib.sha256(os.path.basename(cache_path).encode("utf-8")).hexdigest()[:16]
    lock_path = os.path.join(lock_dir, f"lhb-pool-{lock_key}.sqlite3")
    connection = sqlite3.connect(lock_path, timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("BEGIN IMMEDIATE")
        yield
        connection.commit()
    finally:
        if connection.in_transaction:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
        connection.close()


class LhbPoolManager:
    """龙虎榜关注池数据引擎；写入按日期差量合并并跨实例串行化。"""

    _loaded_payload_lock = threading.RLock()
    _loaded_payload_cache: dict[str, tuple[tuple[int, int], dict]] = {}
    _stock_universe_provider = staticmethod(load_cached_ai_industry_chain_stock_codes)

    def __init__(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._cache_path = os.path.join(project_root, "data", "Cache", "lhb_pool_30d.json")
        self._legacy_pool_cache_path = os.path.join(project_root, "data", "Cache", "lhb_pool_20d.json")
        self._old_cache_path = os.path.join(project_root, "data", "Cache", "lhb_cache.json")
        self._data: dict[str, list[dict]] = {}  # date_str(yyyyMMdd) -> [records]
        self._day_meta: dict[str, dict] = {}  # date_str(yyyyMMdd) -> cache metadata
        self._last_auto_fetch_date: str = ""
        self._state_lock = threading.RLock()
        self._persisted_data: dict[str, list[dict]] = {}
        self._persisted_day_meta: dict[str, dict] = {}
        self._persisted_last_auto_fetch_date = ""
        self._clear_requested = False
        self._load()
        self._migrate_old_cache()

    # ================================================================
    # 持久化
    # ================================================================
    @staticmethod
    def _cache_file_signature(cache_path: str) -> tuple[int, int] | None:
        try:
            stat = os.stat(cache_path)
        except OSError:
            return None
        return (int(stat.st_size), int(stat.st_mtime_ns))

    @classmethod
    def _load_json_payload(cls, cache_path: str) -> dict:
        signature = cls._cache_file_signature(cache_path)
        if signature is None:
            return {}
        cache_key = os.path.abspath(cache_path)
        with cls._loaded_payload_lock:
            cached = cls._loaded_payload_cache.get(cache_key)
            if cached is not None and cached[0] == signature:
                return copy.deepcopy(cached[1])

        with open(cache_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raw = {}
        with cls._loaded_payload_lock:
            cls._loaded_payload_cache[cache_key] = (signature, copy.deepcopy(raw))
        return raw

    @classmethod
    def _remember_json_payload(cls, cache_path: str, payload: dict) -> None:
        signature = cls._cache_file_signature(cache_path)
        if signature is None:
            return
        cache_key = os.path.abspath(cache_path)
        with cls._loaded_payload_lock:
            cls._loaded_payload_cache[cache_key] = (signature, copy.deepcopy(payload))

    def _load(self):
        cache_path = self._cache_path
        if not os.path.exists(cache_path) and os.path.exists(self._legacy_pool_cache_path):
            cache_path = self._legacy_pool_cache_path
            log.info("[龙虎榜池] 检测到旧 20 日缓存，将作为 30 日窗口种子加载")
        if not os.path.exists(cache_path):
            return
        try:
            raw = self._load_json_payload(cache_path)
            self._data = raw.get("daily_data", {})
            self._day_meta = raw.get("day_meta", {})
            self._last_auto_fetch_date = raw.get("last_auto_fetch_date", "")
            self._repair_day_meta()
            if os.path.abspath(cache_path) == os.path.abspath(self._cache_path):
                self._remember_persisted_state()
            migrated_count = self._upgrade_legacy_foreign_display_cache()
            if migrated_count:
                self.save()
                log.info(f"[龙虎榜池] 已升级 {migrated_count} 条旧版外资席位摘要缓存")
            log.info(f"[龙虎榜池] 缓存加载成功，包含 {len(self._data)} 个交易日数据")
        except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError, json.JSONDecodeError) as e:
            log.warning(f"[龙虎榜池] 缓存加载失败，将重建: {e}")
            self._data = {}

    def _remember_persisted_state(self) -> None:
        self._persisted_data = copy.deepcopy(self._data)
        self._persisted_day_meta = copy.deepcopy(self._day_meta)
        self._persisted_last_auto_fetch_date = self._last_auto_fetch_date
        self._clear_requested = False

    @staticmethod
    def _read_uncached_payload(cache_path: str) -> dict:
        if not os.path.exists(cache_path):
            return {}
        with open(cache_path, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _build_full_foreign_display_from_tooltip(tooltip: str) -> str:
        tooltip_text = str(tooltip or "").strip()
        if not tooltip_text:
            return ""
        if tooltip_text == "当日未发现外资席位上榜":
            return "未现身"

        lines = [line.strip() for line in tooltip_text.splitlines() if line.strip()]
        if not lines:
            return ""

        summary_prefix = "外资合计："
        summary_line = lines[0]
        if not summary_line.startswith(summary_prefix):
            return ""

        summary = summary_line[len(summary_prefix) :].strip()
        short_parts: list[str] = []
        for line in lines[1:]:
            if "：" not in line:
                continue
            branch, detail = line.split("：", 1)
            detail = detail.strip()
            if detail.startswith("净买"):
                short_parts.append(f"{branch}+{detail[2:]}")
            elif detail.startswith("净卖"):
                short_parts.append(f"{branch}-{detail[2:]}")
            elif detail.startswith("平衡"):
                short_parts.append(f"{branch}±0")

        if short_parts:
            return f"{summary} | {' / '.join(short_parts)}"
        return summary

    def _upgrade_legacy_foreign_display_cache(self) -> int:
        updated_count = 0
        for records in self._data.values():
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                display = str(record.get("外资净买入") or "").strip()
                if "等" not in display or "席" not in display:
                    continue
                tooltip = record.get("_外资净买入_tooltip")
                full_display = self._build_full_foreign_display_from_tooltip(tooltip)
                if full_display and full_display != display:
                    record["外资净买入"] = full_display
                    updated_count += 1
        return updated_count

    @staticmethod
    def _to_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            if isinstance(value, str):
                value = value.strip().replace("%", "").replace("+", "")
                if value in {"", "-", "--"}:
                    return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _pool_sort_key(cls, row: dict) -> tuple:
        has_buy_point = 1 if str((row or {}).get("买点", "") or "").strip() else 0
        pct = cls._to_float((row or {}).get("涨幅%"), 0.0)
        recent_date = str((row or {}).get("_最近上榜_raw") or (row or {}).get("最近上榜", "") or "")
        if has_buy_point:
            return (1, pct, recent_date, 0.0)
        return (0, 0.0, recent_date, pct)

    @classmethod
    def sort_pool_rows_for_display(cls, rows) -> list[dict]:
        return sorted(list(rows or []), key=cls._pool_sort_key, reverse=True)

    def _build_day_meta(
        self,
        records: list[dict],
        *,
        source_count: int | None = None,
        validation_ref_date: str = "",
        probe_status: str = "unverified",
    ) -> dict:
        record_count = len(records) if isinstance(records, list) else 0
        return {
            "record_count": record_count,
            "source_count": record_count if source_count is None else self._to_int(source_count, record_count),
            "last_probe_ref_date": str(validation_ref_date or ""),
            "probe_status": str(probe_status or "unverified"),
        }

    def _normalize_day_meta_item(self, meta: dict | None, records: list[dict]) -> dict:
        normalized = self._build_day_meta(records)
        if not isinstance(meta, dict):
            return normalized

        actual_count = len(records) if isinstance(records, list) else 0
        normalized["record_count"] = actual_count
        normalized["source_count"] = self._to_int(meta.get("source_count"), actual_count)
        normalized["last_probe_ref_date"] = str(meta.get("last_probe_ref_date", "") or "")
        normalized["probe_status"] = str(
            meta.get("probe_status", normalized["probe_status"]) or normalized["probe_status"]
        )
        return normalized

    def _repair_day_meta(self):
        if not isinstance(self._day_meta, dict):
            self._day_meta = {}

        repaired_meta: dict[str, dict] = {}
        for date_str, records in self._data.items():
            safe_records = records if isinstance(records, list) else []
            repaired_meta[date_str] = self._normalize_day_meta_item(self._day_meta.get(date_str), safe_records)
        self._day_meta = repaired_meta

    def save(self):
        """合并当前实例的日期级变更后原子落盘。"""
        try:
            os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
            with self._state_lock:
                current_data = copy.deepcopy(self._data)
                current_meta = copy.deepcopy(self._day_meta)
                persisted_data = copy.deepcopy(self._persisted_data)
                persisted_meta = copy.deepcopy(self._persisted_day_meta)
                deleted_days = set(persisted_data).difference(current_data)
                dirty_days = {
                    day
                    for day in set(current_data).union(persisted_data)
                    if current_data.get(day) != persisted_data.get(day)
                    or current_meta.get(day) != persisted_meta.get(day)
                }
                last_fetch_changed = self._last_auto_fetch_date != self._persisted_last_auto_fetch_date

                with _serialized_cache_write(self._cache_path):
                    try:
                        cache_exists = os.path.exists(self._cache_path)
                        latest = self._read_uncached_payload(self._cache_path)
                    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                        log.warning(f"[龙虎榜池] 现有缓存不可读，将按当前实例状态重建: {exc}")
                        cache_exists = False
                        latest = {}

                    if not cache_exists and persisted_data:
                        latest = {
                            "last_auto_fetch_date": self._persisted_last_auto_fetch_date,
                            "daily_data": persisted_data,
                            "day_meta": persisted_meta,
                        }

                    if self._clear_requested:
                        merged_data: dict[str, list[dict]] = {}
                        merged_meta: dict[str, dict] = {}
                    else:
                        stored_data = latest.get("daily_data", {})
                        stored_meta = latest.get("day_meta", {})
                        merged_data = copy.deepcopy(stored_data) if isinstance(stored_data, dict) else {}
                        merged_meta = copy.deepcopy(stored_meta) if isinstance(stored_meta, dict) else {}
                        for day in deleted_days:
                            merged_data.pop(day, None)
                            merged_meta.pop(day, None)
                        for day in dirty_days:
                            if day in current_data:
                                merged_data[day] = copy.deepcopy(current_data[day])
                                merged_meta[day] = copy.deepcopy(current_meta.get(day, {}))

                    latest_last_fetch = str(latest.get("last_auto_fetch_date", "") or "")
                    merged_last_fetch = (
                        self._last_auto_fetch_date if self._clear_requested or last_fetch_changed else latest_last_fetch
                    )
                    payload = {
                        "version": 2,
                        "last_auto_fetch_date": merged_last_fetch,
                        "daily_data": merged_data,
                        "day_meta": merged_meta,
                    }
                    temp_path = f"{self._cache_path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
                    try:
                        with open(temp_path, "w", encoding="utf-8") as stream:
                            json.dump(payload, stream, ensure_ascii=False)
                            stream.flush()
                            os.fsync(stream.fileno())
                        with open(temp_path, "r", encoding="utf-8") as stream:
                            json.load(stream)
                        os.replace(temp_path, self._cache_path)
                    finally:
                        if os.path.exists(temp_path):
                            try:
                                os.remove(temp_path)
                            except OSError:
                                pass

                self._data = merged_data
                self._day_meta = merged_meta
                self._last_auto_fetch_date = merged_last_fetch
                self._remember_persisted_state()
                self._remember_json_payload(self._cache_path, payload)
        except (PermissionError, OSError, sqlite3.Error, TypeError, ValueError) as e:
            log.error(f"[龙虎榜池] 缓存保存失败: {e}")

    def _migrate_old_cache(self):
        """把旧的单日 lhb_cache.json 数据迁移到新池中，然后删除旧文件"""
        if not os.path.exists(self._old_cache_path):
            return
        try:
            with open(self._old_cache_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            date_str = old.get("date_str", "")
            rows = old.get("rows", [])
            if date_str and rows and date_str not in self._data:
                # 旧缓存直接平移，不再做格式转换（资金共振字段已废弃）
                self.add_day(date_str, rows)
                self.save()
                log.info(f"[龙虎榜池] 成功迁移旧缓存 {date_str}，{len(rows)} 条记录")
            # 清理旧缓存文件
            os.remove(self._old_cache_path)
            log.info("[龙虎榜池] 旧缓存 lhb_cache.json 已删除")
        except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError, json.JSONDecodeError) as e:
            log.warning(f"[龙虎榜池] 旧缓存迁移失败（无影响）: {e}")

    # ================================================================
    # 数据管理
    # ================================================================
    @staticmethod
    def _record_stock_code(record: dict) -> str:
        if not isinstance(record, dict):
            return ""
        return normalize_ai_chain_code(
            record.get("代码")
            or record.get("股票代码")
            or record.get("证券代码")
            or record.get("stock_code")
            or record.get("code")
        )

    def _resolve_stock_universe_codes(self) -> set[str]:
        provider = getattr(self, "stock_universe_provider", None)
        if not callable(provider):
            provider = getattr(type(self), "_stock_universe_provider", None)
        if not callable(provider):
            return set()
        try:
            return {code for code in (normalize_ai_chain_code(value) for value in provider()) if code}
        except (FileNotFoundError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning(f"[龙虎榜池] AI产业链股票池不可用，按空股票池处理: {exc}")
            return set()

    def _filter_records_to_stock_universe(self, records: list[dict]) -> list[dict]:
        stock_codes = self._resolve_stock_universe_codes()
        if not stock_codes:
            return []
        return [record for record in (records or []) if self._record_stock_code(record) in stock_codes]

    def add_day(self, date_str: str, records: list[dict], meta: dict | None = None):
        """写入某一天的龙虎榜数据"""
        safe_records = self._filter_records_to_stock_universe(records if isinstance(records, list) else [])
        with self._state_lock:
            self._data[date_str] = safe_records
            self._day_meta[date_str] = self._normalize_day_meta_item(meta, safe_records)
        # 不在这里 save()，由调用方决定何时批量保存（减少 IO）

    def get_cached_dates(self) -> set[str]:
        return set(self._data.keys())

    def get_cached_record_count(self, date_str: str) -> int:
        records = self._data.get(date_str, [])
        return len(records) if isinstance(records, list) else 0

    def get_day_meta(self, date_str: str) -> dict:
        meta = self._day_meta.get(date_str, {})
        return dict(meta) if isinstance(meta, dict) else {}

    def get_missing_dates(self, required_dates: list[str]) -> list[str]:
        """找出 required_dates 中还没有缓存的日期"""
        cached = self.get_cached_dates()
        return [d for d in required_dates if d not in cached]

    def get_dates_pending_validation(self, required_dates: list[str], validation_ref_date: str) -> list[str]:
        """找出当前窗口里需要做轻量校验的日期。"""
        pending_dates: list[str] = []
        validation_ref = str(validation_ref_date or "")

        for date_str in required_dates:
            if date_str not in self._data:
                continue

            cached_count = self.get_cached_record_count(date_str)
            meta = self._day_meta.get(date_str)
            if not isinstance(meta, dict):
                pending_dates.append(date_str)
                continue

            if self._to_int(meta.get("record_count"), -1) != cached_count:
                pending_dates.append(date_str)
                continue

            if str(meta.get("last_probe_ref_date", "") or "") != validation_ref:
                pending_dates.append(date_str)

        return pending_dates

    def mark_day_probe(self, date_str: str, source_count: int, validation_ref_date: str, status: str = "ok"):
        """记录某一天最新一次轻量校验结果。"""
        with self._state_lock:
            if date_str not in self._data:
                return

            meta = self._normalize_day_meta_item(self._day_meta.get(date_str), self._data.get(date_str, []))
            meta["source_count"] = self._to_int(source_count, meta["record_count"])
            meta["last_probe_ref_date"] = str(validation_ref_date or "")
            meta["probe_status"] = str(status or "ok")
            self._day_meta[date_str] = meta

    def prune(self, valid_dates: list[str]):
        """裁剪掉不在 valid_dates 窗口内的历史数据"""
        with self._state_lock:
            valid_set = set(valid_dates)
            to_remove = [d for d in self._data if d not in valid_set]
            if to_remove:
                for d in to_remove:
                    del self._data[d]
                    self._day_meta.pop(d, None)
                self.save()
                log.info(f"[龙虎榜池] 裁剪了 {len(to_remove)} 天过期数据: {sorted(to_remove)}")

    def clear_all(self):
        """清空全部缓存数据（手动全量刷新时使用）"""
        with self._state_lock:
            self._data.clear()
            self._day_meta.clear()
            self._clear_requested = True
            self.save()

    @property
    def last_auto_fetch_date(self) -> str:
        with self._state_lock:
            return self._last_auto_fetch_date

    @last_auto_fetch_date.setter
    def last_auto_fetch_date(self, value: str):
        with self._state_lock:
            self._last_auto_fetch_date = value

    # ================================================================
    # 池计算
    # ================================================================

    @staticmethod
    def _is_bse_code(code: str) -> bool:
        """判断是否为北交所或B股（代码首位为 4/8/9，或前两位为 43/83/87）"""
        return code[:2] in ("43", "83", "87") or code[0] == "9"

    @staticmethod
    def _is_st_stock(name: str) -> bool:
        """判断是否为 ST 股票（名称含 ST，不区分大小写）"""
        return "ST" in name.upper()

    @staticmethod
    def _count_rps250_eligible_symbols(data_provider) -> int:
        cache_data = getattr(data_provider, "cache_data", {}) or {}
        count = 0
        for df in cache_data.values():
            try:
                if df is not None and len(df) >= 250:
                    count += 1
            except TypeError:
                continue
        return count

    @staticmethod
    def _coerce_kline_frame(frame):
        if frame is None or hasattr(frame, "empty"):
            return frame

        raw_columns = getattr(frame, "columns", [])
        if raw_columns is None:
            raw_columns = []
        columns = [str(column) for column in list(raw_columns)]
        if not columns or not hasattr(frame, "__getitem__"):
            return frame

        class _SeriesAdapter:
            def __init__(self, values):
                self._values = list(values)

            @property
            def iloc(self):
                return self

            def __getitem__(self, index):
                return self._values[index]

            def tail(self, count):
                return _SeriesAdapter(self._values[-int(count) :])

            def astype(self, target_type):
                return _SeriesAdapter(target_type(value) for value in self._values)

            def tolist(self):
                return list(self._values)

        class _FrameAdapter:
            def __init__(self, source, column_names):
                self._source = source
                self.columns = column_names

            @property
            def empty(self):
                is_empty = getattr(self._source, "is_empty", None)
                if callable(is_empty):
                    return bool(is_empty())
                try:
                    return len(self._source) == 0
                except TypeError:
                    return False

            @property
            def index(self):
                if "date" in self.columns:
                    return self["date"]
                if "\u65e5\u671f" in self.columns:
                    return self["\u65e5\u671f"]
                return range(len(self))

            def __len__(self):
                return len(self._source)

            def __getitem__(self, column):
                series = self._source[column]
                to_list = getattr(series, "to_list", None)
                if callable(to_list):
                    values = to_list()
                else:
                    tolist = getattr(series, "tolist", None)
                    values = tolist() if callable(tolist) else list(series)
                return _SeriesAdapter(values)

            def get(self, column, default=None):
                return self[column] if column in self.columns else default

        return _FrameAdapter(frame, columns)

    def _collect_qualifying_codes(
        self,
        data_snapshot: dict[str, list[dict]],
        stock_universe_codes: set[str],
    ) -> tuple[set[str], dict[str, int]]:
        qualifying_codes: set[str] = set()
        code_hit_count: dict[str, int] = {}

        for records in data_snapshot.values():
            for rec in records:
                code = self._record_stock_code(rec)
                name = rec.get("名称", "")
                if not code or code not in stock_universe_codes:
                    continue
                if self._is_bse_code(code) or self._is_st_stock(name):
                    continue

                try:
                    net_buy = float(rec.get("上榜净买额(万)", 0))
                except (ValueError, TypeError):
                    net_buy = 0.0
                try:
                    jg_net = float(rec.get("机构净买(万)", 0))
                except (ValueError, TypeError):
                    jg_net = 0.0

                if net_buy > 0 and jg_net >= 0:
                    qualifying_codes.add(code)
                    code_hit_count[code] = code_hit_count.get(code, 0) + 1

        return qualifying_codes, code_hit_count

    def _filter_codes_by_rps250(self, qualifying_codes: set[str], data_provider, engine) -> set[str]:
        if engine is None:
            return qualifying_codes

        rps_bundle = engine.get_precomputed_rps()
        if rps_bundle is None:
            return qualifying_codes

        rps250_dict = rps_bundle.get("rps250", {})
        if not rps250_dict:
            return qualifying_codes

        disqualify_missing_rps = True
        eligible_count = self._count_rps250_eligible_symbols(data_provider)
        minimum_coverage = max(1000, int(eligible_count * 0.5)) if eligible_count >= 500 else 0
        if eligible_count >= 500 and len(rps250_dict) < minimum_coverage:
            disqualify_missing_rps = False
            log.warning(f"[龙虎榜池] RPS缓存覆盖不足({len(rps250_dict)}/{eligible_count})，本次缺失RPS不按次新剔除")

        disqualified_rps: set[str] = set()
        below_threshold_rps: set[str] = set()
        for code in qualifying_codes:
            rps_val = rps250_dict.get(code)
            if rps_val is None:
                if disqualify_missing_rps:
                    disqualified_rps.add(code)
                continue
            if rps_val < 85:
                disqualified_rps.add(code)
                below_threshold_rps.add(code)

        if not disqualified_rps:
            return qualifying_codes

        qualifying_codes -= disqualified_rps
        if disqualify_missing_rps:
            log.info(f"[龙虎榜池] 剔除次新及RPS250<85共 {len(disqualified_rps)} 只")
        else:
            log.info(f"[龙虎榜池] RPS缓存覆盖异常，当前仅剔除RPS250<85共 {len(below_threshold_rps)} 只")
        return qualifying_codes

    def _attach_price_history(self, record: dict, code: str, data_provider) -> None:
        try:
            df_k = self._coerce_kline_frame(data_provider.get_data(code))
            if df_k is None or df_k.empty or len(df_k) < 20:
                return

            if "date" in df_k.columns:
                last_date = str(df_k["date"].iloc[-1])[:10]
            elif "日期" in df_k.columns:
                last_date = str(df_k["日期"].iloc[-1])[:10]
            else:
                last_date = str(df_k.index[-1])[:10]

            hist_list = df_k["close"].tail(20).astype(float).tolist()
            record["_history_20"] = hist_list
            record["_history_date"] = last_date

            try:
                open_column = "open" if "open" in df_k.columns else "close"
                last_open = float(df_k[open_column].iloc[-1])
            except (AttributeError, KeyError, IndexError, TypeError, ValueError):
                last_open = hist_list[-1]

            record["买点"] = calculate_buy_point_from_history(
                history=hist_list,
                open_price=last_open,
                close_price=hist_list[-1],
                style=BUY_POINT_STYLE_TEXT,
            )
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
            log.debug(f"[龙虎榜池] 计算 {code} 股价位置失败: {e}")

    def _build_latest_pool_records(
        self,
        data_snapshot: dict[str, list[dict]],
        qualifying_codes: set[str],
        code_hit_count: dict[str, int],
        data_provider,
    ) -> dict[str, dict]:
        latest_records: dict[str, dict] = {}

        for date_str in sorted(data_snapshot.keys(), reverse=True):
            for rec in data_snapshot[date_str]:
                code = self._record_stock_code(rec)
                if code not in qualifying_codes or code in latest_records:
                    continue

                try:
                    net_buy = float(rec.get("上榜净买额(万)", 0))
                    jg_net = float(rec.get("机构净买(万)", 0))
                except (ValueError, TypeError):
                    net_buy = jg_net = 0.0
                if not (net_buy > 0 and jg_net >= 0):
                    continue

                record = dict(rec)
                record["代码"] = code
                record["买点"] = ""
                record["上榜次数"] = code_hit_count.get(code, 1)
                record["最近上榜"] = record.get("上榜日期", date_str)
                if data_provider is not None:
                    self._attach_price_history(record, code, data_provider)
                latest_records[code] = record

        return latest_records

    def compute_pool(self, data_provider=None, engine=None) -> list[dict]:
        """从缓存的多日数据中计算关注池。

        筛选逻辑：
        1. 遍历所有日期的所有记录
        2. 剔除 ST 股、北交所股票（纯本地字符串判断）
        3. 找出在任何一天同时满足 上榜净买额>0 AND 机构净买>=0 的股票代码
        4. 如果有 data_provider，剔除 K 线行数 < 250 的次新股
        5. 对符合条件的股票，提取最近一次上榜的详细数据
        6. 附加"上榜次数"字段（满足条件的天数）

        参数:
            data_provider: 可选，传入可用的 DataProvider 实例，
                           用于通过 K 线缓存行数判断上市天数。
                           没有传入则跳过次新股过滤。

        返回：按 买点触发优先 → 买点组内涨幅%降序 → 非买点按最近上榜日降序 排列的列表
        """
        with self._state_lock:
            data_snapshot = dict(self._data)
        if not data_snapshot:
            return []

        stock_universe_codes = self._resolve_stock_universe_codes()
        if not stock_universe_codes:
            return []

        qualifying_codes, code_hit_count = self._collect_qualifying_codes(data_snapshot, stock_universe_codes)
        if not qualifying_codes:
            return []

        qualifying_codes = self._filter_codes_by_rps250(qualifying_codes, data_provider, engine)
        if not qualifying_codes:
            return []

        latest_records = self._build_latest_pool_records(
            data_snapshot,
            qualifying_codes,
            code_hit_count,
            data_provider,
        )
        # 排序：优先展示买点触发，买点组内按涨跌幅倒序；非买点仍按最近上榜日由近到远。
        result = list(latest_records.values())
        result = self.sort_pool_rows_for_display(result)

        log.debug(f"[龙虎榜池] 池计算完成: {len(data_snapshot)} 天数据中，{len(qualifying_codes)} 只标的入池")

        return result
