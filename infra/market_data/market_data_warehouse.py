# -*- coding: utf-8 -*-
"""Parquet/SQLite-first local market-data warehouse."""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from core.logger import get_logger
from infra.market_data.warehouse_manifest import WarehouseManifest, WarehouseManifestRecord
from vcp.constants import CACHE_DIR

log = get_logger(__name__)

MARKET_DATASET = "cn_daily_bars"
MARKET_DATA_SCHEMA_VERSION = 3
MARKET_DATA_SOURCE_VERSION = "vcp_daily_parquet_v3"
REQUIRED_MARKET_COLUMNS = frozenset({"_code", "datetime", "open", "high", "low", "close"})


@dataclass(frozen=True)
class WarehouseStatus:
    ok: bool
    dataset: str = MARKET_DATASET
    trade_date: str = ""
    schema_version: int = MARKET_DATA_SCHEMA_VERSION
    source: str = ""
    source_version: str = ""
    parquet_path: str = ""
    symbol_count: int = 0
    row_count: int = 0
    updated_at: str = ""
    data_status: str = ""
    error: str = ""
    active_layer: str = ""
    fallback_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WarehouseReadResult:
    data: Any
    status: WarehouseStatus


def _default_parquet_dir() -> Path:
    return Path(CACHE_DIR) / "parquet"


