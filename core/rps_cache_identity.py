from __future__ import annotations

from hashlib import blake2b


def _tail_value(values):
    positional = getattr(values, "iloc", None)
    return positional[-1] if positional is not None else values[-1]


def _frame_tail_value(frame, column_name: str):
    try:
        raw_columns = getattr(frame, "columns", ())
        columns = tuple(raw_columns) if raw_columns is not None else ()
        if column_name in columns:
            return _tail_value(frame[column_name])
    except (AttributeError, IndexError, KeyError, TypeError):
        return ""
    return ""


def _frame_index_tail_value(frame):
    try:
        return _tail_value(frame.index)
    except (AttributeError, IndexError, KeyError, TypeError):
        return ""


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


def _frame_snapshot_date_value(frame, columns: tuple[str, ...]):
    for column_name in ("datetime", "date", "trade_date"):
        if column_name in columns:
            return _frame_tail_value(frame, column_name)
    return _frame_index_tail_value(frame)


def _frame_snapshot_marker(frame) -> tuple:
    columns = _frame_columns(frame)
    return (
        id(frame),
        _frame_length(frame),
        columns,
        _frame_snapshot_declared_version(frame),
        _frame_snapshot_date_value(frame, columns),
        _frame_tail_value(frame, "close"),
    )


def rps_data_snapshot_version(data_dict: dict) -> str:
    """Return a lightweight identity for the in-memory bars used by an RPS calculation."""
    digest = blake2b(digest_size=16)
    for code in sorted(data_dict, key=str):
        digest.update(repr((str(code), _frame_snapshot_marker(data_dict[code]))).encode("utf-8"))
    return digest.hexdigest()


def rps_cache_key(data_dict: dict, start_date: str, end_date: str) -> tuple[str, str, str]:
    return (str(start_date), str(end_date), rps_data_snapshot_version(data_dict))
