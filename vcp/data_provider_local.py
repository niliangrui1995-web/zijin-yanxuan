# -*- coding: utf-8 -*-
"""Local/offline helper functions for TdxDataProvider."""

from __future__ import annotations

import json
import os
import re
import struct
import threading
from datetime import datetime

import pandas as pd

from core.exceptions import CacheIOError, DataFormatError
from core.json_cache import load_json_file, remove_cache_file, save_json_file
from core.logger import get_logger
from domains.quotes.snapshot import TOTAL_SHARES_KEY
from infra.storage.json_cache_repository import cache_file_signature as _dbf_signature
from infra.tasks.lifecycle import (
    TaskCancelledError,
    TaskDeadlineExceeded,
    bounded_io_timeout,
    raise_if_cancelled,
)
from vcp.constants import CACHE_DIR, MAX_HISTORY_BARS
from vcp.utils import ensure_pandas_dataframe, read_tdx_day_file

_log = get_logger(__name__)
_BASE_DBF_LOCK = threading.RLock()
_BASE_DBF_PATH: str | None = None
_BASE_DBF_SIGNATURE: tuple[int, int] | None = None
_BASE_DBF_CAPITALS: dict[str, dict] | None = None
_GBBQ_CACHE_MTIME_RE = re.compile(r'"mtime"\s*:\s*([0-9]+(?:\.[0-9]+)?)')
_GBBQ_CACHE_MTIME_TAIL_BYTES = 64 * 1024
_BASE_DBF_PARSE_ERRORS = (IndexError, struct.error, TypeError, ValueError)


def serialize_gbbq_cache(data_map: dict) -> dict:
    serialized = {}
    for code, df in (data_map or {}).items():
        if df is None or df.empty:
            continue
        serialized[str(code)] = df.to_dict(orient="records")
    return serialized


def deserialize_gbbq_cache(payload: dict) -> dict:
    restored = {}
    for code, rows in (payload or {}).items():
        if not isinstance(rows, list):
            continue
        restored[str(code)] = pd.DataFrame(rows)
    return restored


