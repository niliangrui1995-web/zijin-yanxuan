# ui/workers.py - 后台工作线程
# 从 main_window_qt.py 拆分出来的 ScanWorker 和 RtScanWorker
import gc

import pandas as pd
from PyQt6.QtCore import QThread, pyqtSignal

from app.services.scan_runtime_service import batch_check_market_cap, calculate_scan_indicators
from core.logger import get_logger
from core.sector_rps_helper import enrich_hot_sector_rows, load_sector_rps_snapshot

log = get_logger(__name__)


class ScanWorker(QThread):
    progress = pyqtSignal(int, str)
    result_ready = pyqtSignal(list)
    finished_scan = pyqtSignal(bool, str)

    def __init__(self, data_provider, engine, sd, ed, params):
        super().__init__()
        self.data_provider = data_provider
        self.engine = engine
        self.sd = sd
        self.ed = ed
        self.params = params
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def _raise_if_cancelled(self):
        if self._is_cancelled:
            raise InterruptedError("用户取消")

    def _target_codes_for_day(self, d_rps):
        return [
            k
            for k, v in d_rps["rps250"].items()
            if pd.notna(v)
            and (v >= self.params.rps_threshold or d_rps["rps120"].get(k, 0) >= self.params.rps_threshold)
        ]

    def _refresh_candidate_names(self, matrix):
        candidate_codes = set()
        for d_rps in matrix.values():
            candidate_codes.update(self._target_codes_for_day(d_rps))
        if candidate_codes:
            self.data_provider.code2name = self.data_provider.ensure_code_name_map(
                candidate_codes,
                refresh_missing=True,
            )

    def _prepare_scan_dataframe(self, code, df):
        if "entangle" in df.columns:
            return df
        prepared = calculate_scan_indicators(df.copy())
        with self.data_provider.cache_lock:
            self.data_provider.cache_data[code] = prepared
        return prepared

    def _dedupe_scan_results(self, all_results):
        if not all_results:
            return all_results
        df_all = pd.DataFrame(all_results).sort_values("触发日期")
        df_all = df_all.drop_duplicates(subset=["代码"], keep="last")
        if "评分" in df_all.columns:
            df_all["评分_tmp"] = pd.to_numeric(df_all["评分"], errors="coerce")
            df_all = df_all.sort_values(by=["触发日期", "评分_tmp"], ascending=[False, False])
            df_all = df_all.drop(columns=["评分_tmp"])
        return df_all.to_dict("records")

    def _enrich_hot_sectors(self, all_results):
        if not all_results:
            return
        self.progress.emit(99, "查询热点板块...")
        try:
            target_date = all_results[-1].get("触发日期", "")
            sector_manager, sector_rps, _, source = load_sector_rps_snapshot(
                self.data_provider,
                self.data_provider.get_all_valid_data(),
                target_date=target_date,
                logger=log,
            )
            if sector_manager and sector_rps:
                log.info(f"[区间扫描] 热点板块补全就绪 ({source})")
                enrich_hot_sector_rows(all_results, sector_manager, sector_rps, logger=log)
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
            log.error(f"[板块查询] 异常: {e}")

    def _enrich_market_caps(self, all_results):
        if not all_results:
            return

        import time as _time

        self.progress.emit(99, "计算市值...")
        started_at = _time.time()
        df_res = pd.DataFrame(all_results)
        unique_codes = df_res["代码"].unique().tolist()
        scan_close = {}
        for code in unique_codes:
            code_data = self.data_provider.get_data(code)
            if code_data is not None and len(code_data) > 0:
                scan_close[code] = float(code_data.iloc[-1]["close"])

        cap_results = batch_check_market_cap(unique_codes, close_prices=scan_close)

        for result in all_results:
            code = result["代码"]
            cap = cap_results.get(code)
            if cap and cap > 0:
                result["市值"] = f"{cap / 1e8:.0f}亿"
                result["_cap_raw"] = cap
            else:
                result["市值"] = "--"
                result["_cap_raw"] = 0

        log.info(f"[区间扫描] 市值查询完成 ({_time.time() - started_at:.1f}s)")

    def _scan_candidate_for_day(self, code, d_str, d_rps, reason_stats):
        stock_name = self.data_provider.code2name.get(code, "")
        if "ST" in stock_name.upper():
            return None

        df = self.data_provider.get_data(code)
        if df is None:
            return None

        try:
            df_safe = self._prepare_scan_dataframe(code, df)
            ok, reason, metrics = self.engine.evaluate_conditions(
                df_safe,
                pd.to_datetime(d_str),
                d_rps["rps120"].get(code, 0),
                d_rps["rps250"].get(code, 0),
                None,
                self.params,
                skip_red_check=True,
            )
            if ok:
                metrics.update(
                    {
                        "代码": code,
                        "名称": self.data_provider.code2name.get(code, ""),
                        "触发日期": d_str,
                        "热点板块": "-",
                    }
                )
                return metrics
            reason_stats[reason] = reason_stats.get(reason, 0) + 1
        except (
            AttributeError,
            IndexError,
            KeyError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as e:
            log.error(f"[区间扫描] {code} 评估异常: {e}", exc_info=True)
        return None

    def _ensure_scan_source_data(self):
        import time as _time

        if not getattr(self.data_provider, "_local_gbbq", None):
            self.progress.emit(5, "正在加载除权除息数据 (gbbq)...")
            started_at = _time.time()
            self.data_provider._load_local_gbbq(force=False)
            log.info(f"[区间扫描] gbbq加载完成 ({_time.time() - started_at:.1f}s)")

        self.progress.emit(1, "正在查询数据...")
        if not self.data_provider.cache_data:
            self.progress.emit(0, "首次扫描:读取本地代码表...")
            codes_dict = self.data_provider.ensure_code_name_map()

            def _sync_cb(done, total, eta):
                self._raise_if_cancelled()
                if total > 0 and done % 50 == 0:
                    pct = int((done / total) * 50)
                    self.progress.emit(pct, f"缓存本地日线: {done}/{total} {eta}")

            self.data_provider.sync_market_data(codes_dict, force_refresh=False, progress_callback=_sync_cb)
            self.data_provider.code2name = codes_dict
        else:
            self.data_provider.code2name = self.data_provider.ensure_code_name_map()

    def _build_scan_matrix(self):
        import time as _time

        self.progress.emit(50, "计算 RPS 相对强度矩阵...")
        started_at = _time.time()
        market_cache = self.data_provider.get_all_valid_data()
        matrix = self.engine.build_rps_matrix(market_cache, self.sd, self.ed)
        if matrix:
            self._refresh_candidate_names(matrix)
        return matrix, started_at

    def _scan_matrix_candidates(self, matrix):
        import time as _time

        total_days = len(matrix)
        all_results = []
        reason_stats = {}

        for i, (d_str, d_rps) in enumerate(matrix.items()):
            self._raise_if_cancelled()

            pct = int(100 * (i + 1) / total_days)
            self.progress.emit(pct, f"扫描 {d_str} ({i + 1}/{total_days})")

            targets = self._target_codes_for_day(d_rps)
            for idx_code, code in enumerate(targets):
                # 防止后台扫描长时间独占 CPU，给 UI 线程留出响应窗口。
                if idx_code % 20 == 0:
                    _time.sleep(0.001)

                result = self._scan_candidate_for_day(code, d_str, d_rps, reason_stats)
                if result is not None:
                    all_results.append(result)

        return all_results

    def run(self):
        import time as _time

        _total_start = _time.time()

        # 起始阶段发送正常状态文本，避免 UI 依赖特殊占位字符串
        self.progress.emit(1, "准备扫描...")

        try:
            self._ensure_scan_source_data()
            self._raise_if_cancelled()
            matrix, rps_started_at = self._build_scan_matrix()

            if not matrix:
                self.finished_scan.emit(False, "区间无效或无通达信本地数据")
                return

            all_results = self._scan_matrix_candidates(matrix)
            all_results = self._dedupe_scan_results(all_results)

            # 释放 RPS 矩阵内存（可能占用几十MB，后续步骤不再需要）
            del matrix
            gc.collect()

            log.info(f"[区间扫描] RPS筛选完成 ({_time.time() - rps_started_at:.1f}s)，命中 {len(all_results)} 只")

            # ---- 二级过滤:与盘中监控对齐的机构+市值筛选 ----
            self._enrich_market_caps(all_results)

            # 因用户要求区间扫描需全面、不漏票，此处取消剔除市值<40亿的盘中监控硬过滤机制
            # 让区间扫描忠于技术形态，展示所有满足 VCP 的股票。
            # 市值计算仍保留，仅为了在界面展示数值（但不剔除）。

            # 由于用户要求加快扫描速度，机构过滤对于初始区间扫描过于耗时（需排队查网页），故此处剔除机构筛选逻辑。
            # 如果需要看机构，可以在盘中监控或关注池中再进行查看。

            # 按评分倒序
            if all_results:
                all_results.sort(key=lambda x: x.get("评分", 0), reverse=True)

            log.info(f"[区间扫描] ✅ 完成 ({_time.time() - _total_start:.1f}s)，产生 {len(all_results)} 条结果")

            # 清理内部临时字段
            for r in all_results:
                r.pop("_cap_raw", None)

            self._enrich_hot_sectors(all_results)

            # 扫描完成，主动回收中间对象（Polars 转换等产生的临时 DataFrame）
            gc.collect()

            self.result_ready.emit(all_results)
            self.finished_scan.emit(True, f"扫描完成,捕获 {len(all_results)} 条信号")

        except InterruptedError:
            self.finished_scan.emit(False, "任务已取消")
        except (AttributeError, ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
            self.finished_scan.emit(False, f"扫描异常: {str(e)}")
