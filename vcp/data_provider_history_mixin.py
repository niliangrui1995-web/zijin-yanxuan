import concurrent.futures
import os
import random
import time

import pandas as pd

from core.json_cache import remove_cache_file
from core.logger import get_logger
from core.market_calendar import MarketCalendar
from vcp.constants import DATE_FMT, INCREMENTAL_BARS, MARKET_SYNC_WORKERS, MAX_HISTORY_BARS
from vcp.data_provider_cache import load_cache_from_disk
from vcp.utils import ensure_pandas_dataframe

_log = get_logger(__name__)


class TdxDataProviderHistoryMixin:
    _TNF_RECORD_SIZE = 360
    _TNF_CODE_OFFSET = 50
    _TNF_NAME_OFFSET = 81
    _TNF_NAME_FIELD_LEN = 45
    _TNF_NAME_FILES = ("shs.tnf", "szs.tnf")

    @staticmethod
    def _is_placeholder_name(code, name) -> bool:
        code_text = str(code or "").strip()
        name_text = str(name or "").strip()
        return not name_text or name_text == code_text

    @classmethod
    def _decode_tnf_name(cls, raw_name: bytes) -> str:
        payload = bytes(raw_name or b"").split(b"\x00", 1)[0].rstrip(b" \x00")
        if not payload:
            return ""
        for encoding in ("gbk", "gb18030", "utf-8"):
            try:
                return payload.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
        return payload.decode("latin1", errors="ignore").strip()

    @classmethod
    def _parse_tnf_name_file(cls, tnf_path: str) -> dict[str, str]:
        if not tnf_path or not os.path.exists(tnf_path):
            return {}

        try:
            with open(tnf_path, "rb") as handle:
                payload = handle.read()
        except OSError as exc:
            _log.debug(f"[名称映射] 读取本地证券主表失败: {tnf_path} {exc}")
            return {}

        code_names: dict[str, str] = {}
        stride = cls._TNF_RECORD_SIZE
        code_start = cls._TNF_CODE_OFFSET
        code_end = code_start + 6
        name_start = cls._TNF_NAME_OFFSET
        name_end = name_start + cls._TNF_NAME_FIELD_LEN

        for offset in range(0, len(payload) - stride + 1, stride):
            record = payload[offset: offset + stride]
            code_bytes = record[code_start:code_end]
            if len(code_bytes) != 6 or not code_bytes.isdigit():
                continue
            code = code_bytes.decode("ascii", errors="ignore").strip()
            if len(code) != 6:
                continue
            name = cls._decode_tnf_name(record[name_start:name_end])
            if cls._is_placeholder_name(code, name):
                continue
            code_names[code] = name

        return code_names

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
                        code, name = s['code'], s['name']
                        if 'ST' in name: continue
                        if market == 1 and code.startswith(('60', '68')): stocks[code] = name
                        elif market == 0 and code.startswith(('00', '30')): stocks[code] = name
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

        # --- 曾用名/新名 人工热修复映射册 ---
        # 防止因 pytdx 证券列表缓存不及时或本地 JSON 始终未刷新导致的名称滞后
        MANUAL_NAME_ALIASES = {
            '603196': '璞源材料'
        }

        for sub, prefix in [('sh/lday', 'sh'), ('sz/lday', 'sz')]:
            lday_dir = os.path.join(self.tdx_vipdoc, sub.replace('/', os.sep))
            if not os.path.isdir(lday_dir):
                continue
            for fname in os.listdir(lday_dir):
                if not fname.endswith('.day'):
                    continue
                code = fname[2:-4]
                # 优先使用缓存名称，无缓存则用代码占位
                display_name = name_map.get(code, code)

                # 若命中热修复库，则强行覆写最新名称
                if code in MANUAL_NAME_ALIASES:
                    display_name = MANUAL_NAME_ALIASES[code]

                if prefix == 'sh' and code.startswith(('60', '68')):
                    stocks[code] = display_name
                elif prefix == 'sz' and code.startswith(('00', '30')):
                    stocks[code] = display_name
        has_names = sum(1 for c, n in stocks.items() if c != n)
        _log.info(f"[离线模式] 已从 vipdoc 扫描 {len(stocks)} 只标的（其中 {has_names} 只有名称）")
        return stocks

    def ensure_code_name_map(self, codes=None, *, refresh_missing=False):
        from core.data_store import DataStore

        base_map = dict(self._get_codes_from_vipdoc() or {})
        current_map = getattr(self, "code2name", {}) or {}
        for raw_code, raw_name in dict(current_map).items():
            code = str(raw_code or "").strip()
            if not code:
                continue
            name = str(raw_name or "").strip()
            if not self._is_placeholder_name(code, name):
                base_map[code] = name

        target_codes = []
        if codes:
            for raw_code in codes:
                code = str(raw_code or "").strip()
                if len(code) == 6 and code.isdigit():
                    target_codes.append(code)
        else:
            target_codes = list(base_map.keys())

        missing_codes = [
            code for code in dict.fromkeys(target_codes)
            if self._is_placeholder_name(code, base_map.get(code, ""))
        ]

        if refresh_missing and missing_codes and not self._offline:
            try:
                quotes = self.fetch_realtime_quotes_batch(missing_codes) or {}
            except (AttributeError, ConnectionError, KeyError, OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
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
                        if 'vol' in (local_df.columns if hasattr(local_df, 'columns') else []):
                            if hasattr(local_df, 'to_pandas'):
                                local_df = local_df.rename({'vol': 'volume'})
                            else:
                                local_df.rename(columns={'vol': 'volume'}, inplace=True)
                        return code, local_df, "OK"
                    elif local_df is not None:
                        return code, None, "次新股/上市不足250天"
                return code, None, "offline data missing"
            except (OSError, RuntimeError, TypeError, ValueError) as e:
                return code, None, f"本地读取异常: {e}"

        time.sleep(random.uniform(0.05, 0.15))
        api = self._get_thread_api()
        try:
            if existing_df is not None and not force_refresh:
                import pandas as pd
                if not isinstance(existing_df, pd.DataFrame):
                    if hasattr(existing_df, 'to_pandas'):
                        existing_df = existing_df.to_pandas()
                        if 'datetime' in existing_df.columns:
                            existing_df = existing_df.set_index('datetime')

                new = self._fetch_standard_data(api, code, count=INCREMENTAL_BARS)
                if new is not None:
                    import polars as pl
                    if isinstance(new, pl.DataFrame):
                        new = new.to_pandas()
                        if 'datetime' in new.columns:
                            new = new.set_index('datetime')

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
                    return code, combined[~combined.index.duplicated(keep='last')].iloc[-MAX_HISTORY_BARS:], "OK"
                return code, None, "增量下载超时"
            else:
                df = self._fetch_standard_data(api, code, count=MAX_HISTORY_BARS)
                if df is not None:
                    if len(df) >= 250: return code, df, "OK"
                    else: return code, None, "次新股/上市不足250天"
                return code, None, "全量下载超时"
        except ValueError as ve:
            return code, None, str(ve)
        except (ConnectionError, KeyError, OSError, RuntimeError, TimeoutError, TypeError) as e:
            _log.error(f"[数据中台] {code} 标的数据抓取发生异常: {e}")
            return code, None, "底层结构异常/长期停牌"

    def load_cache_from_disk(self):
        return load_cache_from_disk(self, logger=_log)

    def sync_market_data(self, codes, force_refresh=False, progress_callback=None):
        today = MarketCalendar.today("CN").strftime(DATE_FMT)
        if not self.cache_data:
            last_date = self.load_cache_from_disk()
        else:
            last_date = today if self.cache_data else ""

        if last_date == today and not force_refresh: return True
        if not force_refresh and self._is_before_930_today() and self._is_trading_day() and last_date:
            _log.info(f"[缓存] 最近一次更新早于 09:30（{last_date}），继续沿用上一交易日快照")
            return True

        total = len(codes)
        # 为什么离线只用 20 线程？50 线程同时持有 DataFrame 内存峰值太高，容易触发 Windows OOM 闪退
        workers = 20 if self._offline else MARKET_SYNC_WORKERS
        _log.info(f"\n[数据中台] 阶段1: 同步日线 -> 目标 {total} 只 | 线程数 {workers} | {'离线本地' if self._offline else ('强制覆盖' if force_refresh else '增量/缓存')}")
        if self.tdx_vipdoc:
            _log.info(f"         数据源: 优先通达信本地 -> {self.tdx_vipdoc}")
        _log.info("         请稍候...")
        completed, audit_log = 0, {}
        start_time = time.time()
        last_log_at = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_code = {executor.submit(self._worker_fetch, code, force_refresh, self.cache_data.get(code) if not force_refresh else None): code for code in codes}
            for future in concurrent.futures.as_completed(future_to_code):
                completed += 1
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
                        eta_msg = f" ETA {int(remaining_sec / 60)} min" if remaining_sec >= 60 else f" ETA {int(remaining_sec)} s"
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
        failed_count = sum(len(v) for v in audit_log.values())
        _log.error(f"\n[缓存] 阶段1完成：已同步 {len(self.cache_data)} 只标的 | 失败 {failed_count} | 耗时 {time.time()-start_time:.1f}s")
        _log.info(f"{'='*50}\n [内部审计报告] 数据对账单\n{'='*50}")
        _log.info(f" total: {total} | cached: {len(self.cache_data)} | failed: {failed_count}")
        if failed_count > 0:
            for reason, err_codes in sorted(audit_log.items(), key=lambda item: len(item[1]), reverse=True):
                _log.info(f"  - {reason}: {len(err_codes)} 只 (例: {', '.join(err_codes[:5])}...)")
        _log.info(f"{'='*50}")
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
        try:
            from vcp.polars_engine import save_cache_parquet
            save_cache_parquet(self.cache_data, today)
            parquet_saved = True
            remove_cache_file(self.legacy_cache_file)
            remove_cache_file(self.legacy_cache_file + '.corrupted')
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
                return df

        # K 线窗口等历史图表不能依赖“缓存先被别处预热”这一前置条件；
        # 当内存缓存为空时，直接回退读取本地通达信日线并写回缓存。
        if getattr(self, "tdx_vipdoc", None):
            local_df = self._fetch_from_local_tdx(code)
            if local_df is not None and len(local_df) > 0:
                with self.cache_lock:
                    cached_df = self.cache_data.get(code)
                    if cached_df is None:
                        self.cache_data[code] = local_df
                        return local_df
                    return cached_df

        return None

    def get_data_fresh_for_chart(self, code, force_sync=False):
        """Return latest daily bars by combining local cache and online incremental data.

        force_sync=True 时跳过盘前/盘后的缓存短路判断，强制尝试联网补全。
        """
        from vcp.engine import VCPEngine

        existing_df = self.get_data(code)
        if not force_sync and self._is_before_930_today():
            return existing_df
        if (
            not force_sync
            and self._is_after_1500_today()
            and existing_df is not None
            and len(existing_df) > 0
        ):
            try:
                if pd.Timestamp(existing_df.index.max()).date() >= MarketCalendar.today("CN"):
                    return existing_df
            except (TypeError, ValueError) as _e:
                _log.debug(f"[K线 {code}] 缓存日期检查异常: {_e}")
        if not self.server_pool:
            return existing_df
        api = self._get_thread_api()
        try:
            if existing_df is not None and len(existing_df) >= 250:
                new = self._fetch_standard_data(api, code, count=INCREMENTAL_BARS)
                if new is not None and len(new) > 0:
                    import polars as pl
                    if isinstance(new, pl.DataFrame):
                        new = new.to_pandas()
                        if 'datetime' in new.columns:
                            new = new.set_index('datetime')

                    last_existing = existing_df.index.max()
                    first_new = new.index.min()
                    gap_days = (first_new - last_existing).days
                    if gap_days > 10:
                        full_df = self._fetch_standard_data(api, code, count=MAX_HISTORY_BARS)
                        if full_df is not None and len(full_df) >= 250:
                            full_df = VCPEngine.calculate_indicators(full_df)
                            with self.cache_lock:
                                self.cache_data[code] = full_df
                            return full_df
                    combined = pd.concat([existing_df, new])
                    merged = combined[~combined.index.duplicated(keep='last')].iloc[-MAX_HISTORY_BARS:]
                    merged = VCPEngine.calculate_indicators(merged)
                    with self.cache_lock:
                        self.cache_data[code] = merged
                    return merged
            else:
                full_df = self._fetch_standard_data(api, code, count=MAX_HISTORY_BARS)
                if full_df is not None and len(full_df) >= 250:
                    full_df = VCPEngine.calculate_indicators(full_df)
                    with self.cache_lock:
                        self.cache_data[code] = full_df
                    return full_df
        except (TimeoutError, OSError, ConnectionError) as e:
            _log.error(f"[K线 {code}] 联网补全失败(网络层)，继续使用缓存: {e}")
        except (ValueError, TypeError, KeyError, ArithmeticError) as e:
            _log.error(f"[K线 {code}] 联网补全失败(数据层)，继续使用缓存: {e}")
        return existing_df
