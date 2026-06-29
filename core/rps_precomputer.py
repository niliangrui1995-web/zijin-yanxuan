# -*- coding: utf-8 -*-
"""
core/rps_precomputer.py
F5 预计算核心组件，封装 RPS大矩阵 以及 Sector板块 RPS的纯后台计算流。
"""

import datetime
import gc
import os
import time

from core.json_cache import remove_cache_file, save_json_file
from core.logger import get_logger, system_log_backpressure
from core.runtime_paths import PROJECT_ROOT, RPS_CACHE_FILE, SECTOR_RPS_CACHE_FILE, ensure_cache_dir

log = get_logger(__name__)


def _should_emit_ui_status(msg: str) -> bool:
    text = str(msg or "").strip()
    if not text:
        return False
    return set(text) != {"="}


def _get_memory_usage_mb() -> float:
    """获取当前进程内存占用(MB)。psutil 是可选依赖，没装则返回 -1"""
    try:
        import psutil

        return psutil.Process().memory_info().rss / 1024 / 1024
    except ImportError:
        return -1.0


def _emit_status(set_status_callback, msg: str) -> None:
    if set_status_callback and _should_emit_ui_status(msg):
        set_status_callback(msg)


def _handle_stage1_progress(done: int, total: int, eta: str, set_status_callback, progress_state=None) -> None:
    if total <= 0:
        return
    should_emit = done == total or done % 1000 == 0
    if progress_state is not None:
        bucket = int((done / float(total)) * 10)
        last_bucket = int(progress_state.get("last_bucket", -1))
        if bucket > last_bucket and bucket > 0:
            progress_state["last_bucket"] = bucket
            should_emit = True
    if not should_emit:
        return
    suffix = f" {eta}" if eta else ""
    msg = f"[F5] 阶段1/3: 重读本地数据 {done}/{total}{suffix}"
    _emit_status(set_status_callback, msg)
    if done == total or done % 1000 == 0:
        log.info(msg)


def _save_stage1_checkpoint(cache_data, today_str: str) -> None:
    try:
        from vcp.polars_engine import save_cache_parquet

        if save_cache_parquet(cache_data, today_str):
            log.info("[F5] stage1 checkpoint saved; next F5 can resume")
        else:
            log.warning("[F5] stage1 checkpoint save returned false")
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
        log.warning(f"[F5] 断点存档失败(不影响后续): {e}")


def _provider_saved_stage1_cache(data_provider, today_str: str) -> bool:
    saved_date = str(getattr(data_provider, "_last_market_data_parquet_saved_date", "") or "").strip()
    return bool(today_str and saved_date == today_str)


