# -*- coding: utf-8 -*-
"""Local/offline helper functions for TdxDataProvider."""

from __future__ import annotations

import os
from datetime import datetime

import pandas as pd

from core.exceptions import CacheIOError, DataFormatError
from core.json_cache import load_json_file, remove_cache_file, save_json_file
from core.logger import get_logger
from vcp.constants import CACHE_DIR, MAX_HISTORY_BARS
from vcp.utils import read_tdx_day_file

_log = get_logger(__name__)


def serialize_gbbq_cache(data_map: dict) -> dict:
    serialized = {}
    for code, df in (data_map or {}).items():
        if df is None or df.empty:
            continue
        serialized[str(code)] = df.to_dict(orient='records')
    return serialized


def deserialize_gbbq_cache(payload: dict) -> dict:
    restored = {}
    for code, rows in (payload or {}).items():
        if not isinstance(rows, list):
            continue
        restored[str(code)] = pd.DataFrame(rows)
    return restored


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
    gbbq_path = os.path.join(tdx_root, 'T0002', 'hq_cache', 'gbbq')
    if not os.path.exists(gbbq_path):
        _log.info(f"[数据中台] 本地 gbbq 文件不存在: {gbbq_path}")
        return current

    gbbq_mtime = os.path.getmtime(gbbq_path)

    if not force and os.path.exists(gbbq_cache_file):
        try:
            cached = load_json_file(gbbq_cache_file)
            if cached.get('mtime') and cached.get('mtime') != gbbq_mtime:
                raise ValueError("gbbq 文件已更新，需要重新解析缓存")
            restored = deserialize_gbbq_cache(cached.get('data', {}))
            remove_cache_file(legacy_gbbq_cache_file)
            _log.info(
                f"[缓存] 已加载本地 gbbq 缓存: {len(restored)} 个代码, {cached.get('records', '?')} 条记录"
            )
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

        xdxr = df[df['category'] == 1].copy()
        rebuilt = {}
        for code, group in xdxr.groupby('code'):
            rebuilt[str(code)] = group

        _log.info(f"[缓存] 已解析本地 gbbq 原始文件: {len(rebuilt)} 个代码, {len(xdxr)} 条记录")
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            save_json_file(
                gbbq_cache_file,
                {
                    'data': serialize_gbbq_cache(rebuilt),
                    'mtime': gbbq_mtime,
                    'records': len(xdxr),
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


def get_market_code(stock_code) -> int:
    stock_code = str(stock_code)
    return 1 if stock_code.startswith(('6', '9')) else 0


def tdx_day_path(tdx_vipdoc: str | None, code) -> str:
    code = str(code).strip()
    if code.startswith(('6', '9')):
        sub = os.path.join('sh', 'lday', f'sh{code}.day')
    else:
        sub = os.path.join('sz', 'lday', f'sz{code}.day')
    return os.path.join(tdx_vipdoc or "", sub)


def apply_forward_adjustment(api, market, code, df, local_gbbq: dict | None):
    """Apply forward adjustment using local gbbq data first, then fall back to online API."""

    try:
        xdxr_df = None
        if code in (local_gbbq or {}):
            local = local_gbbq[code]
            xdxr_df = local.copy()
            xdxr_df['dt'] = pd.to_datetime(
                xdxr_df['datetime'].astype(str),
                format='%Y%m%d',
                errors='coerce',
            ).dt.date
            xdxr_df = xdxr_df.dropna(subset=['dt'])
            xdxr_df = xdxr_df.set_index('dt').sort_index(ascending=False)
        elif api is not None:
            xdxr_data = api.get_xdxr_info(market, code)
            if not xdxr_data:
                return df
            xdxr_df = pd.DataFrame(xdxr_data)
            xdxr_df = xdxr_df[xdxr_df['category'] == 1]
            if xdxr_df.empty:
                return df
            xdxr_df['dt'] = pd.to_datetime(
                xdxr_df[['year', 'month', 'day']].astype(str).agg('-'.join, axis=1)
            ).dt.date
            xdxr_df = xdxr_df.set_index('dt').sort_index(ascending=False)
        else:
            return df

        if xdxr_df is None or xdxr_df.empty:
            return df

        work_df = df.reset_index() if df.index.name == 'datetime' else df.copy()

        if 'datetime' in work_df.columns:
            dt_col = pd.to_datetime(work_df['datetime']).dt.date
        else:
            dt_col = None

        for i in range(len(xdxr_df)):
            row = xdxr_df.iloc[i]
            if 'songgu_qianzongguben' in row.index:
                sz = float(row.get('songgu_qianzongguben', 0) or 0) / 10.0
                fh = float(row.get('hongli_panqianliutong', 0) or 0) / 10.0
            else:
                sz = (float(row.get('songgu', 0) or 0) + float(row.get('houzhen', 0) or 0)) / 10.0
                fh = float(row.get('fenhong', 0) or 0) / 10.0

            dt = xdxr_df.index[i]
            if isinstance(dt, pd.Timestamp):
                dt = dt.date()
            elif isinstance(dt, str):
                dt = datetime.strptime(dt, "%Y-%m-%d").date()

            if dt_col is None:
                continue

            mask = dt_col < dt
            if not mask.any():
                continue

            for col in ['open', 'high', 'low', 'close']:
                if col in work_df.columns:
                    work_df.loc[mask, col] = (work_df.loc[mask, col] - fh) / (1 + sz)
            for vol_col in ['vol', 'volume']:
                if vol_col in work_df.columns:
                    work_df.loc[mask, vol_col] = work_df.loc[mask, vol_col] * (1 + sz)

        if 'datetime' in work_df.columns:
            work_df['datetime'] = pd.to_datetime(work_df['datetime'])
            work_df = work_df.set_index('datetime')
        return work_df
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
    if df is not None and 'datetime' in df.columns:
        df = df.set_index('datetime')

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


def build_offline_quotes(codes, get_data) -> dict:
    """离线模式或无服务器节点时，利用内存中既有的最新日线数据充当当天的最新报价字典进行兜底返回"""

    res = {}
    for code in codes:
        hist_df = get_data(code)
        if hist_df is not None and len(hist_df) > 0:
            last_row = hist_df.iloc[-1]
            prev_row = hist_df.iloc[-2] if len(hist_df) > 1 else last_row
            last_close = float(prev_row['close']) if len(hist_df) > 1 else float(last_row['open'])
            quote_date = None
            try:
                quote_date = pd.Timestamp(hist_df.index[-1]).strftime('%Y-%m-%d')
            except (TypeError, ValueError):
                quote_date = None
            res[code] = {
                'open': float(last_row.get('open', 0)),
                'high': float(last_row.get('high', 0)),
                'low': float(last_row.get('low', 0)),
                'close': float(last_row.get('close', 0)),
                'volume': float(last_row.get('volume', 0)),
                'amount': float(last_row.get('amount', 0)),
                'last_close': last_close,
                'date': quote_date,
            }
    return res
