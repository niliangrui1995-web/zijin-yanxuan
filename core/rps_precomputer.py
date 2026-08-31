# -*- coding: utf-8 -*-
"""Strict, cancellable F5 market/RPS snapshot pipeline."""

from __future__ import annotations

import gc
import math
import os
import time
from pathlib import Path

from app.services.f5_job_contract import (
    F5JobEvent,
    F5JobRequest,
    F5JobResult,
    F5JobStatus,
    F5Phase,
    F5SnapshotArtifacts,
)
from core.exceptions import AppError
from core.f5_resource_guard import (
    F5_FULL_REREAD_MIN_COMMIT_HEADROOM_BYTES,
    F5_WORKER_START_MIN_COMMIT_HEADROOM_BYTES,
    F5MemoryPressureError,
    ensure_f5_commit_headroom,
    read_system_commit_headroom,
)
from core.json_cache import remove_cache_file, save_json_file
from core.logger import get_logger, system_log_backpressure
from core.market_snapshot_dates import infer_effective_trade_date, normalize_trade_date
from core.runtime_paths import DEFAULT_TDX_ROOT, ensure_cache_dir
from domains.market_calendar import MarketCalendar
from infra.market_data.f5_market_snapshot_store import F5MarketSnapshotStore
from infra.market_data.market_data_warehouse import MARKET_DATA_SCHEMA_VERSION, MARKET_DATA_SOURCE_VERSION
from infra.market_data.vipdoc_source_freshness import inspect_vipdoc_daily_source
from infra.storage.file_integrity import fingerprint_file
from infra.tasks.lifecycle import TaskCancelledError, TaskDeadlineExceeded

log = get_logger(__name__)

F5_LOCAL_REREAD_MAX_WORKERS = 2


def _latest_completed_cn_trade_date() -> str:
    try:
        return MarketCalendar.get_latest_completed_trade_date("CN", allow_refresh=False).strftime("%Y%m%d")
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        return ""


def _get_memory_usage_mb() -> float:
    try:
        import psutil

        return psutil.Process().memory_info().rss / 1024 / 1024
    except ImportError:
        return -1.0


def _log_stage1_progress(done: int, total: int, eta: str) -> None:
    if total <= 0 or (done != total and done % 1000 != 0):
        return
    suffix = f" {eta}" if eta else ""
    msg = f"[F5] 阶段1/3: 重读本地数据 {done}/{total}{suffix}"
    log.info(msg)


def _valid_rps_count(values) -> int:
    count = 0
    for value in (values or {}).values():
        try:
            count += int(math.isfinite(float(value)))
        except (TypeError, ValueError):
            continue
    return count


def _log_memory_snapshot(stage_name: str) -> None:
    mem_mb = _get_memory_usage_mb()
    if mem_mb > 0:
        log.info("[F5] 内存快照 [%s]: %.0f MB", stage_name, mem_mb)
    commit = read_system_commit_headroom()
    if commit is not None:
        log.info(
            "[F5] 系统提交余量 [%s]: %d MB / %d MB",
            stage_name,
            commit.headroom_bytes // 1024 // 1024,
            commit.commit_limit_bytes // 1024 // 1024,
        )


def _build_rps_matrix(engine, all_data: dict, trade_date: str, cache_path: str) -> dict:
    from vcp.polars_engine import prices_matrix_cache_scope

    with prices_matrix_cache_scope(cache_path):
        return engine.build_rps_matrix(all_data, trade_date, trade_date)


class _F5EventEmitter:
    def __init__(self, request: F5JobRequest, callback) -> None:
        self.request = request
        self.callback = callback
        self.seq = 0

    def emit(self, phase: F5Phase, message: str, *, completed: int = 0, total: int = 0) -> None:
        if self.callback is None:
            return
        self.seq += 1
        self.callback(
            F5JobEvent(
                run_id=self.request.run_id,
                seq=self.seq,
                phase=phase,
                message=str(message or ""),
                completed=int(completed or 0),
                total=int(total or 0),
            )
        )