class RPSPrecomputer:
    """封装原本在 MainWindow_DataCacheMixin 中的 _action_refresh 业务。"""

    @staticmethod
    def run_f5_pipeline(data_provider, engine, cancelled_checker, set_status_callback, done_callback):
        """
        运行 F5 预计算核心流程。这是个纯阻塞方法，应由 TaskManager 在后台线程调用。

        :param data_provider: TdxDataProvider 实例
        :param engine: VCPEngine 实例
        :param cancelled_checker: 一个无参函数 `lambda: bool` 返回是否用户中途取消
        :param set_status_callback: 回调，用于回传进度日记给界面
        :param done_callback: 回调，完成后把计算耗时和股票总数传回以更新 UI
        """
        ensure_cache_dir()
        total_start = time.time()

        def _log_and_status(msg):
            log.info(msg)
            _emit_status(set_status_callback, msg)

        def _log_memory(stage_name: str):
            """为什么要监控内存？F5 闪退直接原因是 Windows OOM kill，加监控能精确定位哪个阶段吃光内存"""
            mem_mb = _get_memory_usage_mb()
            if mem_mb > 0:
                log.info(f"[F5] 内存快照 [{stage_name}]: {mem_mb:.0f} MB")

        with system_log_backpressure("F5", allowed_info_loggers=(__name__,)):
            try:
                _log_and_status("\n" + "=" * 60)
                _log_and_status("[F5] 盘后一键预计算 -- 开始")
                _log_and_status("=" * 60)
                _log_memory("启动基线")

                # --- 阶段0: 除权除息 ---
                _log_and_status("[F5] 阶段0: 重新解析通达信 gbbq 除权除息数据...")
                try:
                    data_provider._load_local_gbbq(force=True)
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
                    log.error(f"[F5] gbbq 解析异常(不影响后续): {e}")

                # --- 阶段1: 重读日线 ---
                today_str = datetime.date.today().strftime("%Y%m%d")
                stage1_progress_state = {}
                skip_stage1 = False
                try:
                    cached_date = data_provider.load_cache_from_disk()
                    cached_data = getattr(data_provider, "cache_data", {}) or {}
                    if cached_date == today_str and len(cached_data) > 2000:
                        codes_dict = data_provider._get_codes_from_vipdoc()
                        data_provider.code2name = codes_dict
                        _log_and_status(f"[F5] stage1/3: resume from local warehouse cache ({len(cached_data)} symbols)")
                        skip_stage1 = True
                except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as e:
                    log.info(f"[F5] 断点续算检测失败(不影响全量重读): {e}")

                if not skip_stage1:
                    _log_and_status("[F5] 阶段1/3: 清空缓存,开始从 vipdoc 重读...")
                    try:
                        with data_provider.cache_lock:
                            data_provider.cache_data = {}
                        was_online = data_provider.is_online()
                        data_provider.set_online_mode(False)
                        try:
                            codes_dict = data_provider._get_codes_from_vipdoc()
                            _log_and_status(f"[F5] 阶段1/3: 从 vipdoc 扫描到 {len(codes_dict)} 只标的")

                            data_provider.sync_market_data(
                                codes_dict,
                                force_refresh=True,
                                progress_callback=lambda done, total, eta: _handle_stage1_progress(
                                    done, total, eta, set_status_callback, stage1_progress_state
                                ),
                            )
                            data_provider.code2name = codes_dict
                        finally:
                            if was_online:
                                data_provider.set_online_mode(True)
                        count = len(data_provider.cache_data)
                        _log_and_status(f"[F5] 阶段1/3 完成 -- 共加载 {count} 只标的")

                        if _provider_saved_stage1_cache(data_provider, today_str):
                            log.info("[F5] stage1 checkpoint skipped; provider already saved today's cache")
                        else:
                            _save_stage1_checkpoint(data_provider.cache_data, today_str)

                    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
                        log.error(f"[F5] ❌ 阶段1 重读本地数据异常: {e}", exc_info=True)
                        return

                if cancelled_checker and cancelled_checker():
                    _log_and_status("[F5] ⏹ 用户取消")
                    return

                # 阶段1→2 过渡：强制回收阶段1的临时对象（frames、中间DataFrame等）
                # 为什么重要？如果不回收，阶段2的大矩阵+阶段1的残留同时在内存中，峰值直接翻倍
                gc.collect()
                _log_memory("阶段1→2 GC后")

                # --- 阶段2: RPS 矩阵 ---
                _log_and_status("[F5] 阶段2/3: 预计算 RPS 矩阵...")
                try:
                    all_data = {c: df for c, df in data_provider.cache_data.items() if df is not None and len(df) >= 60}
                    log.info(f"[F5] 阶段2/3: 有效标的 {len(all_data)} 只(>=60根K线)")
                    today_str = datetime.date.today().strftime("%Y%m%d")
                    rps_matrix = engine.build_rps_matrix(all_data, today_str, today_str)

                    if rps_matrix:
                        d_str = list(rps_matrix.keys())[-1]
                        d_rps = rps_matrix[d_str]
                        rps120 = d_rps.get("rps120", {})
                        rps250 = d_rps.get("rps250", {})
                        rps_pkg = {"date": d_str, "rps120": rps120, "rps250": rps250}
                        save_json_file(RPS_CACHE_FILE, rps_pkg)
                        remove_cache_file(RPS_CACHE_FILE.replace(".json", ".pkl"))
                        engine.set_precomputed_rps(d_str, rps120, rps250)
                        # 有效排名 = 值不是 NaN 的条目数
                        valid_count = sum(1 for v in rps120.values() if v == v)  # NaN != NaN
                        _log_and_status(f"[F5] 阶段2/3 完成 -- RPS 已存 ({valid_count} 只有效排名)")
                    else:
                        log.warning("[F5] ⚠ 阶段2/3: RPS 矩阵计算返回空")
                    # 释放 rps_matrix 字典（可能上百 MB），只保留已经写入 engine 的 rps120/rps250
                    del rps_matrix
                except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
                    log.error(f"[F5] ❌ 阶段2 RPS 计算异常: {e}", exc_info=True)

                if cancelled_checker and cancelled_checker():
                    _log_and_status("[F5] ⏹ 用户取消")
                    return

                # 阶段2→2.5 过渡：释放 RPS 计算的大矩阵残留
                gc.collect()
                _log_memory("阶段2→2.5 GC后")

                # --- 阶段2.5: 板块 RPS ---
                _log_and_status("[F5] 阶段2.5/3: 预计算板块 RPS...")
                try:
                    from vcp.sector import SectorManager

                    tdx_root = os.path.dirname(data_provider.tdx_vipdoc) if data_provider.tdx_vipdoc else r"D:\HT"
                    sm = SectorManager.get_instance(tdx_root)
                    all_data_f5 = {
                        c: df for c, df in data_provider.cache_data.items() if df is not None and len(df) >= 60
                    }
                    sector_date = datetime.date.today().strftime("%Y%m%d")
                    sector_rps = sm.build_sector_rps(all_data_f5, sector_date)
                    sector_pkg = {"date": sector_date, "sector_rps": sector_rps}
                    save_json_file(SECTOR_RPS_CACHE_FILE, sector_pkg)
                    remove_cache_file(SECTOR_RPS_CACHE_FILE.replace(".json", ".pkl"))
                    _log_and_status(f"[F5] 阶段2.5/3 完成 -- 板块 RPS ({len(sector_rps)} 个)")
                    del all_data_f5, sector_rps, sector_pkg
                except (AttributeError, ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
                    log.error(f"[F5] ❌ 阶段2.5 板块 RPS 异常: {e}", exc_info=True)

                # 全部完成后最终回收
                gc.collect()
                _log_memory("全部完成")

                elapsed = time.time() - total_start
                _log_and_status(f"[F5] ✅ 全部完成 -- 耗时 {elapsed:.1f} 秒")

            except (AttributeError, ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
                log.error(f"[F5] ❌ 预计算过程发生未预期异常: {e}", exc_info=True)
            finally:
                elapsed = time.time() - total_start
                count = len(data_provider.cache_data) if data_provider.cache_data else 0
                log.info(f"[F5] 内部流程结束 (count={count}, elapsed={elapsed:.1f}s)")

                # 收尾过期清理
                try:
                    from core.cache_policy import cleanup_stale_caches

                    cleanup_stale_caches(PROJECT_ROOT)
                except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as e:
                    log.warning(f"[F5] 缓存清理跳过: {e}")

                # 使用回调返送给UI以脱钩
                if done_callback:
                    done_callback(count, elapsed)