def _atomic_parquet_write(df, final_path: Path, compression: str = "zstd") -> None:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = final_path.with_name(f"{final_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        df.write_parquet(str(tmp_path), compression=compression)
        os.replace(str(tmp_path), str(final_path))
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _frame_to_polars(code: str, frame):
    import polars as pl

    if frame is None:
        return None
    if getattr(frame, "empty", False) or (hasattr(frame, "height") and frame.height == 0):
        return None
    if isinstance(frame, pd.DataFrame):
        temp = frame.reset_index()
        first_col = temp.columns[0]
        if first_col != "datetime" and "datetime" not in temp.columns:
            temp = temp.rename(columns={first_col: "datetime"})
        temp["_code"] = str(code)
        return pl.from_pandas(temp)
    if hasattr(frame, "with_columns"):
        return frame.with_columns(pl.lit(str(code)).alias("_code"))
    return None


def _polars_len_expr(pl):
    return pl.len() if hasattr(pl, "len") else pl.count()


class MarketDataWarehouse:
    """Read/write facade for local market bars and their SQLite manifest."""

    def __init__(
        self,
        *,
        parquet_dir: str | Path | None = None,
        manifest: WarehouseManifest | None = None,
        dataset: str = MARKET_DATASET,
    ) -> None:
        self.parquet_dir = Path(parquet_dir) if parquet_dir is not None else _default_parquet_dir()
        self.manifest = manifest if manifest is not None else WarehouseManifest()
        self.dataset = dataset
        self._lock = threading.RLock()

    @property
    def parquet_path(self) -> Path:
        return self.parquet_dir / "market_data.parquet"

    @property
    def meta_path(self) -> Path:
        return self.parquet_dir / "meta.parquet"

    def is_available(self) -> bool:
        return self.current_status(validate_parquet=True).ok

    def current_status(self, *, validate_parquet: bool = False) -> WarehouseStatus:
        record = self.manifest.latest(self.dataset)
        if record is None:
            return WarehouseStatus(
                ok=False,
                dataset=self.dataset,
                parquet_path=str(self.parquet_path),
                data_status="missing_manifest",
                error="manifest record is missing",
                active_layer="warehouse_unavailable",
                fallback_reason="missing_manifest",
            )

        status = self._status_from_record(record, ok=record.data_status == "ok")
        if record.data_status != "ok":
            return WarehouseStatus(
                **{
                    **status.to_dict(),
                    "ok": False,
                    "active_layer": "warehouse_unavailable",
                    "fallback_reason": record.data_status,
                }
            )
        if record.schema_version != MARKET_DATA_SCHEMA_VERSION:
            return WarehouseStatus(
                **{
                    **status.to_dict(),
                    "ok": False,
                    "data_status": "schema_incompatible",
                    "error": f"expected schema {MARKET_DATA_SCHEMA_VERSION}, got {record.schema_version}",
                    "active_layer": "warehouse_unavailable",
                    "fallback_reason": "schema_incompatible",
                }
            )
        parquet_path = Path(record.parquet_path or self.parquet_path)
        if not parquet_path.exists():
            return WarehouseStatus(
                **{
                    **status.to_dict(),
                    "ok": False,
                    "data_status": "missing_parquet",
                    "error": f"parquet file is missing: {parquet_path}",
                    "active_layer": "warehouse_unavailable",
                    "fallback_reason": "missing_parquet",
                }
            )
        if validate_parquet:
            return self.validate_manifest(record=record)
        return WarehouseStatus(
            **{
                **status.to_dict(),
                "ok": True,
                "active_layer": "parquet_sqlite_warehouse",
            }
        )

    def validate_manifest(self, *, record: WarehouseManifestRecord | None = None) -> WarehouseStatus:
        record = record or self.manifest.latest(self.dataset)
        if record is None:
            return self.current_status(validate_parquet=False)

        base = self._status_from_record(record, ok=False).to_dict()
        parquet_path = Path(record.parquet_path or self.parquet_path)
        if record.schema_version != MARKET_DATA_SCHEMA_VERSION:
            return WarehouseStatus(
                **{
                    **base,
                    "data_status": "schema_incompatible",
                    "error": f"expected schema {MARKET_DATA_SCHEMA_VERSION}, got {record.schema_version}",
                    "active_layer": "warehouse_unavailable",
                    "fallback_reason": "schema_incompatible",
                }
            )
        if not parquet_path.exists():
            return WarehouseStatus(
                **{
                    **base,
                    "data_status": "missing_parquet",
                    "error": f"parquet file is missing: {parquet_path}",
                    "active_layer": "warehouse_unavailable",
                    "fallback_reason": "missing_parquet",
                }
            )
        try:
            inspected = self._inspect_parquet(parquet_path)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return WarehouseStatus(
                **{
                    **base,
                    "data_status": "parquet_unreadable",
                    "error": str(exc),
                    "active_layer": "warehouse_unavailable",
                    "fallback_reason": "parquet_unreadable",
                }
            )

        missing_columns = sorted(REQUIRED_MARKET_COLUMNS.difference(inspected["columns"]))
        if missing_columns:
            return WarehouseStatus(
                **{
                    **base,
                    "data_status": "schema_incompatible",
                    "error": f"missing columns: {', '.join(missing_columns)}",
                    "active_layer": "warehouse_unavailable",
                    "fallback_reason": "schema_incompatible",
                }
            )
        if int(record.row_count or 0) != int(inspected["row_count"] or 0):
            return WarehouseStatus(
                **{
                    **base,
                    "data_status": "manifest_mismatch",
                    "error": f"row_count manifest={record.row_count} parquet={inspected['row_count']}",
                    "active_layer": "warehouse_unavailable",
                    "fallback_reason": "manifest_mismatch",
                }
            )
        if int(record.symbol_count or 0) != int(inspected["symbol_count"] or 0):
            return WarehouseStatus(
                **{
                    **base,
                    "data_status": "manifest_mismatch",
                    "error": f"symbol_count manifest={record.symbol_count} parquet={inspected['symbol_count']}",
                    "active_layer": "warehouse_unavailable",
                    "fallback_reason": "manifest_mismatch",
                }
            )
        return WarehouseStatus(
            **{
                **base,
                "ok": True,
                "data_status": "ok",
                "error": "",
                "row_count": int(inspected["row_count"]),
                "symbol_count": int(inspected["symbol_count"]),
                "active_layer": "parquet_sqlite_warehouse",
                "fallback_reason": "",
            }
        )

    def read_full_market(self) -> WarehouseReadResult:
        status = self.current_status(validate_parquet=False)
        if not status.ok:
            return WarehouseReadResult(None, status)

        try:
            import polars as pl

            with self._lock:
                pl_df = pl.read_parquet(str(Path(status.parquet_path or self.parquet_path)))
            missing_columns = sorted(REQUIRED_MARKET_COLUMNS.difference(pl_df.columns))
            if missing_columns:
                return WarehouseReadResult(
                    None,
                    WarehouseStatus(
                        **{
                            **status.to_dict(),
                            "ok": False,
                            "data_status": "schema_incompatible",
                            "error": f"missing columns: {', '.join(missing_columns)}",
                            "active_layer": "warehouse_unavailable",
                            "fallback_reason": "schema_incompatible",
                        }
                    ),
                )

            cache_data = {}
            for part in pl_df.partition_by("_code", maintain_order=True):
                code = str(part["_code"][0])
                cache_data[code] = part.drop("_code")

            row_count = int(pl_df.height)
            symbol_count = len(cache_data)
            if status.row_count and status.row_count != row_count:
                return WarehouseReadResult(
                    None,
                    WarehouseStatus(
                        **{
                            **status.to_dict(),
                            "ok": False,
                            "data_status": "manifest_mismatch",
                            "error": f"row_count manifest={status.row_count} parquet={row_count}",
                            "active_layer": "warehouse_unavailable",
                            "fallback_reason": "manifest_mismatch",
                        }
                    ),
                )
            if status.symbol_count and status.symbol_count != symbol_count:
                return WarehouseReadResult(
                    None,
                    WarehouseStatus(
                        **{
                            **status.to_dict(),
                            "ok": False,
                            "data_status": "manifest_mismatch",
                            "error": f"symbol_count manifest={status.symbol_count} parquet={symbol_count}",
                            "active_layer": "warehouse_unavailable",
                            "fallback_reason": "manifest_mismatch",
                        }
                    ),
                )
            return WarehouseReadResult(
                cache_data,
                WarehouseStatus(
                    **{
                        **status.to_dict(),
                        "ok": True,
                        "row_count": row_count,
                        "symbol_count": symbol_count,
                        "active_layer": "parquet_sqlite_warehouse",
                    }
                ),
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return WarehouseReadResult(
                None,
                WarehouseStatus(
                    **{
                        **status.to_dict(),
                        "ok": False,
                        "data_status": "parquet_unreadable",
                        "error": str(exc),
                        "active_layer": "warehouse_unavailable",
                        "fallback_reason": "parquet_unreadable",
                    }
                ),
            )

    def read_symbol(self, code: str) -> WarehouseReadResult:
        code_text = str(code or "").strip()
        status = self.current_status(validate_parquet=False)
        if not code_text or not status.ok:
            return WarehouseReadResult(None, status)

        try:
            import polars as pl

            with self._lock:
                part = (
                    pl.scan_parquet(str(Path(status.parquet_path or self.parquet_path)))
                    .filter(pl.col("_code").cast(pl.Utf8) == code_text)
                    .collect()
                )
            if part.height == 0:
                return WarehouseReadResult(
                    None,
                    WarehouseStatus(
                        **{
                            **status.to_dict(),
                            "ok": False,
                            "data_status": "symbol_missing",
                            "error": f"symbol is missing from warehouse: {code_text}",
                            "active_layer": "warehouse_unavailable",
                            "fallback_reason": "symbol_missing",
                        }
                    ),
                )
            missing_columns = sorted(REQUIRED_MARKET_COLUMNS.difference(part.columns))
            if missing_columns:
                return WarehouseReadResult(
                    None,
                    WarehouseStatus(
                        **{
                            **status.to_dict(),
                            "ok": False,
                            "data_status": "schema_incompatible",
                            "error": f"missing columns: {', '.join(missing_columns)}",
                            "active_layer": "warehouse_unavailable",
                            "fallback_reason": "schema_incompatible",
                        }
                    ),
                )
            if "_code" in part.columns:
                part = part.drop("_code")
            frame = part.to_pandas()
            if "datetime" in frame.columns and not isinstance(frame.index, pd.DatetimeIndex):
                frame = frame.copy()
                frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
                frame = frame.dropna(subset=["datetime"]).set_index("datetime")
            return WarehouseReadResult(
                frame,
                WarehouseStatus(
                    **{
                        **status.to_dict(),
                        "ok": True,
                        "symbol_count": 1,
                        "row_count": int(len(frame)),
                        "active_layer": "parquet_sqlite_warehouse",
                    }
                ),
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            return WarehouseReadResult(
                None,
                WarehouseStatus(
                    **{
                        **status.to_dict(),
                        "ok": False,
                        "data_status": "parquet_unreadable",
                        "error": str(exc),
                        "active_layer": "warehouse_unavailable",
                        "fallback_reason": "parquet_unreadable",
                    }
                ),
            )

    def write_market_dataset(
        self,
        cache_data: dict[str, Any],
        trade_date: str,
        *,
        source: str = "vipdoc",
        source_version: str = MARKET_DATA_SOURCE_VERSION,
    ) -> WarehouseStatus:
        try:
            import polars as pl
        except ImportError as exc:
            return WarehouseStatus(
                ok=False,
                dataset=self.dataset,
                trade_date=str(trade_date or ""),
                parquet_path=str(self.parquet_path),
                data_status="dependency_missing",
                error=str(exc),
                active_layer="warehouse_unavailable",
                fallback_reason="dependency_missing",
            )

        frames = []
        for code, frame in (cache_data or {}).items():
            try:
                converted = _frame_to_polars(str(code), frame)
            except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
                log.debug("[warehouse] skip %s while converting to parquet: %s", code, exc)
                converted = None
            if converted is not None:
                frames.append(converted)
        if not frames:
            return WarehouseStatus(
                ok=False,
                dataset=self.dataset,
                trade_date=str(trade_date or ""),
                parquet_path=str(self.parquet_path),
                data_status="empty_dataset",
                error="no market frames to write",
                active_layer="warehouse_unavailable",
                fallback_reason="empty_dataset",
            )

        with self._lock:
            data = pl.concat(frames, how="vertical_relaxed")
            actual_symbol_count = int(data["_code"].n_unique()) if "_code" in data.columns else 0
            _atomic_parquet_write(data, self.parquet_path, compression="zstd")
            meta = pl.DataFrame(
                {
                    "date": [str(trade_date or "")],
                    "n_stocks": [actual_symbol_count],
                    "version": [MARKET_DATA_SCHEMA_VERSION],
                }
            )
            _atomic_parquet_write(meta, self.meta_path, compression="zstd")

        return self.register_existing_parquet(
            trade_date=str(trade_date or ""),
            source=source,
            source_version=source_version,
            row_count=int(data.height),
            symbol_count=actual_symbol_count,
        )

    def register_existing_parquet(
        self,
        *,
        trade_date: str = "",
        source: str = "legacy_parquet",
        source_version: str = MARKET_DATA_SOURCE_VERSION,
        row_count: int | None = None,
        symbol_count: int | None = None,
    ) -> WarehouseStatus:
        parquet_path = self.parquet_path
        if not parquet_path.exists():
            return WarehouseStatus(
                ok=False,
                dataset=self.dataset,
                trade_date=str(trade_date or ""),
                parquet_path=str(parquet_path),
                data_status="missing_parquet",
                error=f"parquet file is missing: {parquet_path}",
                active_layer="warehouse_unavailable",
                fallback_reason="missing_parquet",
            )
        if not trade_date:
            trade_date = self._read_meta_trade_date()
        try:
            inspected = self._inspect_parquet(parquet_path)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return WarehouseStatus(
                ok=False,
                dataset=self.dataset,
                trade_date=str(trade_date or ""),
                parquet_path=str(parquet_path),
                data_status="parquet_unreadable",
                error=str(exc),
                active_layer="warehouse_unavailable",
                fallback_reason="parquet_unreadable",
            )
        missing_columns = sorted(REQUIRED_MARKET_COLUMNS.difference(inspected["columns"]))
        if missing_columns:
            return WarehouseStatus(
                ok=False,
                dataset=self.dataset,
                trade_date=str(trade_date or ""),
                parquet_path=str(parquet_path),
                data_status="schema_incompatible",
                error=f"missing columns: {', '.join(missing_columns)}",
                active_layer="warehouse_unavailable",
                fallback_reason="schema_incompatible",
            )

        actual_rows = int(inspected["row_count"])
        actual_symbols = int(inspected["symbol_count"])
        record = WarehouseManifestRecord.build(
            dataset=self.dataset,
            trade_date=str(trade_date or ""),
            schema_version=MARKET_DATA_SCHEMA_VERSION,
            source=source,
            source_version=source_version,
            parquet_path=str(parquet_path),
            symbol_count=actual_symbols,
            row_count=actual_rows,
            data_status="ok",
        )
        self.manifest.upsert(record)
        return WarehouseStatus(
            ok=True,
            dataset=self.dataset,
            trade_date=record.trade_date,
            schema_version=record.schema_version,
            source=record.source,
            source_version=record.source_version,
            parquet_path=record.parquet_path,
            symbol_count=record.symbol_count,
            row_count=record.row_count,
            updated_at=record.updated_at,
            data_status=record.data_status,
            active_layer="parquet_sqlite_warehouse",
        )

    def _inspect_parquet(self, parquet_path: Path) -> dict[str, Any]:
        import polars as pl

        scan = pl.scan_parquet(str(parquet_path))
        try:
            columns = set(scan.collect_schema().names())
        except AttributeError:
            columns = set(scan.schema.keys())
        if "_code" not in columns:
            return {"columns": columns, "row_count": 0, "symbol_count": 0}
        summary = scan.select(
            [
                _polars_len_expr(pl).alias("row_count"),
                pl.col("_code").n_unique().alias("symbol_count"),
            ]
        ).collect()
        return {
            "columns": columns,
            "row_count": int(summary["row_count"][0]),
            "symbol_count": int(summary["symbol_count"][0]),
        }

    def _read_meta_trade_date(self) -> str:
        if not self.meta_path.exists():
            return ""
        try:
            import polars as pl

            meta = pl.read_parquet(str(self.meta_path))
            if "date" in meta.columns and meta.height > 0:
                return str(meta["date"][0])
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            return ""
        return ""

    def _status_from_record(self, record: WarehouseManifestRecord, *, ok: bool) -> WarehouseStatus:
        return WarehouseStatus(
            ok=ok,
            dataset=record.dataset,
            trade_date=record.trade_date,
            schema_version=record.schema_version,
            source=record.source,
            source_version=record.source_version,
            parquet_path=record.parquet_path,
            symbol_count=record.symbol_count,
            row_count=record.row_count,
            updated_at=record.updated_at,
            data_status=record.data_status,
            error=record.error,
        )


_DEFAULT_WAREHOUSE: MarketDataWarehouse | None = None
_DEFAULT_WAREHOUSE_LOCK = threading.Lock()


def get_default_market_data_warehouse() -> MarketDataWarehouse:
    global _DEFAULT_WAREHOUSE
    if _DEFAULT_WAREHOUSE is None:
        with _DEFAULT_WAREHOUSE_LOCK:
            if _DEFAULT_WAREHOUSE is None:
                _DEFAULT_WAREHOUSE = MarketDataWarehouse()
    return _DEFAULT_WAREHOUSE