def _find_json_array_end(payload: str, start: int) -> int:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(payload)):
        char = payload[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index + 1
    raise ValueError("gbbq code cache array is incomplete")


def _read_gbbq_cache_mtime(gbbq_cache_file: str) -> float | None:
    try:
        size = os.path.getsize(gbbq_cache_file)
        with open(gbbq_cache_file, "rb") as handle:
            if size > _GBBQ_CACHE_MTIME_TAIL_BYTES:
                handle.seek(-_GBBQ_CACHE_MTIME_TAIL_BYTES, os.SEEK_END)
            tail = handle.read().decode("utf-8", errors="ignore")
    except (OSError, TypeError, ValueError):
        return None

    matches = _GBBQ_CACHE_MTIME_RE.findall(tail)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except (TypeError, ValueError):
        return None


def _load_gbbq_cache_rows_for_code(gbbq_cache_file: str, code: str, expected_mtime: float | None) -> list[dict]:
    cached_mtime = _read_gbbq_cache_mtime(gbbq_cache_file) if expected_mtime is not None else None
    if expected_mtime is not None and cached_mtime is not None and cached_mtime != float(expected_mtime):
        raise ValueError("gbbq cache mtime mismatch")

    with open(gbbq_cache_file, "r", encoding="utf-8") as handle:
        payload = handle.read()

    if expected_mtime is not None and cached_mtime is None:
        match = _GBBQ_CACHE_MTIME_RE.search(payload)
        if match and float(match.group(1)) != float(expected_mtime):
            raise ValueError("gbbq cache mtime mismatch")

    needle = f'"{code}":'
    position = payload.find(needle)
    if position < 0:
        return []
    array_start = payload.find("[", position + len(needle))
    if array_start < 0:
        raise ValueError("gbbq code cache array is missing")
    array_end = _find_json_array_end(payload, array_start)
    rows = json.loads(payload[array_start:array_end])
    return rows if isinstance(rows, list) else []


def _parse_tdx_base_dbf(path: str) -> dict[str, dict]:
    global _BASE_DBF_PATH, _BASE_DBF_SIGNATURE, _BASE_DBF_CAPITALS

    signature = _dbf_signature(path)
    with _BASE_DBF_LOCK:
        if path == _BASE_DBF_PATH and signature == _BASE_DBF_SIGNATURE and _BASE_DBF_CAPITALS is not None:
            return _BASE_DBF_CAPITALS

        if signature is None:
            _BASE_DBF_PATH = path
            _BASE_DBF_SIGNATURE = None
            _BASE_DBF_CAPITALS = {}
            return _BASE_DBF_CAPITALS

        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            _log.debug(f"[数据中台] 读取通达信 base.dbf 失败: {exc}")
            _BASE_DBF_PATH = path
            _BASE_DBF_SIGNATURE = signature
            _BASE_DBF_CAPITALS = {}
            return _BASE_DBF_CAPITALS

        try:
            record_count = struct.unpack("<I", raw[4:8])[0]
            header_len = struct.unpack("<H", raw[8:10])[0]
            record_len = struct.unpack("<H", raw[10:12])[0]

            fields = []
            offset = 32
            record_offset = 1
            while offset + 32 <= header_len and raw[offset] != 0x0D:
                desc = raw[offset : offset + 32]
                name = desc[:11].split(b"\x00", 1)[0].decode("gbk", errors="ignore").strip()
                length = int(desc[16])
                fields.append((name, record_offset, length))
                record_offset += length
                offset += 32

            code_field = next((item for item in fields if item[0] == "GPDM"), None)
            capital_field = next((item for item in fields if item[0] == "ZGB"), None)
            if not code_field or not capital_field or record_len <= 0:
                raise ValueError("base.dbf 缺少 GPDM/ZGB 字段")

            capitals: dict[str, dict] = {}
            for i in range(record_count):
                start = header_len + i * record_len
                end = start + record_len
                if end > len(raw):
                    break
                record = raw[start:end]
                if record[:1] == b"*":
                    continue

                _, code_pos, code_len = code_field
                _, capital_pos, capital_len = capital_field
                code = record[code_pos : code_pos + code_len].decode("gbk", errors="ignore").strip()
                zgb_text = record[capital_pos : capital_pos + capital_len].decode("gbk", errors="ignore").strip()
                if len(code) != 6 or not code.isdigit() or not zgb_text:
                    continue

                zgb_wan_shares = float(zgb_text)
                if zgb_wan_shares <= 0:
                    continue
                capitals[code] = {
                    TOTAL_SHARES_KEY: zgb_wan_shares * 10000.0,
                    "source": "tdx_base",
                }
        except _BASE_DBF_PARSE_ERRORS as exc:
            _log.warning("[数据中台] 解析通达信 base.dbf 失败: %s", exc, exc_info=True)
            capitals = {}

        _BASE_DBF_PATH = path
        _BASE_DBF_SIGNATURE = signature
        _BASE_DBF_CAPITALS = capitals
        return _BASE_DBF_CAPITALS


def load_local_tdx_capital_snapshot(codes, tdx_vipdoc: str | None) -> dict[str, dict]:
    """Read total share capital from local Tongdaxin ``base.dbf``.

    The ``ZGB`` field is stored in ten-thousand shares, while quote snapshots use
    raw shares for dynamic market-cap calculation.
    """

    normalized_codes = [
        str(code or "").strip().zfill(6) for code in dict.fromkeys(codes or []) if str(code or "").strip()
    ]
    normalized_codes = [code for code in normalized_codes if len(code) == 6 and code.isdigit()]
    if not normalized_codes or not tdx_vipdoc:
        return {}

    tdx_root = os.path.dirname(tdx_vipdoc)
    base_dbf_path = os.path.join(tdx_root, "T0002", "hq_cache", "base.dbf")
    capital_map = _parse_tdx_base_dbf(base_dbf_path)
    return {code: dict(capital_map[code]) for code in normalized_codes if code in capital_map}


def load_local_gbbq(
    tdx_vipdoc: str | None,
    gbbq_cache_file: str,
    legacy_gbbq_cache_file: str,
    local_gbbq: dict | None = None,
    *,
    force: bool = False,
) -> dict:
    """Load local gbbq ex-rights/ex-dividends data into memory."""

    current = dict(local_gbbq or {})
    if not tdx_vipdoc:
        return current

    tdx_root = os.path.dirname(tdx_vipdoc)
    gbbq_path = os.path.join(tdx_root, "T0002", "hq_cache", "gbbq")
    if not os.path.exists(gbbq_path):
        _log.info(f"[数据中台] 本地 gbbq 文件不存在: {gbbq_path}")
        return current

    gbbq_mtime = os.path.getmtime(gbbq_path)

    if not force and os.path.exists(gbbq_cache_file):
        try:
            cached = load_json_file(gbbq_cache_file)
            if cached.get("mtime") and cached.get("mtime") != gbbq_mtime:
                raise ValueError("gbbq 文件已更新，需要重新解析缓存")
            restored = deserialize_gbbq_cache(cached.get("data", {}))
            remove_cache_file(legacy_gbbq_cache_file)
            _log.info(f"[缓存] 已加载本地 gbbq 缓存: {len(restored)} 个代码, {cached.get('records', '?')} 条记录")
            return restored
        except (CacheIOError, DataFormatError, ValueError) as exc:
            _log.debug(f"[缓存] gbbq JSON 缓存损坏或版本不匹配，将重新解析: {exc}")

    try:
        from pytdx.reader import GbbqReader

        reader = GbbqReader()
        df = reader.get_df(gbbq_path)
        if df is None or df.empty:
            _log.info("[数据中台] gbbq 文件解析为空")
            return current

        xdxr = df[df["category"] == 1].copy()
        rebuilt = {}
        for code, group in xdxr.groupby("code"):
            rebuilt[str(code)] = group

        _log.info(f"[缓存] 已解析本地 gbbq 原始文件: {len(rebuilt)} 个代码, {len(xdxr)} 条记录")
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            save_json_file(
                gbbq_cache_file,
                {
                    "data": serialize_gbbq_cache(rebuilt),
                    "mtime": gbbq_mtime,
                    "records": len(xdxr),
                },
            )
            remove_cache_file(legacy_gbbq_cache_file)
            _log.info(f"[数据中台] gbbq JSON 缓存已保存 -> {gbbq_cache_file}")
        except CacheIOError as exc:
            _log.error(f"[数据中台] gbbq JSON 缓存保存失败: {exc}")
        return rebuilt
    except (AttributeError, ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        _log.error(f"[数据中台] gbbq 加载失败(不影响联网复权): {exc}")
        return current


def load_local_gbbq_for_code(
    tdx_vipdoc: str | None,
    gbbq_cache_file: str,
    legacy_gbbq_cache_file: str,
    local_gbbq: dict | None,
    code: str,
    *,
    force: bool = False,
    fallback_to_full_load: bool = False,
) -> dict:
    """Load one stock's gbbq rows from the JSON cache without materializing all codes."""

    current = dict(local_gbbq or {})
    code_text = str(code or "").strip()
    if not code_text:
        return current
    if code_text in current and not force:
        return current
    if force:
        return load_local_gbbq(
            tdx_vipdoc,
            gbbq_cache_file,
            legacy_gbbq_cache_file,
            current,
            force=True,
        )
    if not tdx_vipdoc:
        return current

    tdx_root = os.path.dirname(tdx_vipdoc)
    gbbq_path = os.path.join(tdx_root, "T0002", "hq_cache", "gbbq")
    if not os.path.exists(gbbq_path):
        return current
    if not os.path.exists(gbbq_cache_file):
        if not fallback_to_full_load:
            _log.debug(f"[缓存] gbbq 单代码缓存不存在，保留现有缓存: {code_text}")
            return current
        return load_local_gbbq(
            tdx_vipdoc,
            gbbq_cache_file,
            legacy_gbbq_cache_file,
            current,
            force=True,
        )

    try:
        rows = _load_gbbq_cache_rows_for_code(
            gbbq_cache_file,
            code_text,
            os.path.getmtime(gbbq_path),
        )
        remove_cache_file(legacy_gbbq_cache_file)
    except (CacheIOError, json.JSONDecodeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        if not fallback_to_full_load:
            _log.debug(f"[缓存] gbbq 单代码缓存不可用，保留现有缓存并延后全量重建: {code_text} {exc}")
            return current
        _log.debug(f"[缓存] gbbq 单代码缓存读取失败，将回退全量加载: {code_text} {exc}")
        return load_local_gbbq(
            tdx_vipdoc,
            gbbq_cache_file,
            legacy_gbbq_cache_file,
            current,
            force=True,
        )

    if rows:
        current[code_text] = pd.DataFrame(rows)
        _log.debug(f"[缓存] 已按需加载 gbbq 单代码缓存: {code_text} {len(rows)} 条记录")
    return current


def get_market_code(stock_code) -> int:
    stock_code = str(stock_code)
    # 920xxx 为北交所，通达信市场号为 2；900xxx 仍为沪市 B 股。
    if stock_code.startswith("92"):
        return 2
    return 1 if stock_code.startswith(("6", "9")) else 0


def tdx_day_path(tdx_vipdoc: str | None, code) -> str:
    code = str(code).strip()
    # 920xxx 存在 vipdoc/bj；900xxx 仍使用 vipdoc/sh。
    if code.startswith("92"):
        sub = os.path.join("bj", "lday", f"bj{code}.day")
    elif code.startswith(("6", "9")):
        sub = os.path.join("sh", "lday", f"sh{code}.day")
    else:
        sub = os.path.join("sz", "lday", f"sz{code}.day")
    return os.path.join(tdx_vipdoc or "", sub)


def _get_xdxr_info_with_deadline(api, market, code, *, cancellation_token=None):
    """Read pytdx adjustment metadata within the owning task's I/O deadline."""
    raise_if_cancelled(cancellation_token)
    client = getattr(api, "client", None)
    set_timeout = getattr(client, "settimeout", None)
    get_timeout = getattr(client, "gettimeout", None)
    timeout_changed = callable(set_timeout) and callable(get_timeout)
    previous_timeout = get_timeout() if timeout_changed else None
    if timeout_changed:
        set_timeout(bounded_io_timeout(5, cancellation_token))
    try:
        try:
            result = api.get_xdxr_info(market, code)
        except Exception:
            raise_if_cancelled(cancellation_token)
            raise
    finally:
        if timeout_changed:
            set_timeout(previous_timeout)
    raise_if_cancelled(cancellation_token)
    return result


def _build_adjustment_events(api, market, code, local_gbbq, cancellation_token):
    if code in (local_gbbq or {}):
        events = local_gbbq[code].copy()
        events["dt"] = pd.to_datetime(
            events["datetime"].astype(str),
            format="%Y%m%d",
            errors="coerce",
        ).dt.date
        events = events.dropna(subset=["dt"])
        return events.set_index("dt").sort_index(ascending=False)
    if api is None:
        return None
    raw_events = _get_xdxr_info_with_deadline(
        api,
        market,
        code,
        cancellation_token=cancellation_token,
    )
    if not raw_events:
        return None
    events = pd.DataFrame(raw_events)
    events = events[events["category"] == 1]
    if events.empty:
        return events
    events["dt"] = pd.to_datetime(
        events[["year", "month", "day"]].astype(str).agg("-".join, axis=1)
    ).dt.date
    return events.set_index("dt").sort_index(ascending=False)


def _prepare_adjustment_frame(df):
    work_df = df.reset_index() if df.index.name == "datetime" else df.copy()
    adjusted_cols = [
        column
        for column in ("open", "high", "low", "close", "vol", "volume")
        if column in work_df.columns
    ]
    for column in adjusted_cols:
        work_df[column] = pd.to_numeric(work_df[column], errors="coerce").astype(float)
    dt_col = pd.to_datetime(work_df["datetime"]).dt.date if "datetime" in work_df.columns else None
    return work_df, dt_col


def _adjustment_terms(row) -> tuple[float, float]:
    if "songgu_qianzongguben" in row.index:
        return (
            float(row.get("songgu_qianzongguben", 0) or 0) / 10.0,
            float(row.get("hongli_panqianliutong", 0) or 0) / 10.0,
        )
    return (
        (float(row.get("songgu", 0) or 0) + float(row.get("houzhen", 0) or 0)) / 10.0,
        float(row.get("fenhong", 0) or 0) / 10.0,
    )


def _adjustment_date(value):
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, str):
        return datetime.strptime(value, "%Y-%m-%d").date()
    return value


def _apply_adjustment_events(work_df, dt_col, events, cancellation_token) -> None:
    if dt_col is None:
        return
    for index in range(len(events)):
        raise_if_cancelled(cancellation_token)
        row = events.iloc[index]
        stock_ratio, dividend = _adjustment_terms(row)
        mask = dt_col < _adjustment_date(events.index[index])
        if not mask.any():
            continue
        for column in ("open", "high", "low", "close"):
            if column in work_df.columns:
                work_df.loc[mask, column] = (work_df.loc[mask, column] - dividend) / (
                    1 + stock_ratio
                )
        for column in ("vol", "volume"):
            if column in work_df.columns:
                work_df.loc[mask, column] = work_df.loc[mask, column] * (1 + stock_ratio)


def apply_forward_adjustment(
    api,
    market,
    code,
    df,
    local_gbbq: dict | None,
    *,
    cancellation_token=None,
):
    """Apply forward adjustment using local gbbq data first, then fall back to online API."""

    raise_if_cancelled(cancellation_token)
    try:
        events = _build_adjustment_events(api, market, code, local_gbbq, cancellation_token)
        if events is None or events.empty:
            return df
        work_df, dt_col = _prepare_adjustment_frame(df)
        _apply_adjustment_events(work_df, dt_col, events, cancellation_token)
        if "datetime" in work_df.columns:
            work_df["datetime"] = pd.to_datetime(work_df["datetime"])
            work_df = work_df.set_index("datetime")
        raise_if_cancelled(cancellation_token)
        return work_df
    except (TaskCancelledError, TaskDeadlineExceeded):
        raise
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        _log.error(f"[数据中台] 前复权计算异常: {exc}", exc_info=True)
        raise ValueError(f"除权除息因子计算失败: {exc}") from exc


def fetch_from_local_tdx(
    code,
    *,
    tdx_vipdoc: str | None,
    offline: bool,
    server_pool,
    local_gbbq: dict | None,
    offline_warn_printed: bool,
) -> tuple[pd.DataFrame | None, bool]:
    if not tdx_vipdoc:
        return None, offline_warn_printed

    path = tdx_day_path(tdx_vipdoc, code)
    df = read_tdx_day_file(path)
    if df is not None and "datetime" in df.columns:
        df = df.set_index("datetime")

    if df is None or df.empty:
        return None, offline_warn_printed
    if len(df) > MAX_HISTORY_BARS:
        df = df.iloc[-MAX_HISTORY_BARS:]

    warn_printed = offline_warn_printed
    if offline or not server_pool:
        if local_gbbq:
            df = apply_forward_adjustment(None, get_market_code(code), code, df, local_gbbq)
        elif not warn_printed:
            _log.warning("[警告] 本地 gbbq 缓存不可用，前复权一致性可能下降")
            _log.info("[提示] 请确认通达信目录下存在 T0002/hq_cache/gbbq 文件")
            warn_printed = True
    return df, warn_printed


def _pandas_history_tail_rows(hist_df) -> tuple[dict, dict, object] | None:
    if not isinstance(hist_df, pd.DataFrame) or hist_df.empty:
        return None
    last_row = hist_df.iloc[-1].to_dict()
    prev_row = hist_df.iloc[-2].to_dict() if len(hist_df) > 1 else last_row
    quote_date = last_row.get("datetime")
    return prev_row, last_row, hist_df.index[-1] if quote_date is None else quote_date


def _native_row_api_tail_rows(hist_df) -> tuple[dict, dict, object] | None:
    row_getter = getattr(hist_df, "row", None)
    if not callable(row_getter):
        return None
    try:
        row_count = int(getattr(hist_df, "height", 0))
        if row_count <= 0:
            return None
        last_row = dict(row_getter(row_count - 1, named=True))
        prev_row = dict(row_getter(row_count - 2, named=True)) if row_count > 1 else last_row
        return prev_row, last_row, last_row.get("datetime")
    except (AttributeError, IndexError, RuntimeError, TypeError, ValueError):
        return None


def _native_dict_api_tail_rows(hist_df) -> tuple[dict, dict, object] | None:
    to_dicts = getattr(hist_df, "to_dicts", None)
    if not callable(to_dicts):
        return None
    try:
        tail = getattr(hist_df, "tail", None)
        rows = (tail(2) if callable(tail) else hist_df).to_dicts()
        if not rows:
            return None
        last_row = dict(rows[-1])
        prev_row = dict(rows[-2]) if len(rows) > 1 else last_row
        return prev_row, last_row, last_row.get("datetime")
    except (AttributeError, IndexError, RuntimeError, TypeError, ValueError):
        return None


def _normalized_history_tail_rows(hist_df) -> tuple[dict, dict, object] | None:
    normalized = ensure_pandas_dataframe(hist_df)
    return _pandas_history_tail_rows(normalized)


def _native_history_tail_rows(hist_df) -> tuple[dict, dict, object] | None:
    """Return the last two bars without converting an entire native frame."""
    if hist_df is None:
        return None
    for extractor in (_pandas_history_tail_rows, _native_row_api_tail_rows, _native_dict_api_tail_rows):
        rows = extractor(hist_df)
        if rows is not None:
            return rows
    return _normalized_history_tail_rows(hist_df)


def _format_quote_date(value) -> str | None:
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def build_offline_quotes(codes, get_data) -> dict:
    """Use the final two cached bars as quotes without whole-frame conversion."""

    res = {}
    for code in codes:
        rows = _native_history_tail_rows(get_data(code))
        if rows is None:
            continue
        prev_row, last_row, quote_date_value = rows
        last_close = float(prev_row.get("close", 0)) if prev_row is not last_row else float(last_row.get("open", 0))
        res[code] = {
            "open": float(last_row.get("open", 0)),
            "high": float(last_row.get("high", 0)),
            "low": float(last_row.get("low", 0)),
            "close": float(last_row.get("close", 0)),
            "volume": float(last_row.get("volume", 0)),
            "amount": float(last_row.get("amount", 0)),
            "last_close": last_close,
            "date": _format_quote_date(quote_date_value),
        }
    return res
