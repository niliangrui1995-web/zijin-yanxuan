import concurrent.futures
import datetime as dt
import gc
import os
import random
import time

import pandas as pd

from core.json_cache import remove_cache_file
from core.logger import get_logger
from core.market_calendar import MarketCalendar
from domains.quotes.tdx_name_map import (
    TNF_CODE_OFFSET,
    TNF_NAME_FIELD_LEN,
    TNF_NAME_FILES,
    TNF_NAME_OFFSET,
    TNF_RECORD_SIZE,
    decode_tnf_name,
    is_placeholder_name,
    normalize_code_name_targets,
    parse_tnf_name_file,
)
from vcp.constants import DATE_FMT, INCREMENTAL_BARS, MARKET_SYNC_WORKERS, MAX_HISTORY_BARS
from vcp.data_provider_cache import load_cache_from_disk
from vcp.utils import ensure_pandas_dataframe

_log = get_logger(__name__)


def _resolve_market_sync_workers(*, offline: bool, requested_max_workers=None) -> int:
    default_workers = 20 if offline else MARKET_SYNC_WORKERS
    if requested_max_workers is None:
        return default_workers
    try:
        requested = int(requested_max_workers)
    except (TypeError, ValueError):
        return default_workers
    if requested <= 0:
        return default_workers
    return max(1, min(default_workers, requested))


def _normalize_trade_date(value) -> str:
    if value is None:
        return ""
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return ""
    if pd.isna(timestamp):
        return ""
    return timestamp.strftime(DATE_FMT)


def _cached_frame_trade_date(frame) -> str:
    if frame is None:
        return ""
    try:
        if isinstance(frame, pd.DataFrame):
            if frame.empty:
                return ""
            date_column = next((name for name in ("datetime", "date", "trade_date") if name in frame.columns), None)
            raw_date = frame[date_column].max() if date_column else frame.index.max()
        else:
            if getattr(frame, "height", 0) <= 0:
                return ""
            columns = tuple(getattr(frame, "columns", ()) or ())
            date_column = next((name for name in ("datetime", "date", "trade_date") if name in columns), None)
            if not date_column:
                return ""
            get_column = getattr(frame, "get_column", None)
            series = get_column(date_column) if callable(get_column) else frame[date_column]
            raw_date = series.max()
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
        return ""
    return _normalize_trade_date(raw_date)


def _requested_cache_has_coverage(cache_data, requested_codes) -> bool:
    cache = cache_data or {}
    for code in requested_codes:
        frame = cache.get(code)
        if frame is None:
            return False
        if isinstance(frame, pd.DataFrame):
            if frame.empty:
                return False
        elif getattr(frame, "height", 0) <= 0:
            return False
    return True


def _requested_cached_trade_date(cache_data, requested_codes) -> str:
    """Return the oldest cached date only when every requested code is covered."""
    oldest_date = ""
    cache = cache_data or {}
    for code in requested_codes:
        cached_date = _cached_frame_trade_date(cache.get(code))
        if not cached_date:
            return ""
        if not oldest_date or cached_date < oldest_date:
            oldest_date = cached_date
    return oldest_date


