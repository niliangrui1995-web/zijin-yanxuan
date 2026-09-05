from __future__ import annotations

from hashlib import blake2b


def _frame_length(frame) -> int:
    try:
        return len(frame)
    except TypeError:
        return 0


def _frame_columns(frame) -> tuple[str, ...]:
    try:
        raw_columns = getattr(frame, "columns", ())
        if raw_columns is None:
            return ()
        return tuple(str(column) for column in raw_columns)
    except TypeError:
        return ()


def _frame_snapshot_declared_version(frame):
    try:
        attrs = getattr(frame, "attrs", {}) or {}
        return attrs.get("snapshot_version") or attrs.get("snapshot_trade_date") or ""
    except AttributeError:
        return ""


def _update_vector_digest(digest, values) -> None:
    array = values.to_numpy() if hasattr(values, "to_numpy") else values
    dtype = getattr(array, "dtype", None)
    digest.update(str(dtype).encode("utf-8"))
    if dtype is not None and not dtype.hasobject:
        digest.update(array.tobytes())
    else:
        items = array.tolist() if hasattr(array, "tolist") else list(array)
        digest.update(repr(items).encode("utf-8"))


def rps_data_snapshot_version(data_dict: dict) -> str:
    """Bind RPS caches to all source dates and closes, including historical corrections."""
    digest = blake2b(digest_size=16)
    digest.update(b"rps-prices-v2")
    for code in sorted(data_dict, key=str):
        frame = data_dict[code]
        digest.update(repr((str(code), _frame_length(frame), _frame_snapshot_declared_version(frame))).encode("utf-8"))
        columns = _frame_columns(frame)
        date_column = next((name for name in ("datetime", "date", "trade_date") if name in columns), None)
        dates = frame[date_column] if date_column is not None else getattr(frame, "index", ())
        closes = frame["close"] if "close" in columns else ()
        _update_vector_digest(digest, dates)
        _update_vector_digest(digest, closes)
    return digest.hexdigest()


def rps_cache_key(data_dict: dict, start_date: str, end_date: str) -> tuple[str, str, str]:
    return (str(start_date), str(end_date), rps_data_snapshot_version(data_dict))