class F5VipdocSourceError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = str(error_code or "vipdoc_source_invalid")
        super().__init__(str(message or "vipdoc source validation failed"))


class _F5PipelineExecution:
    def __init__(
        self,
        *,
        data_provider,
        engine,
        request: F5JobRequest,
        cancelled_checker,
        event_callback,
        market_snapshot_writer,
        rps_path: str,
        sector_rps_path: str,
    ) -> None:
        self.data_provider = data_provider
        self.engine = engine
        self.request = request
        self.cancelled_checker = cancelled_checker
        self.market_snapshot_writer = market_snapshot_writer
        self.rps_path = rps_path
        self.sector_rps_path = sector_rps_path
        self.requested_date = request.requested_date
        self.run_id = request.run_id
        self.start_time = time.time()
        self.warnings: list[str] = []
        self.emitter = _F5EventEmitter(request, event_callback)
        self.market_status = None
        self.effective_trade_date = ""
        self.symbol_count = 0
        self.rps_date = ""
        self.rps120 = {}
        self.rps250 = {}
        self.rps_valid_count = 0
        self.sector_count = 0
        self.vipdoc_source = None
        self._last_market_headroom_check_at = 0.0

    def run(self) -> F5JobResult:
        ensure_cache_dir()
        with system_log_backpressure("F5", allowed_info_loggers=(__name__,)):
            try:
                return self._run_stages()
            except (TaskCancelledError, TaskDeadlineExceeded) as exc:
                log.info("[F5] 任务已取消: %s", exc)
                return self._result(F5JobStatus.CANCELLED, error_code="cancelled", error_message=str(exc))
            except F5MemoryPressureError as exc:
                log.warning("[F5] 资源预检拒绝任务: %s", exc)
                return self._result(
                    F5JobStatus.FAILED,
                    error_code=exc.error_code,
                    error_message=str(exc),
                )
            except F5VipdocSourceError as exc:
                log.warning("[F5] 本地数据源校验失败: %s", exc)
                return self._result(F5JobStatus.FAILED, error_code=exc.error_code, error_message=str(exc))
            except MemoryError as exc:
                log.error("[F5] 预计算内存耗尽", exc_info=True)
                return self._result(
                    F5JobStatus.FAILED,
                    error_code="worker_memory_exhausted",
                    error_message=f"MemoryError: {exc}" if str(exc) else "MemoryError",
                )
            except (AppError, AttributeError, ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.error("[F5] 预计算失败: %s", exc, exc_info=True)
                return self._result(F5JobStatus.FAILED, error_code="f5_pipeline_failed", error_message=str(exc))
            except BaseException as exc:
                log.error("[F5] 未预期的预计算异常: %s", exc, exc_info=True)
                return self._result(
                    F5JobStatus.FAILED,
                    error_code="f5_pipeline_crash",
                    error_message=f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__,
                )

    def _run_stages(self) -> F5JobResult:
        self._checkpoint()
        ensure_f5_commit_headroom(F5_WORKER_START_MIN_COMMIT_HEADROOM_BYTES, stage="F5 准备")
        self._emit(F5Phase.PREPARE, "[F5] 盘后一键预计算 -- 开始")
        _log_memory_snapshot("启动基线")
        self._inspect_vipdoc_source()
        self._refresh_adjustments()
        all_data = self._sync_market()
        self._build_rps(all_data)
        self._build_sector_rps(all_data)
        artifacts = self._build_artifacts()
        gc.collect()
        _log_memory_snapshot("全部完成")
        self._emit(F5Phase.VALIDATE, f"[F5] 快照已就绪 -- 耗时 {self._elapsed():.1f} 秒")
        return self._result(F5JobStatus.READY_TO_ACTIVATE, artifacts=artifacts)

    def _refresh_adjustments(self) -> None:
        ensure_f5_commit_headroom(F5_WORKER_START_MIN_COMMIT_HEADROOM_BYTES, stage="F5 gbbq 解析")
        self._emit(F5Phase.GBBQ, "[F5] 阶段0: 重新解析通达信 gbbq 除权除息数据...")
        try:
            self.data_provider.ensure_adjustment_metadata(force=True)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self.warnings.append(f"gbbq 解析异常: {exc}")
            log.warning("[F5] gbbq 解析异常: %s", exc)
        self._checkpoint()

    def _sync_market(self) -> dict:
        self._full_market_sync()
        self._verify_vipdoc_source_stable()
        self._checkpoint()
        cache_data = getattr(self.data_provider, "cache_data", {}) or {}
        self.symbol_count = len(cache_data)
        if self.symbol_count <= 0:
            raise ValueError("market synchronization produced no symbols")
        self._resolve_market_contract(cache_data)
        self._emit(
            F5Phase.MARKET_STAGE,
            f"[F5] 阶段1/3 完成 -- 共加载 {self.symbol_count} 只标的",
            completed=self.symbol_count,
            total=self.symbol_count,
        )
        gc.collect()
        _log_memory_snapshot("阶段1→2 GC后")
        return {code: frame for code, frame in cache_data.items() if frame is not None and len(frame) >= 60}

    def _full_market_sync(self) -> None:
        ensure_f5_commit_headroom(F5_FULL_REREAD_MIN_COMMIT_HEADROOM_BYTES, stage="F5 本地日线全量重读")
        self._emit(F5Phase.MARKET_SYNC, "[F5] 阶段1/3: 开始从 vipdoc 全量重读...")
        with self.data_provider.cache_lock:
            self.data_provider.cache_data = {}
        was_online = self.data_provider.is_online()
        self.data_provider.set_online_mode(False)
        try:
            codes_dict = self.data_provider._get_codes_from_vipdoc()
            if not codes_dict:
                raise ValueError("vipdoc returned no security codes")
            self.data_provider.sync_market_data(codes_dict, **self._market_sync_kwargs())
            self.data_provider.code2name = codes_dict
        finally:
            if was_online:
                self.data_provider.set_online_mode(True)

    def _market_sync_kwargs(self) -> dict:
        return {
            "force_refresh": True,
            "max_workers": F5_LOCAL_REREAD_MAX_WORKERS,
            "progress_callback": self._market_progress,
            "cancellation_checker": self._market_sync_cancelled,
            "snapshot_writer": self._write_market_snapshot,
            "snapshot_date": self.requested_date,
            "load_cached_snapshot_if_empty": False,
        }

    def _write_market_snapshot(self, cache_data, snapshot_date):
        ensure_f5_commit_headroom(F5_FULL_REREAD_MIN_COMMIT_HEADROOM_BYTES, stage="F5 市场快照写入")
        self.market_status = self.market_snapshot_writer(cache_data, snapshot_date)
        return self.market_status

    def _market_progress(self, done: int, total: int, eta: str) -> None:
        self._checkpoint()
        _log_stage1_progress(done, total, eta)
        suffix = f" {eta}" if eta else ""
        message = f"[F5] 重读本地数据 {done}/{total}{suffix}"
        self.emitter.emit(F5Phase.MARKET_SYNC, message, completed=done, total=total)

    def _market_sync_cancelled(self) -> bool:
        now = time.monotonic()
        if now - self._last_market_headroom_check_at >= 0.5:
            ensure_f5_commit_headroom(F5_FULL_REREAD_MIN_COMMIT_HEADROOM_BYTES, stage="F5 本地日线全量重读")
            self._last_market_headroom_check_at = now
        return self._cancelled()

    def _resolve_market_contract(self, cache_data) -> None:
        if self.market_status is None or not self.market_status.ok:
            error = getattr(self.market_status, "error", "market snapshot was not staged")
            raise RuntimeError(error)
        self.effective_trade_date = infer_effective_trade_date(cache_data)
        if not self.effective_trade_date:
            raise ValueError("unable to infer effective trade date")
        source_date = str(getattr(self.vipdoc_source, "effective_trade_date", "") or "")
        if source_date and self.effective_trade_date != source_date:
            raise F5VipdocSourceError(
                "vipdoc_effective_date_mismatch",
                f"vipdoc 最新有效日期 {source_date} 与读取结果 {self.effective_trade_date} 不一致",
            )

    def _build_rps(self, all_data: dict) -> None:
        self._checkpoint()
        ensure_f5_commit_headroom(F5_WORKER_START_MIN_COMMIT_HEADROOM_BYTES, stage="F5 RPS 计算")
        if not all_data:
            raise ValueError("no symbols have at least 60 market bars")
        self._emit(F5Phase.RPS, "[F5] 阶段2/3: 预计算 RPS 矩阵...")
        cache_path = str(Path(self.request.job_dir) / "vcp_prices_matrix.parquet")
        matrix = _build_rps_matrix(self.engine, all_data, self.effective_trade_date, cache_path)
        if not matrix:
            raise ValueError("RPS matrix calculation returned empty")
        key = list(matrix)[-1]
        self.rps_date = normalize_trade_date(key)
        if self.rps_date != self.effective_trade_date:
            raise ValueError("RPS date does not match effective market trade date")
        payload = matrix[key] or {}
        self.rps120, self.rps250 = payload.get("rps120"), payload.get("rps250")
        if not isinstance(self.rps120, dict) or not isinstance(self.rps250, dict):
            raise ValueError("RPS payload is missing rps120/rps250")
        self.rps_valid_count = _valid_rps_count(self.rps120)
        if self.rps_valid_count <= 0:
            raise ValueError("RPS payload has no valid rankings")
        save_json_file(self.rps_path, {"date": self.rps_date, "rps120": self.rps120, "rps250": self.rps250})
        remove_cache_file(self.rps_path.replace(".json", ".pkl"))
        self._emit(F5Phase.RPS, f"[F5] 阶段2/3 完成 -- RPS {self.rps_valid_count} 只有效排名")

    def _build_sector_rps(self, all_data: dict) -> None:
        self._checkpoint()
        ensure_f5_commit_headroom(F5_WORKER_START_MIN_COMMIT_HEADROOM_BYTES, stage="F5 板块 RPS 计算")
        gc.collect()
        _log_memory_snapshot("阶段2→2.5 GC后")
        self._emit(F5Phase.SECTOR_RPS, "[F5] 阶段2.5/3: 预计算板块 RPS...")
        from vcp.sector import SectorManager

        vipdoc = str(getattr(self.data_provider, "tdx_vipdoc", "") or "")
        tdx_root = os.path.dirname(vipdoc) if vipdoc else DEFAULT_TDX_ROOT
        sector_rps = SectorManager.get_instance(tdx_root).build_sector_rps(all_data, self.effective_trade_date)
        if not isinstance(sector_rps, dict) or not sector_rps:
            raise ValueError("sector RPS calculation returned empty")
        save_json_file(self.sector_rps_path, {"date": self.effective_trade_date, "sector_rps": sector_rps})
        remove_cache_file(self.sector_rps_path.replace(".json", ".pkl"))
        self.sector_count = len(sector_rps)
        self._emit(F5Phase.SECTOR_RPS, f"[F5] 阶段2.5/3 完成 -- 板块 RPS {self.sector_count} 个")

    def _build_artifacts(self) -> F5SnapshotArtifacts:
        status = self.market_status
        market_path = Path(status.parquet_path).resolve()
        rps_path = Path(self.rps_path).resolve()
        sector_rps_path = Path(self.sector_rps_path).resolve()
        gbbq_candidate = Path(self.data_provider.gbbq_cache_file)
        gbbq_path = gbbq_candidate.resolve() if gbbq_candidate.is_file() else None
        market_fingerprint = fingerprint_file(market_path)
        rps_fingerprint = fingerprint_file(rps_path)
        sector_rps_fingerprint = fingerprint_file(sector_rps_path)
        gbbq_fingerprint = fingerprint_file(gbbq_path) if gbbq_path is not None else None
        return F5SnapshotArtifacts(
            snapshot_id=self.request.run_id,
            requested_date=self.requested_date,
            effective_trade_date=self.effective_trade_date,
            market_parquet_path=str(market_path),
            market_schema_version=int(status.schema_version or MARKET_DATA_SCHEMA_VERSION),
            market_source="vipdoc",
            market_source_version=MARKET_DATA_SOURCE_VERSION,
            market_symbol_count=int(status.symbol_count),
            market_row_count=int(status.row_count),
            rps_path=str(rps_path),
            rps_date=self.rps_date,
            rps_valid_count=self.rps_valid_count,
            sector_rps_path=str(sector_rps_path),
            sector_date=self.effective_trade_date,
            sector_count=self.sector_count,
            market_size_bytes=market_fingerprint.size_bytes,
            market_sha256=market_fingerprint.sha256,
            rps_size_bytes=rps_fingerprint.size_bytes,
            rps_sha256=rps_fingerprint.sha256,
            sector_rps_size_bytes=sector_rps_fingerprint.size_bytes,
            sector_rps_sha256=sector_rps_fingerprint.sha256,
            gbbq_path=str(gbbq_path) if gbbq_path is not None else "",
            gbbq_size_bytes=gbbq_fingerprint.size_bytes if gbbq_fingerprint is not None else 0,
            gbbq_sha256=gbbq_fingerprint.sha256 if gbbq_fingerprint is not None else "",
            rps250_valid_count=_valid_rps_count(self.rps250),
        )

    def _result(self, status: F5JobStatus, *, artifacts=None, error_code="", error_message="") -> F5JobResult:
        return F5JobResult(
            run_id=self.run_id,
            status=status,
            requested_date=self.requested_date,
            effective_trade_date=self.effective_trade_date,
            symbol_count=self.symbol_count,
            rps_valid_count=self.rps_valid_count,
            sector_count=self.sector_count,
            elapsed_seconds=self._elapsed(),
            artifacts=artifacts,
            error_code=error_code,
            error_message=error_message,
            warnings=tuple(self.warnings),
        )

    def _emit(self, phase: F5Phase, message: str, *, completed: int = 0, total: int = 0) -> None:
        log.info(message)
        self.emitter.emit(phase, message, completed=completed, total=total)

    def _checkpoint(self) -> None:
        if self._cancelled():
            raise TaskCancelledError("F5 job cancelled")

    def _cancelled(self) -> bool:
        return bool(self.cancelled_checker and self.cancelled_checker())

    def _elapsed(self) -> float:
        return time.time() - self.start_time

    def _inspect_vipdoc_source(self) -> None:
        vipdoc = str(getattr(self.data_provider, "tdx_vipdoc", "") or "")
        if not vipdoc or not Path(vipdoc).is_dir():
            return
        report = inspect_vipdoc_daily_source(vipdoc)
        if report.unstable:
            report = inspect_vipdoc_daily_source(vipdoc)
        if report.unstable:
            raise F5VipdocSourceError(
                "vipdoc_source_unstable",
                "通达信本地日线文件正在更新，请稍后重新执行 F5",
            )
        if not report.effective_trade_date or report.dated_symbol_count <= 0:
            raise F5VipdocSourceError(
                "vipdoc_source_unavailable",
                "未从通达信 vipdoc 读取到有效日线记录",
            )
        expected_date = _latest_completed_cn_trade_date()
        if expected_date and report.effective_trade_date < expected_date:
            raise F5VipdocSourceError(
                "vipdoc_source_stale",
                f"通达信本地日线最新日期 {report.effective_trade_date} 落后于最近完成交易日 {expected_date}",
            )
        active_date = self._active_market_trade_date()
        if active_date and report.effective_trade_date < active_date:
            raise F5VipdocSourceError(
                "vipdoc_source_stale",
                f"通达信本地日线最新日期 {report.effective_trade_date} 落后于当前市场快照 {active_date}",
            )
        self.vipdoc_source = report
        try:
            save_json_file(str(Path(self.request.job_dir) / "vipdoc_source.json"), report.to_dict())
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self.warnings.append(f"vipdoc 来源诊断写入失败: {exc}")
        self._emit(
            F5Phase.PREPARE,
            f"[F5] 本地数据源预检 -- {report.effective_trade_date}，{report.symbol_count} 只标的",
        )

    def _verify_vipdoc_source_stable(self) -> None:
        before = self.vipdoc_source
        if before is None:
            return
        after = inspect_vipdoc_daily_source(before.source_path)
        if after.unstable or after.signature != before.signature:
            raise F5VipdocSourceError(
                "vipdoc_source_changed_during_f5",
                "F5 执行期间通达信本地日线发生变化，已拒绝激活不一致快照",
            )

    def _active_market_trade_date(self) -> str:
        try:
            getter = getattr(self.data_provider, "_get_market_data_warehouse", None)
            warehouse = getter() if callable(getter) else getattr(self.data_provider, "market_data_warehouse", None)
            status_reader = getattr(warehouse, "current_status", None)
            status = status_reader(validate_parquet=False) if callable(status_reader) else None
            if status is not None and bool(getattr(status, "ok", False)):
                return normalize_trade_date(getattr(status, "trade_date", ""))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return ""
        return ""


class RPSPrecomputer:
    """Build a complete F5 snapshot and expose only explicit terminal results."""

    @staticmethod
    def run_f5_job(
        request: F5JobRequest,
        *,
        data_provider=None,
        engine=None,
        cancelled_checker=None,
        event_callback=None,
    ) -> F5JobResult:
        """Build job-local files without switching shared manifests or fixed cache paths."""

        Path(request.job_dir).mkdir(parents=True, exist_ok=True)
        Path(request.snapshot_dir).mkdir(parents=True, exist_ok=True)
        data_provider = data_provider or RPSPrecomputer._create_data_provider()
        engine = engine or RPSPrecomputer._create_engine()
        if request.tdx_vipdoc:
            data_provider.tdx_vipdoc = request.tdx_vipdoc
        data_provider.gbbq_cache_file = str(Path(request.snapshot_dir) / "gbbq.json")
        data_provider.legacy_gbbq_cache_file = str(Path(request.snapshot_dir) / "gbbq.pkl")
        store = F5MarketSnapshotStore(request.snapshot_dir)

        def _stage_market(cache_data, _snapshot_date):
            effective_date = infer_effective_trade_date(cache_data)
            if not effective_date:
                raise ValueError("unable to infer effective trade date from market frames")
            return store.stage_market_dataset(cache_data, effective_date)

        return RPSPrecomputer._execute(
            data_provider=data_provider,
            engine=engine,
            request=request,
            cancelled_checker=cancelled_checker,
            event_callback=event_callback,
            market_snapshot_writer=_stage_market,
            rps_path=str(Path(request.snapshot_dir) / "rps.json"),
            sector_rps_path=str(Path(request.snapshot_dir) / "sector_rps.json"),
        )

    @staticmethod
    def _execute(**kwargs) -> F5JobResult:
        return _F5PipelineExecution(**kwargs).run()

    @staticmethod
    def _create_data_provider():
        from app.services.runtime_services import create_data_provider

        return create_data_provider(offline=True)

    @staticmethod
    def _create_engine():
        from app.services.scan_runtime_service import create_scan_engine

        return create_scan_engine()


__all__ = ["F5_LOCAL_REREAD_MAX_WORKERS", "RPSPrecomputer"]
