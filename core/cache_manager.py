# -*- coding: utf-8 -*-
"""
core/cache_manager.py
File-level cache management for JSON cache restore/save and stale cache cleanup.
"""

import datetime
import os
import re

from core.exceptions import BusinessRuleError, CacheIOError, DataFormatError
from core.json_cache import load_json_file, remove_cache_file, save_json_file
from core.logger import get_logger
from core.runtime_paths import RPS_CACHE_FILE, ensure_cache_dir

log = get_logger(__name__)


class CacheManager:
    """Manage disk-backed caches without touching UI concerns directly."""

    def __init__(self):
        self.cache_dir = ensure_cache_dir()
        self.rps_path = RPS_CACHE_FILE

    @staticmethod
    def _legacy_pickle_path(path: str) -> str:
        return f"{os.path.splitext(path)[0]}.pkl"

    def _load_json(self, path: str):
        return load_json_file(path)

    def _save_json(self, path: str, data) -> None:
        save_json_file(path, data)

    @staticmethod
    def _count_valid_rps_values(rps120) -> int:
        if isinstance(rps120, dict):
            count = 0
            for value in rps120.values():
                try:
                    float_value = float(value)
                except (TypeError, ValueError):
                    continue
                if float_value == float_value:
                    count += 1
            return count

        if hasattr(rps120, "notna"):
            try:
                return int(rps120.notna().sum())
            except (AttributeError, TypeError, ValueError):
                return 0

        return 0

    @staticmethod
    def _count_rps250_eligible_symbols(cache_data: dict) -> int:
        count = 0
        for df in (cache_data or {}).values():
            try:
                if df is not None and len(df) >= 250:
                    count += 1
            except TypeError:
                continue
        return count

    @classmethod
    def _should_rebuild_rps_payload(cls, payload: dict, data_provider=None) -> tuple[bool, int, int]:
        cache_data = getattr(data_provider, "cache_data", {}) or {}
        eligible_count = cls._count_rps250_eligible_symbols(cache_data)
        if eligible_count < 500:
            return False, 0, eligible_count

        loaded_count = cls._count_valid_rps_values((payload or {}).get("rps250"))
        minimum_count = max(1000, int(eligible_count * 0.5))
        return loaded_count < minimum_count, loaded_count, eligible_count

    @staticmethod
    def _extract_latest_trade_date_from_frame(df) -> str:
        if df is None:
            return ""

        try:
            if len(df) <= 0:
                return ""
        except TypeError:
            return ""

        columns = getattr(df, "columns", None)
        if columns is not None and len(columns) > 0:
            for column_name in ("datetime", "date", "日期"):
                if column_name not in columns:
                    continue
                try:
                    series = df[column_name]
                    if hasattr(series, "to_list"):
                        last_value = series.to_list()[-1]
                    else:
                        last_value = series.iloc[-1]
                except (AttributeError, IndexError, KeyError, TypeError):
                    continue

                try:
                    if hasattr(last_value, "strftime"):
                        return last_value.strftime("%Y%m%d")
                    date_str = str(last_value).strip().replace("-", "")[:8]
                    return date_str if len(date_str) == 8 and date_str.isdigit() else ""
                except (AttributeError, TypeError, ValueError):
                    continue

        try:
            index = getattr(df, "index", None)
            if index is None or len(index) <= 0:
                return ""
            last_value = index[-1]
        except (IndexError, TypeError):
            return ""

        try:
            if hasattr(last_value, "strftime"):
                return last_value.strftime("%Y%m%d")
            date_str = str(last_value).strip().replace("-", "")[:8]
            return date_str if len(date_str) == 8 and date_str.isdigit() else ""
        except (AttributeError, TypeError, ValueError):
            return ""

    @staticmethod
    def _infer_latest_rps_trade_date(cache_data: dict) -> str:
        latest_date = ""
        for df in (cache_data or {}).values():
            date_str = CacheManager._extract_latest_trade_date_from_frame(df)
            if date_str and date_str > latest_date:
                latest_date = date_str

        return latest_date

    def _rebuild_rps_from_cache(self, engine, data_provider, set_status_callback=None) -> bool:
        cache_data = getattr(data_provider, "cache_data", {}) or {}
        all_data = {}
        for code, df in cache_data.items():
            try:
                if df is not None and len(df) >= 60:
                    all_data[code] = df
            except TypeError:
                continue

        if not all_data:
            raise BusinessRuleError("local cache has no eligible symbols for rps rebuild")

        target_date = self._infer_latest_rps_trade_date(all_data)
        if not target_date:
            raise BusinessRuleError("unable to infer latest trade date from local cache")

        rps_matrix = engine.build_rps_matrix(all_data, target_date, target_date)
        if not rps_matrix:
            raise BusinessRuleError(f"rps rebuild returned empty matrix for {target_date}")

        resolved_date = list(rps_matrix.keys())[-1]
        resolved_rps = rps_matrix[resolved_date] or {}
        rps120 = resolved_rps.get("rps120")
        rps250 = resolved_rps.get("rps250")
        if rps120 is None or rps250 is None:
            raise BusinessRuleError("rps120/rps250 missing in rebuilt payload")

        payload = {"date": resolved_date, "rps120": rps120, "rps250": rps250}
        self._save_json(self.rps_path, payload)
        remove_cache_file(self._legacy_pickle_path(self.rps_path))
        engine.set_precomputed_rps(resolved_date, rps120, rps250)
        count = self._count_valid_rps_values(rps120)
        log.info(f"[RPS] 本地缓存重建预计算RPS成功(基准日:{resolved_date}, 仅{count}条有效)")

        if set_status_callback:
            set_status_callback(f"RPS cache rebuilt: {resolved_date}, {count} symbols")
        return True

    def try_load_rps_from_disk(self, engine, data_provider=None, set_status_callback=None):
        """
        Try loading the precomputed RPS bundle from JSON cache.
        If JSON cache is missing, optionally rebuild it from local market cache.
        """
        try:
            if os.path.exists(self.rps_path):
                payload = self._load_json(self.rps_path)
                cached_date = payload.get("date", "")
                rps120 = payload.get("rps120")
                rps250 = payload.get("rps250")
                if rps120 is None or rps250 is None:
                    raise BusinessRuleError("rps120/rps250 missing in cache payload")

                should_rebuild, loaded_count, eligible_count = self._should_rebuild_rps_payload(
                    payload,
                    data_provider=data_provider,
                )
                if should_rebuild and data_provider is not None:
                    log.warning(
                        f"[RPS][RULE] 磁盘预计算RPS覆盖不足({loaded_count}/{eligible_count})，"
                        "尝试基于本地K线缓存重建"
                    )
                    try:
                        if self._rebuild_rps_from_cache(
                            engine,
                            data_provider,
                            set_status_callback=set_status_callback,
                        ):
                            return
                    except BusinessRuleError as exc:
                        log.warning(f"[RPS][RULE] 预计算RPS重建失败，回退到磁盘缓存: {exc}")

                engine.set_precomputed_rps(cached_date, rps120, rps250)
                remove_cache_file(self._legacy_pickle_path(self.rps_path))
                count = self._count_valid_rps_values(rps120)
                log.info(f"[RPS] 从磁盘加载预计算RPS成功(基准日:{cached_date}, 仅{count}条有效)")

                if set_status_callback:
                    set_status_callback(f"RPS cache loaded: {cached_date}, {count} symbols")
                return

            if data_provider is not None:
                self._rebuild_rps_from_cache(
                    engine,
                    data_provider,
                    set_status_callback=set_status_callback,
                )
        except CacheIOError as exc:
            log.error(f"[RPS][I/O] 磁盘加载失败: {exc}")
        except DataFormatError as exc:
            log.error(f"[RPS][FORMAT] 缓存格式异常: {exc}")
        except BusinessRuleError as exc:
            log.warning(f"[RPS][RULE] 缓存不可用: {exc}")

    def save_rt_cache(self, table):
        """Persist today's RT monitor rows into JSON cache."""
        try:
            rows = []
            headers = []

            if hasattr(table, "model") and getattr(table, "model", lambda: None)():
                model = table.model()
                if hasattr(model, "sourceModel"):
                    model = model.sourceModel()

                if hasattr(model, "row_data"):
                    if not model.row_data:
                        return
                    headers = model.headers if hasattr(model, "headers") else []
                    for row_dict in model.row_data:
                        rows.append([str(row_dict.get(header, "")) for header in headers])

            if not rows:
                return

            if rows and rows[0]:
                first_cell = rows[0][0]
                if len(first_cell) > 10 or "(" in first_cell or "," in first_cell:
                    raise BusinessRuleError("abnormal first cell value in rt cache")

            data = {
                "date": datetime.date.today().isoformat(),
                "version": 2,
                "rows": rows,
                "headers": headers,
            }
            path = os.path.join(
                self.cache_dir, f"rt_monitor_{datetime.date.today().isoformat()}.json"
            )
            self._save_json(path, data)
            remove_cache_file(self._legacy_pickle_path(path))
            log.info(f"[盘中缓存] 已保存{len(rows)}条信号到 {os.path.basename(path)}")

            self._cleanup_old_rt_caches(10)
        except CacheIOError as exc:
            log.error(f"[盘中缓存][I/O] 保存失败: {exc}")
        except DataFormatError as exc:
            log.error(f"[盘中缓存][FORMAT] 保存前校验失败: {exc}")
        except BusinessRuleError as exc:
            log.warning(f"[盘中缓存][RULE] 跳过保存: {exc}")

    def load_rt_cache(self, table, set_status_callback=None):
        """Restore the most recent RT monitor JSON cache on startup."""
        path = None
        for days_ago in range(10):
            check_date = datetime.date.today() - datetime.timedelta(days=days_ago)
            candidate = os.path.join(
                self.cache_dir, f"rt_monitor_{check_date.isoformat()}.json"
            )
            if os.path.exists(candidate):
                path = candidate
                break

        if not path:
            return

        try:
            data = self._load_json(path)

            raw_rows = data.get("rows", [])
            if not raw_rows:
                raise BusinessRuleError("rows is empty")
            cache_date = data.get("date", "?")

            if hasattr(table, "model") and getattr(table, "model", lambda: None)():
                model = table.model()
                if hasattr(model, "sourceModel"):
                    model = model.sourceModel()

                if hasattr(model, "update_data") and hasattr(model, "headers"):
                    historical_headers = data.get("headers", [])
                    effective_headers = historical_headers if historical_headers else model.headers

                    final_data = []
                    for row_vals in raw_rows:
                        if (
                            isinstance(row_vals, (list, tuple))
                            and len(row_vals) == 2
                            and isinstance(row_vals[0], (list, tuple))
                        ):
                            row_vals = row_vals[0]

                        row_dict = {}
                        for column_index, value in enumerate(row_vals):
                            if column_index < len(effective_headers):
                                row_dict[effective_headers[column_index]] = value
                        final_data.append(row_dict)

                    model.update_data(final_data)
                    if set_status_callback:
                        set_status_callback(f"RT cache restored ({cache_date}, {len(raw_rows)} rows)")
                    return

            log.warning("[RT cache] table model not available, skip restore")
        except CacheIOError as exc:
            log.error(f"[盘中缓存][I/O] 加载失败: {exc}")
        except DataFormatError as exc:
            log.error(f"[盘中缓存][FORMAT] 加载失败: {exc}")
        except BusinessRuleError as exc:
            log.warning(f"[盘中缓存][RULE] 缓存不可用: {exc}")

    def _cleanup_old_rt_caches(self, retention_days=10):
        """Remove expired RT JSON cache and leftover legacy pickle files."""
        today = datetime.date.today()
        for filename in os.listdir(self.cache_dir):
            if not filename.startswith("rt_monitor_"):
                continue

            matched = re.search(r"rt_monitor_(\d{4}-\d{2}-\d{2})\.(json|pkl)$", filename)
            if not matched:
                continue

            try:
                file_date = datetime.datetime.strptime(
                    matched.group(1), "%Y-%m-%d"
                ).date()
                if (today - file_date).days > retention_days or filename.endswith(".pkl"):
                    os.remove(os.path.join(self.cache_dir, filename))
            except (ValueError, OSError) as exc:
                log.debug(f"[缓存管理] 清理旧缓存 {filename} 失败: {exc}")