class TdxDataProviderHistoryMixin:
    _TNF_RECORD_SIZE = TNF_RECORD_SIZE
    _TNF_CODE_OFFSET = TNF_CODE_OFFSET
    _TNF_NAME_OFFSET = TNF_NAME_OFFSET
    _TNF_NAME_FIELD_LEN = TNF_NAME_FIELD_LEN
    _TNF_NAME_FILES = TNF_NAME_FILES
    _MANUAL_NAME_ALIASES = {
        "603196": "\u749e\u6e90\u6750\u6599",
    }

    @staticmethod
    def _is_placeholder_name(code, name) -> bool:
        return is_placeholder_name(code, name)

    @staticmethod
    def _normalize_code_name_targets(codes) -> list[str]:
        return normalize_code_name_targets(codes)

    @classmethod
    def _decode_tnf_name(cls, raw_name: bytes) -> str:
        return decode_tnf_name(raw_name)

    @classmethod
    def _parse_tnf_name_file(cls, tnf_path: str) -> dict[str, str]:
        return parse_tnf_name_file(tnf_path)

    def _load_local_tdx_name_map(self) -> dict[str, str]:
        cached_map = getattr(self, "_local_tdx_name_map_cache", None)
        if isinstance(cached_map, dict) and cached_map:
            return dict(cached_map)

        vipdoc = getattr(self, "tdx_vipdoc", "") or ""
        if not vipdoc:
            self._local_tdx_name_map_cache = {}
            return {}

        tdx_root = os.path.dirname(vipdoc)
        hq_cache_dir = os.path.join(tdx_root, "T0002", "hq_cache")
        merged_map: dict[str, str] = {}
        for filename in self._TNF_NAME_FILES:
            merged_map.update(self._parse_tnf_name_file(os.path.join(hq_cache_dir, filename)))

        self._local_tdx_name_map_cache = dict(merged_map)
        if merged_map:
            _log.info(f"[离线模式] 已从本地证券主表解析 {len(merged_map)} 只标的名称")
        return dict(merged_map)

    @classmethod
    def _parse_tnf_name_file_for_codes(cls, tnf_path: str, target_codes: set[str]) -> dict[str, str]:
        if not target_codes:
            return {}
        return parse_tnf_name_file(tnf_path, target_codes=set(target_codes or set()))

    def _load_local_tdx_name_map_for_codes(self, codes) -> dict[str, str]:
        target_codes = set(self._normalize_code_name_targets(codes))
        if not target_codes:
            return {}

        cached_map = getattr(self, "_local_tdx_name_map_cache", None)
        if isinstance(cached_map, dict) and cached_map:
            return {
                code: str(cached_map.get(code, "") or "").strip()
                for code in target_codes
                if not self._is_placeholder_name(code, cached_map.get(code, ""))
            }

        vipdoc = getattr(self, "tdx_vipdoc", "") or ""
        if not vipdoc:
            return {}

        tdx_root = os.path.dirname(vipdoc)
        hq_cache_dir = os.path.join(tdx_root, "T0002", "hq_cache")
        merged_map: dict[str, str] = {}
        for filename in self._TNF_NAME_FILES:
            missing = target_codes.difference(merged_map.keys())
            if not missing:
                break
            merged_map.update(
                self._parse_tnf_name_file_for_codes(
                    os.path.join(hq_cache_dir, filename),
                    missing,
                )
            )
        return merged_map

    def _merge_local_tdx_name_map(self, base_map: dict | None, *, persist: bool = False) -> dict[str, str]:
        from core.data_store import DataStore

        merged_map = {
            str(raw_code or "").strip(): str(raw_name or "").strip()
            for raw_code, raw_name in dict(base_map or {}).items()
            if str(raw_code or "").strip()
        }
        local_name_map = self._load_local_tdx_name_map()
        if not local_name_map:
            return merged_map

        refreshed = 0
        for code, name in local_name_map.items():
            if self._is_placeholder_name(code, name):
                continue
            if merged_map.get(code) != name:
                merged_map[code] = name
                refreshed += 1

        if refreshed and persist:
            try:
                DataStore().save_json("vcp_code_names", merged_map)
                _log.info(f"[名称映射] 已从本地证券主表补齐 {refreshed} 只标的名称")
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                _log.debug(f"[名称映射] 持久化本地名称缓存失败: {exc}")

        return merged_map

    def _get_code_name_map_for_targets(self, target_codes: list[str]) -> dict[str, str]:
        from core.data_store import DataStore

        if not target_codes:
            return {}

        try:
            cached = DataStore().load_json("vcp_code_names") or {}
        except (OSError, RuntimeError, TypeError, ValueError):
            cached = {}
        cached_map = {
            str(raw_code or "").strip(): str(raw_name or "").strip()
            for raw_code, raw_name in dict(cached or {}).items()
            if str(raw_code or "").strip()
        }
        base_map: dict[str, str] = {}
        for code in target_codes:
            name = str(cached_map.get(code, "") or "").strip()
            if self._is_placeholder_name(code, name):
                name = code
            base_map[code] = name

        unresolved_codes = [code for code, name in base_map.items() if self._is_placeholder_name(code, name)]
        local_name_map = self._load_local_tdx_name_map_for_codes(unresolved_codes)
        for code, name in local_name_map.items():
            if not self._is_placeholder_name(code, name):
                base_map[code] = name

        for code, name in self._MANUAL_NAME_ALIASES.items():
            if code in base_map:
                base_map[code] = name

        return base_map

    def load_cached_code_name_map(self) -> dict[str, str]:
        from core.data_store import DataStore

        try:
            cached = DataStore().load_json("vcp_code_names") or {}
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            _log.debug(f"[名称映射] 读取缓存名称失败: {exc}")
            cached = {}

        cached_map = {
            str(raw_code or "").strip(): str(raw_name or "").strip()
            for raw_code, raw_name in dict(cached or {}).items()
            if str(raw_code or "").strip()
        }
        if not cached_map:
            return {}

        cached_map.update(self._MANUAL_NAME_ALIASES)
        self.code2name = cached_map
        _log.info(f"[名称映射] 已从缓存载入 {len(cached_map)} 条标的名称")
        return dict(cached_map)

    def get_all_codes(self):
        from core.data_store import DataStore

        if self._offline or not self.server_pool:
            cached = DataStore().load_json("vcp_code_names")
            if cached:
                cached = self._merge_local_tdx_name_map(cached, persist=True)
                _log.info(f"[离线模式] 从名称缓存读取 {len(cached)} 只标的（含股票名称）")
                return cached
            if self.tdx_vipdoc:
                return self._get_codes_from_vipdoc()
            return {}

        api = self._get_thread_api()
        stocks = {}
        for market in [0, 1]:
            count = api.get_security_count(market)
            if not count:
                continue
            for i in range(0, count, 1000):
                batch = api.get_security_list(market, i)
                if batch:
                    for s in batch:
                        code, name = s["code"], s["name"]
                        if "ST" in name:
                            continue
                        if market == 1 and code.startswith(("60", "68")):
                            stocks[code] = name
                        elif market == 0 and code.startswith(("00", "30")):
                            stocks[code] = name
        if stocks:
            try:
                DataStore().save_json("vcp_code_names", stocks)
                _log.info(f"[数据中台] 已保存 {len(stocks)} 只标的名称缓存至 SQLite")
            except (OSError, RuntimeError, TypeError, ValueError) as e:
                _log.error(f"[数据中台] 名称缓存保存失败: {e}")
        return stocks

    def _get_codes_from_vipdoc(self):
        stocks = {}
        from core.data_store import DataStore

        name_map = self._merge_local_tdx_name_map(
            DataStore().load_json("vcp_code_names") or {},
            persist=True,
        )
        tdx_vipdoc = getattr(self, "tdx_vipdoc", "") or ""
        if not tdx_vipdoc:
            return name_map

        # --- 曾用名/新名 人工热修复映射册 ---
        # 防止因 pytdx 证券列表缓存不及时或本地 JSON 始终未刷新导致的名称滞后
        MANUAL_NAME_ALIASES = {"603196": "璞源材料"}

        for sub, prefix in [("sh/lday", "sh"), ("sz/lday", "sz")]:
            lday_dir = os.path.join(tdx_vipdoc, sub.replace("/", os.sep))
            if not os.path.isdir(lday_dir):
                continue
            for fname in os.listdir(lday_dir):
                if not fname.endswith(".day"):
                    continue
                code = fname[2:-4]
                # 优先使用缓存名称，无缓存则用代码占位
                display_name = name_map.get(code, code)

                # 若命中热修复库，则强行覆写最新名称
                if code in MANUAL_NAME_ALIASES:
                    display_name = MANUAL_NAME_ALIASES[code]

                if prefix == "sh" and code.startswith(("60", "68")):
                    stocks[code] = display_name
                elif prefix == "sz" and code.startswith(("00", "30")):
                    stocks[code] = display_name
        has_names = sum(1 for c, n in stocks.items() if c != n)
        _log.info(f"[离线模式] 已从 vipdoc 扫描 {len(stocks)} 只标的（其中 {has_names} 只有名称）")
        return stocks

    def ensure_code_name_map(self, codes=None, *, refresh_missing=False):
        from core.data_store import DataStore

        target_codes = self._normalize_code_name_targets(codes)
        if target_codes:
            base_map = self._get_code_name_map_for_targets(target_codes)
        else:
            base_map = dict(self._get_codes_from_vipdoc() or {})
        current_map = getattr(self, "code2name", {}) or {}
        for raw_code, raw_name in dict(current_map).items():
            code = str(raw_code or "").strip()
            if not code:
                continue
            name = str(raw_name or "").strip()
            if not self._is_placeholder_name(code, name):
                base_map[code] = name

        if not target_codes:
            target_codes = list(base_map.keys())

        missing_codes = [
            code for code in dict.fromkeys(target_codes) if self._is_placeholder_name(code, base_map.get(code, ""))
        ]

        if refresh_missing and missing_codes and not self._offline:
            try:
                quotes = self.fetch_realtime_quotes_batch(missing_codes) or {}
            except (
                AttributeError,
                ConnectionError,
                KeyError,
                OSError,
                RuntimeError,
                TimeoutError,
                TypeError,
                ValueError,
            ) as exc:
                _log.debug(f"[名称映射] 在线补名称失败: {exc}")
                quotes = {}

            refreshed = {}
            for raw_code, payload in dict(quotes).items():
                code = str(raw_code or "").strip()
                name = str((payload or {}).get("name") or "").strip()
                if len(code) == 6 and code.isdigit() and not self._is_placeholder_name(code, name):
                    refreshed[code] = name

            if refreshed:
                base_map.update(refreshed)
                try:
                    store = DataStore()
                    cached_map = store.load_json("vcp_code_names", {}) or {}
                    merged_map = {
                        str(raw_code).strip(): str(raw_name or "").strip()
                        for raw_code, raw_name in dict(cached_map).items()
                        if str(raw_code or "").strip()
                    }
                    merged_map.update(refreshed)
                    store.save_json("vcp_code_names", merged_map)
                    _log.info(f"[名称映射] 已在线补齐 {len(refreshed)} 只标的名称")
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    _log.debug(f"[名称映射] 持久化名称缓存失败: {exc}")

        self.code2name = base_map
        return dict(base_map)

    def _worker_fetch(self, code, force_refresh, existing_df):
        if self._offline:
            try:
                if self.tdx_vipdoc:
                    local_df = self._fetch_from_local_tdx(code)
                    if local_df is not None and len(local_df) >= 250:
                        # 修复: 兼容 Polars 和 Pandas 两种 rename API
                        if "vol" in (local_df.columns if hasattr(local_df, "columns") else []):
                            if hasattr(local_df, "to_pandas"):
                                local_df = local_df.rename({"vol": "volume"})
                            else:
                                local_df.rename(columns={"vol": "volume"}, inplace=True)
                        return code, local_df, "OK"
                    elif local_df is not None:
                        return code, None, "次新股/上市不足250天"
                return code, None, "offline data missing"
            except (OSError, RuntimeError, TypeError, ValueError) as e:
                return code, None, f"本地读取异常: {e}"

        # Non-cryptographic request jitter.
        time.sleep(random.uniform(0.05, 0.15))  # nosec B311
        api = self._get_thread_api()
        try:
            if existing_df is not None and not force_refresh:
                import pandas as pd

                if not isinstance(existing_df, pd.DataFrame):
                    if hasattr(existing_df, "to_pandas"):
                        existing_df = existing_df.to_pandas()
                        if "datetime" in existing_df.columns:
                            existing_df = existing_df.set_index("datetime")

                new = self._fetch_standard_data(api, code, count=INCREMENTAL_BARS)
                if new is not None:
                    import polars as pl

                    if isinstance(new, pl.DataFrame):
                        new = new.to_pandas()
                        if "datetime" in new.columns:
                            new = new.set_index("datetime")

                    last_existing = existing_df.index.max()
                    first_new = new.index.min()
                    gap_days = (first_new - last_existing).days
                    if gap_days > 10:
                        df = self._fetch_standard_data(api, code, count=MAX_HISTORY_BARS)
                        if df is not None:
                            if len(df) >= 250:
                                return code, df, "OK"
                            return code, None, "次新股/上市不足250天"
                        return code, None, "全量下载超时"
                    combined = pd.concat([existing_df, new])
                    return code, combined[~combined.index.duplicated(keep="last")].iloc[-MAX_HISTORY_BARS:], "OK"
                return code, None, "增量下载超时"
            else:
                df = self._fetch_standard_data(api, code, count=MAX_HISTORY_BARS)
                if df is not None:
                    if len(df) >= 250:
                        return code, df, "OK"
                    else:
                        return code, None, "次新股/上市不足250天"
                return code, None, "全量下载超时"
        except ValueError as ve:
            return code, None, str(ve)
        except (ConnectionError, KeyError, OSError, RuntimeError, TimeoutError, TypeError) as e:
            _log.error(f"[数据中台] {code} 标的数据抓取发生异常: {e}")
            return code, None, "底层结构异常/长期停牌"

    def load_cache_from_disk(self):
        trade_date = load_cache_from_disk(self, logger=_log)
        snapshot_trade_date = _normalize_trade_date(trade_date)
        self._market_data_snapshot_trade_date = snapshot_trade_date if self.cache_data else ""
        return trade_date

    def sync_market_data(self, codes, force_refresh=False, progress_callback=None, *, max_workers=None):
        requested_codes = tuple(dict.fromkeys(codes)) if codes is not None else ()
        if not requested_codes:
            return True
        today_date = MarketCalendar.today("CN")
        today = today_date.strftime(DATE_FMT)
        latest_trade_date = MarketCalendar.get_latest_trade_date("CN", ref_date=today_date).strftime(DATE_FMT)
        if not self.cache_data:
            self.load_cache_from_disk()

        snapshot_trade_date = _normalize_trade_date(getattr(self, "_market_data_snapshot_trade_date", ""))
        snapshot_is_fresh = snapshot_trade_date == latest_trade_date and _requested_cache_has_coverage(
            self.cache_data,
            requested_codes,
        )
        last_date = (
            latest_trade_date
            if snapshot_is_fresh
            else _requested_cached_trade_date(self.cache_data, requested_codes)
        )

        if last_date == latest_trade_date and not force_refresh:
            return True
        if not force_refresh and self._is_before_930_today() and self._is_trading_day():
            previous_trade_date = MarketCalendar.get_latest_trade_date(
                "CN",
                ref_date=today_date - dt.timedelta(days=1),
            ).strftime(DATE_FMT)
            if last_date == previous_trade_date:
                _log.info(f"[缓存] 09:30 前继续沿用上一交易日快照（{last_date}）")
                return True

        self._market_data_snapshot_trade_date = ""
        total = len(requested_codes)
        # 为什么离线只用 20 线程？50 线程同时持有 DataFrame 内存峰值太高，容易触发 Windows OOM 闪退
        workers = _resolve_market_sync_workers(offline=self._offline, requested_max_workers=max_workers)
        _log.info(
            f"\n[数据中台] 阶段1: 同步日线 -> 目标 {total} 只 | 线程数 {workers} | {'离线本地' if self._offline else ('强制覆盖' if force_refresh else '增量/缓存')}"
        )
        if self.tdx_vipdoc:
            _log.info(f"         数据源: 优先通达信本地 -> {self.tdx_vipdoc}")
        _log.info("         请稍候...")
        completed, audit_log = 0, {}
        start_time = time.time()
        last_log_at = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_code = {
                executor.submit(
                    self._worker_fetch, code, force_refresh, self.cache_data.get(code) if not force_refresh else None
                ): code
                for code in requested_codes
            }
            for future in concurrent.futures.as_completed(future_to_code):
                completed += 1
                if completed % 50 == 0:
                    time.sleep(0.001)
                pct = 100 * (completed / float(total))
                current_step = int(pct / 10) * 10
                should_log = (completed == total) or (current_step > last_log_at)

                if should_log:
                    last_log_at = current_step
                    percent = ("{0:.1f}").format(pct)
                    elapsed = time.time() - start_time
                    if elapsed > 2 and completed > 0:
                        rate = completed / elapsed
                        remaining_sec = (total - completed) / rate if rate > 0 else 0
                        eta_msg = (
                            f" ETA {int(remaining_sec / 60)} min"
                            if remaining_sec >= 60
                            else f" ETA {int(remaining_sec)} s"
                        )
                    else:
                        eta_msg = ""
                    _log.info(f" -> 同步进度: {percent}% [{completed}/{total}]{eta_msg}")
                    if progress_callback:
                        try:
                            progress_callback(completed, total, eta_msg)
                        except (RuntimeError, TypeError, ValueError) as _e:
                            _log.debug(f"[数据中台] 进度回调异常: {_e}")
                res_code, res_df, status_msg = future.result()
                if res_df is not None:
                    with self.cache_lock:
                        self.cache_data[res_code] = res_df
                else:
                    audit_log.setdefault(status_msg, []).append(res_code)
        gc.collect()
        failed_count = sum(len(v) for v in audit_log.values())
        _log.error(
            f"\n[缓存] 阶段1完成：已同步 {len(self.cache_data)} 只标的 | 失败 {failed_count} | 耗时 {time.time() - start_time:.1f}s"
        )
        _log.info(f"{'=' * 50}\n [内部审计报告] 数据对账单\n{'=' * 50}")
        _log.info(f" total: {total} | cached: {len(self.cache_data)} | failed: {failed_count}")
        if failed_count > 0:
            for reason, err_codes in sorted(audit_log.items(), key=lambda item: len(item[1]), reverse=True):
                _log.info(f"  - {reason}: {len(err_codes)} 只 (例: {', '.join(err_codes[:5])}...)")
        _log.info(f"{'=' * 50}")
        # 阶段2: 跳过批量指标预算（按需计算更高效）
        # 原因: 5000 只全量预算耗时 5-10 秒且霸占 GIL 导致 UI 卡顿，
        #       而 evaluate_conditions/precompute_ready_pool 内部已有
        #       'if entangle not in df.columns' 的按需计算兜底逻辑，
        #       实际只有 RPS≥80 的几百只会被真正评估。
        _log.info("[数据中台] 阶段2: 跳过批量指标预算(改为按需计算)，直接进入降精度...")
        self._downcast_memory()

        _log.info("[数据中台] 阶段3: 写入本地缓存(Parquet)...")
        # 主路径写 Parquet（体积更小、加载更快）
        parquet_saved = False
        self._last_market_data_parquet_saved_date = ""
        try:
            from vcp.polars_engine import save_cache_parquet

            parquet_saved = bool(save_cache_parquet(self.cache_data, today))
            if parquet_saved:
                self._last_market_data_parquet_saved_date = today
                self._market_data_snapshot_trade_date = today
                remove_cache_file(self.legacy_cache_file)
                remove_cache_file(self.legacy_cache_file + ".corrupted")
                remove_cache_file(self.legacy_fallback_cache_file)
        except ImportError:
            _log.error("[数据中台] polars 未安装，无法写入 Parquet 缓存")
        except (OSError, RuntimeError, TypeError, ValueError) as e:
            _log.error(f"[数据中台] Parquet 写入失败: {e}", exc_info=True)

        if not parquet_saved:
            _log.error("[数据中台] Parquet 失败，已停止写入旧版 pkl fallback，请检查 pyarrow/polars 环境")
        _log.info(f"[数据中台] 阶段3 完成 -> 缓存已保存 (日期: {today})\n")
        return True

    def get_data(self, code):
        with self.cache_lock:
            df = self.cache_data.get(code)
            if df is not None:
                normalized_df = ensure_pandas_dataframe(df)
                if isinstance(normalized_df, pd.DataFrame) and normalized_df is not df:
                    self.cache_data[code] = normalized_df
                    df = normalized_df
                try:
                    self._last_market_data_source_status = {
                        "ok": True,
                        "active_layer": "memory_cache",
                        "data_status": "ok",
                        "symbol_count": len(self.cache_data),
                        "row_count": len(df) if hasattr(df, "__len__") else 0,
                        "fallback_reason": "",
                    }
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pass
                return df

        warehouse = None
        warehouse_getter = getattr(self, "_get_market_data_warehouse", None)
        if callable(warehouse_getter):
            warehouse = warehouse_getter()
        else:
            warehouse = getattr(self, "market_data_warehouse", None)
        if warehouse is not None:
            result = warehouse.read_symbol(code)
            if result.status.ok and result.data is not None:
                warehouse_df = ensure_pandas_dataframe(result.data)
                if isinstance(warehouse_df, pd.DataFrame) and len(warehouse_df) > 0:
                    with self.cache_lock:
                        cached_df = self.cache_data.get(code)
                        if cached_df is None:
                            self.cache_data[code] = warehouse_df
                            cached_df = warehouse_df
                    try:
                        self._last_market_data_source_status = result.status.to_dict()
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        pass
                    return cached_df
            try:
                self._last_market_data_source_status = result.status.to_dict()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass

        # K 线窗口等历史图表不能依赖“缓存先被别处预热”这一前置条件；
        # 当内存缓存为空时，直接回退读取本地通达信日线并写回缓存。
        if getattr(self, "tdx_vipdoc", None):
            local_df = self._fetch_from_local_tdx(code)
            if local_df is not None and len(local_df) > 0:
                with self.cache_lock:
                    cached_df = self.cache_data.get(code)
                    if cached_df is None:
                        self.cache_data[code] = local_df
                        try:
                            self._last_market_data_source_status = {
                                "ok": True,
                                "active_layer": "vipdoc_fallback",
                                "data_status": "ok",
                                "symbol_count": len(self.cache_data),
                                "row_count": len(local_df),
                                "fallback_reason": "warehouse_unavailable_or_symbol_missing",
                            }
                        except (AttributeError, RuntimeError, TypeError, ValueError):
                            pass
                        return local_df
                    try:
                        self._last_market_data_source_status = {
                            "ok": True,
                            "active_layer": "memory_cache_after_vipdoc",
                            "data_status": "ok",
                            "symbol_count": len(self.cache_data),
                            "row_count": len(cached_df) if hasattr(cached_df, "__len__") else 0,
                            "fallback_reason": "",
                        }
                    except (AttributeError, RuntimeError, TypeError, ValueError):
                        pass
                    return cached_df

        try:
            self._last_market_data_source_status = {
                "ok": False,
                "active_layer": "unavailable",
                "data_status": "history_unavailable",
                "fallback_reason": "warehouse_and_vipdoc_unavailable",
            }
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        return None

    def get_data_batch(self, codes):
        requested_codes = tuple(
            dict.fromkeys(str(code or "").strip() for code in (codes or []) if str(code or "").strip())
        )
        if not requested_codes:
            return {}

        result: dict[str, pd.DataFrame] = {}
        missing_codes: list[str] = []
        with self.cache_lock:
            for code in requested_codes:
                frame = self.cache_data.get(code)
                if frame is None:
                    missing_codes.append(code)
                    continue
                normalized = ensure_pandas_dataframe(frame)
                if not isinstance(normalized, pd.DataFrame) or normalized.empty:
                    missing_codes.append(code)
                    continue
                if normalized is not frame:
                    self.cache_data[code] = normalized
                result[code] = normalized

        warehouse_result = None
        if missing_codes:
            warehouse_getter = getattr(self, "_get_market_data_warehouse", None)
            warehouse = warehouse_getter() if callable(warehouse_getter) else getattr(self, "market_data_warehouse", None)
            read_symbols = getattr(warehouse, "read_symbols", None)
            if callable(read_symbols):
                try:
                    warehouse_result = read_symbols(missing_codes)
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                    warehouse_result = None

        if warehouse_result is not None:
            try:
                self._last_market_data_source_status = warehouse_result.status.to_dict()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
            if warehouse_result.status.ok and isinstance(warehouse_result.data, dict):
                for code, frame in warehouse_result.data.items():
                    normalized = ensure_pandas_dataframe(frame)
                    if not isinstance(normalized, pd.DataFrame) or normalized.empty:
                        continue
                    with self.cache_lock:
                        cached = self.cache_data.setdefault(code, normalized)
                    result[code] = ensure_pandas_dataframe(cached)

        for code in requested_codes:
            if code in result:
                continue
            local_df = self._fetch_from_local_tdx(code) if getattr(self, "tdx_vipdoc", None) else None
            if local_df is None or len(local_df) <= 0:
                continue
            with self.cache_lock:
                cached = self.cache_data.setdefault(code, local_df)
            result[code] = ensure_pandas_dataframe(cached)

        return result

    def get_data_fresh_for_chart(self, code, force_sync=False):
        """按需委托给本地历史服务做图表补全。"""
        return self._get_local_history_provider().get_data_fresh_for_chart(
            code,
            force_sync=force_sync,
        )
