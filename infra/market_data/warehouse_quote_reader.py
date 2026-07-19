# -*- coding: utf-8 -*-
"""Small tail-only quote reads from the immutable market-data warehouse."""

from __future__ import annotations

from dataclasses import replace

from infra.market_data.market_data_warehouse import (
    REQUIRED_MARKET_COLUMNS,
    PolarsError,
    WarehouseReadResult,
    WarehouseStatus,
)

_QUOTE_FIELDS = ("open", "high", "low", "close", "volume", "amount")
_READ_ERRORS = (ImportError, OSError, RuntimeError, TypeError, ValueError, PolarsError)
_WAREHOUSE_ACTIVE_LAYER = "parquet_sqlite_warehouse"


def _normalize_codes(codes) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(code or "").strip() for code in (codes or []) if str(code or "").strip()))


def _empty_result(warehouse) -> WarehouseReadResult:
    return WarehouseReadResult(
        {},
        WarehouseStatus(
            ok=True,
            dataset=warehouse.dataset,
            parquet_path=str(warehouse.parquet_path),
            data_status="ok",
            active_layer=_WAREHOUSE_ACTIVE_LAYER,
        ),
    )


def _failure(status: WarehouseStatus, data_status: str, error: str) -> WarehouseReadResult:
    return WarehouseReadResult(
        None,
        replace(
            status,
            ok=False,
            data_status=data_status,
            error=error,
            active_layer="warehouse_unavailable",
            fallback_reason=data_status,
        ),
    )


def _collect_tail_rows(parquet_path: str, codes: tuple[str, ...]):
    import polars as pl

    return (
        pl.scan_parquet(parquet_path)
        .filter(pl.col("_code").cast(pl.Utf8).is_in(list(codes)))
        .sort(["_code", "datetime"])
        .group_by("_code", maintain_order=True)
        .tail(2)
        .collect()
    )


def _number(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _quote_date(value) -> str | None:
    if value is None:
        return None
    try:
        return value.strftime("%Y-%m-%d")
    except (AttributeError, TypeError, ValueError):
        text = str(value).strip()
        return text[:10] if text else None


def _quote_from_tail(rows: list[dict]) -> dict[str, float | str | None]:
    last = rows[-1]
    previous = rows[-2] if len(rows) > 1 else last
    quote: dict[str, float | str | None] = {field: _number(last, field) for field in _QUOTE_FIELDS}
    quote["last_close"] = _number(previous, "close") if previous is not last else quote["open"]
    quote["date"] = _quote_date(last.get("datetime"))
    return quote


def _quotes_from_frame(frame) -> dict[str, dict[str, float | str | None]]:
    rows_by_code: dict[str, list[dict]] = {}
    for row in frame.to_dicts():
        code = str(row.pop("_code", "") or "").strip()
        if code:
            rows_by_code.setdefault(code, []).append(row)
    return {code: _quote_from_tail(rows) for code, rows in rows_by_code.items() if rows}


def read_latest_quotes(warehouse, codes) -> WarehouseReadResult:
    """Read only the final two bars per requested symbol and build quote payloads."""

    code_texts = _normalize_codes(codes)
    if not code_texts:
        return _empty_result(warehouse)
    status = warehouse.current_status(validate_parquet=False)
    if not status.ok:
        return WarehouseReadResult(None, status)
    try:
        part = _collect_tail_rows(str(status.parquet_path or warehouse.parquet_path), code_texts)
        missing_columns = sorted(REQUIRED_MARKET_COLUMNS.difference(part.columns))
        if missing_columns:
            return _failure(
                status,
                "schema_incompatible",
                f"missing columns: {', '.join(missing_columns)}",
            )
        quotes = _quotes_from_frame(part)
        return WarehouseReadResult(
            quotes,
            replace(
                status,
                ok=True,
                data_status="ok",
                error="",
                symbol_count=len(quotes),
                row_count=part.height,
                active_layer=_WAREHOUSE_ACTIVE_LAYER,
                fallback_reason="",
            ),
        )
    except _READ_ERRORS as exc:
        return _failure(status, "parquet_unreadable", str(exc))


__all__ = ["read_latest_quotes"]
