# polars_engine.py - 高性能加速引擎 v2
# 三大优化: ①numpy 并行 pct_change+rank ②Parquet 缓存 ③增量 RPS

import contextvars
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta

import numpy as np
import polars as pl

from core.logger import get_logger
from core.rps_cache_identity import rps_cache_key
from vcp.constants import CACHE_DIR, DATE_FMT, RPS_BUFFER_DAYS

_log = get_logger(__name__)
_PRICES_MATRIX_LOCK = threading.Lock()
_PARQUET_CACHE_LOCK = threading.Lock()
_POLARS_EXCEPTION_TYPES = tuple(
    exc_type
    for exc_type in (
        getattr(pl.exceptions, "PolarsError", None),
        getattr(pl.exceptions, "ColumnNotFoundError", None),
        getattr(pl.exceptions, "ComputeError", None),
        getattr(pl.exceptions, "InvalidOperationError", None),
        getattr(pl.exceptions, "SchemaError", None),
        getattr(pl.exceptions, "ShapeError", None),
    )
    if isinstance(exc_type, type)
)
_POLARS_RUNTIME_ERRORS = (
    AttributeError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
) + _POLARS_EXCEPTION_TYPES
_POLARS_DATA_ERRORS = (
    AttributeError,
    IndexError,
    KeyError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
) + _POLARS_EXCEPTION_TYPES


def _atomic_parquet_write(df: pl.DataFrame, final_path: str, compression: str = "zstd") -> None:
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    tmp_path = f"{final_path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        df.write_parquet(tmp_path, compression=compression)
        os.replace(tmp_path, final_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


# ================================================================
# 工具函数: numpy 向量化 pct_change + rank
# ================================================================


def _numpy_pct_change(matrix: np.ndarray, period: int) -> np.ndarray:
    """纯 numpy pct_change — 比 pandas 快 3-5x

    matrix: shape (n_dates, n_stocks), float64, 可含 NaN
    返回: 同 shape 矩阵, 前 period 行为 NaN
    """
    result = np.full_like(matrix, np.nan)
    if matrix.shape[0] > period:
        # 向量化除法: current / past - 1
        result[period:] = matrix[period:] / matrix[:-period] - 1.0
    return result


def _numpy_rank_pct_axis1(matrix: np.ndarray) -> np.ndarray:
    """纯 numpy 横向百分位排名 — 替代 pandas rank(axis=1, pct=True)

    对每一行的非 NaN 值排名，返回百分位 (0~1)

    #7: 优先使用 scipy.stats.rankdata 批量排名（C 级实现），
    比原先的 Python for 循环快 2-3x。scipy 不可用时自动退化为手写循环。
    """
    n_rows, _ = matrix.shape
    result = np.full_like(matrix, np.nan)

    try:
        from scipy.stats import rankdata

        # scipy 路径: 逐行调用 C 级 rankdata，NaN 用 'omit' 策略
        for i in range(n_rows):
            row = matrix[i]
            valid_mask = ~np.isnan(row)
            valid_count = valid_mask.sum()
            if valid_count < 2:
                continue
            valid_vals = row[valid_mask]
            ranks = rankdata(valid_vals, method="ordinal")
            result[i, valid_mask] = ranks / valid_count
    except ImportError:
        # scipy 未安装时退化为原始 numpy 实现
        for i in range(n_rows):
            row = matrix[i]
            valid_mask = ~np.isnan(row)
            valid_count = valid_mask.sum()
            if valid_count < 2:
                continue
            valid_vals = row[valid_mask]
            order = np.argsort(np.argsort(valid_vals))
            pct_rank = (order + 1.0) / valid_count
            result[i, valid_mask] = pct_rank

    return result


# ================================================================
# 优化1: 价格矩阵快速构建（纯 Polars）
# ================================================================


def _to_pldf(df) -> pl.DataFrame | None:
    """将 Polars 或 Pandas DataFrame 统一转为 pl.DataFrame，防御式兼容"""
    if df is None:
        return None
    if isinstance(df, pl.DataFrame):
        return df

    import pandas as pd

    if isinstance(df, pd.DataFrame):
        temp = df.reset_index() if df.index.name == "datetime" else df
        return pl.from_pandas(temp)
    return None


def _as_date(value, dtype):
    if dtype == pl.String:
        return value.str.to_date(strict=False)
    return value.cast(pl.Date)


def build_prices_matrix_fast(
    data_dict: dict,
    min_start,
    end_ts=None,
) -> tuple[np.ndarray, list[str], np.ndarray]:
    """高性能价格矩阵构建 — 纯 Polars 实现

    返回: (matrix: np.ndarray [n_dates x n_stocks], columns: list[str], dates: np.ndarray[datetime.date])
    用 numpy 宽矩阵做后续 pct_change + rank，完全跳过 Pandas 中转。
    """
    t0 = time.time()

    # 统一日期边界到 datetime.date
    if isinstance(min_start, str):
        min_start_date = datetime.strptime(min_start.replace("-", ""), "%Y%m%d").date()
    elif hasattr(min_start, "date"):
        min_start_date = min_start.date() if callable(getattr(min_start, "date")) else min_start
    else:
        min_start_date = min_start

    end_date_val = None
    if end_ts is not None:
        if isinstance(end_ts, str):
            end_date_val = datetime.strptime(end_ts.replace("-", ""), "%Y%m%d").date()
        elif hasattr(end_ts, "date"):
            end_date_val = end_ts.date() if callable(getattr(end_ts, "date")) else end_ts
        else:
            end_date_val = end_ts

    # 收集所有股票的 (date, close) 窄表，再做一次性 pivot
    frames = []
    for code, df in data_dict.items():
        pldf = _to_pldf(df)
        if pldf is None or pldf.height == 0:
            continue
        if "close" not in pldf.columns or "datetime" not in pldf.columns:
            continue
        try:
            # 统一 datetime 为 Date 类型
            sub = pldf.select(
                [
                    _as_date(pl.col("datetime"), pldf.schema.get("datetime")).alias("date"),
                    pl.col("close"),
                ]
            )
            # 日期过滤
            sub = sub.filter(pl.col("date") >= pl.lit(min_start_date))
            if end_date_val is not None:
                sub = sub.filter(pl.col("date") <= pl.lit(end_date_val))
            if sub.height == 0:
                continue
            sub = sub.with_columns(pl.lit(code).alias("code"))
            frames.append(sub)
        except _POLARS_DATA_ERRORS as _e:
            _log.debug(f"[加速引擎] 矩阵构建跳过一只: {_e}")
            continue

    if not frames:
        return np.array([]).reshape(0, 0), [], np.array([])

    # 垂直拼接后 pivot 成宽表
    long_df = pl.concat(frames, how="vertical_relaxed")
    del frames

    wide = long_df.pivot(
        on="code",
        index="date",
        values="close",
    ).sort("date")
    # 为什么在这里 del？concat 的 long_df 和 pivot 的 wide 同时存在时峰值翻倍（各约 200MB）
    del long_df

    # 前向填充最多10天（替代 pd.ffill(limit=10)）
    stock_cols = [c for c in wide.columns if c != "date"]
    wide = wide.with_columns([pl.col(c).forward_fill(limit=10) for c in stock_cols])

    dates_arr = wide["date"].to_numpy()
    matrix = wide.select(stock_cols).to_numpy()

    elapsed = time.time() - t0
    _log.info(
        f"[加速引擎] 价格矩阵构建完成(纯Polars): {len(stock_cols)} 只 × {len(dates_arr)} 日 (耗时 {elapsed:.2f}s)"
    )
    return matrix, stock_cols, dates_arr


# ================================================================
# 优化1+3: RPS 矩阵 — numpy 向量化 + 增量缓存
# ================================================================

# 增量 RPS 价格矩阵磁盘缓存路径
_PRICES_MATRIX_CACHE = os.environ.get(
    "VCP_RPS_MATRIX_CACHE",
    os.path.join(CACHE_DIR, "vcp_prices_matrix.parquet"),
)
_PRICES_MATRIX_CACHE_OVERRIDE = contextvars.ContextVar("vcp_prices_matrix_cache", default="")


def _prices_matrix_cache_path() -> str:
    return _PRICES_MATRIX_CACHE_OVERRIDE.get() or _PRICES_MATRIX_CACHE


@contextmanager
def prices_matrix_cache_scope(path: str):
    token = _PRICES_MATRIX_CACHE_OVERRIDE.set(str(path or ""))
    try:
        yield
    finally:
        _PRICES_MATRIX_CACHE_OVERRIDE.reset(token)


def _save_prices_matrix(matrix: np.ndarray, columns: list[str], dates: np.ndarray) -> None:
    """将价格矩阵保存为 Parquet（增量 RPS 基底）— 纯 Polars"""
    try:
        # 构造 Polars DataFrame: date列 + 每只股票一列
        data_dict = {"date": dates}
        for i, col_name in enumerate(columns):
            data_dict[col_name] = matrix[:, i]
        save_df = pl.DataFrame(data_dict)
        cache_path = _prices_matrix_cache_path()
        with _PRICES_MATRIX_LOCK:
            _atomic_parquet_write(save_df, cache_path, compression="zstd")
    except _POLARS_RUNTIME_ERRORS as e:
        _log.error(f"[加速引擎] 价格矩阵缓存保存失败: {e}")


def _load_prices_matrix() -> tuple[np.ndarray, list[str], np.ndarray] | None:
    """从 Parquet 加载历史价格矩阵 — 纯 Polars"""
    cache_path = _prices_matrix_cache_path()
    if not os.path.exists(cache_path):
        return None
    try:
        with _PRICES_MATRIX_LOCK:
            df = pl.read_parquet(cache_path)
        if df.height == 0:
            return None
        dates = df["date"].to_numpy()
        stock_cols = [c for c in df.columns if c != "date"]
        matrix = df.select(stock_cols).to_numpy()
        return matrix, stock_cols, dates
    except _POLARS_RUNTIME_ERRORS as e:
        _log.error(f"[加速引擎] 价格矩阵缓存加载失败: {e}")
        return None


def build_rps_matrix_pl(
    data_dict: dict,
    start_date: str,
    end_date: str,
    rps_cache: dict | None = None,
) -> dict:
    """高性能 RPS 矩阵计算 v3 — 全面去除 Pandas 依赖

    三重优化:
    1. 价格矩阵: Polars pivot 构建 + 增量复用（Parquet 缓存历史矩阵）
    2. pct_change: numpy 向量化（C 级除法）
    3. rank: numpy/scipy argsort
    """
    num_stocks = len(data_dict)

    # 缓存检查
    if rps_cache is not None:
        cache_key = rps_cache_key(data_dict, start_date, end_date)
        if cache_key in rps_cache:
            _log.debug(f"[加速引擎] RPS 矩阵命中缓存 (区间 {start_date} ~ {end_date})，跳过重算")
            return rps_cache[cache_key]

    _log.info(f"\n[加速引擎] 正在计算全市场 RPS 强度矩阵... (标的数: {num_stocks})")
    t_total = time.time()

    start_dt = datetime.strptime(start_date.replace("-", ""), "%Y%m%d").date()
    end_dt = datetime.strptime(end_date.replace("-", ""), "%Y%m%d").date()
    min_start_dt = start_dt - timedelta(days=RPS_BUFFER_DAYS)

    # ---- 增量复用: 尝试加载历史矩阵 ----
    matrix = None
    columns = None
    dates_arr = None

    cached = _load_prices_matrix()
    if cached is not None:
        c_matrix, c_columns, c_dates = cached
        if len(c_dates) > 0:
            existing_end = c_dates[-1]
            if hasattr(existing_end, "astype"):
                import numpy as _np

                existing_end_date = _np.datetime64(existing_end, "D").astype("datetime64[D]").astype(object)
                existing_start_date = _np.datetime64(c_dates[0], "D").astype("datetime64[D]").astype(object)
            else:
                existing_end_date = existing_end
                existing_start_date = c_dates[0]

            if existing_end_date >= end_dt and existing_start_date <= min_start_dt:
                # 完全覆盖，从缓存切片
                mask = (c_dates >= np.datetime64(min_start_dt)) & (c_dates <= np.datetime64(end_dt))
                idx = np.where(mask)[0]
                if len(idx) > 0:
                    matrix = c_matrix[idx]
                    columns = c_columns
                    dates_arr = c_dates[idx]
                    _log.info(f"[加速引擎] 增量复用: 价格矩阵全命中 (缓存至 {existing_end_date})")

    # 如果增量失败，全量构建
    if matrix is None or matrix.size == 0:
        matrix, columns, dates_arr = build_prices_matrix_fast(data_dict, min_start_dt, end_dt)

    if matrix.size == 0:
        _log.warning("[加速引擎] ⚠ 无可用价格数据")
        return {}

    # 为什么 _save_prices_matrix 推迟到下面？在 pct_change + rank 之前保存会使得
    # 价格矩阵（~20MB）+ Polars 序列化临时副本（~200MB）同时与后续 6 个大数组共存
    # 推迟到 pct/rank 数组释放后再保存，峰值能降约 200MB
    _deferred_save_data = (matrix.copy(), list(columns), dates_arr.copy())

    # ---- numpy 向量化 pct_change + rank ----
    t1 = time.time()

    # 分步计算并立即释放中间矩阵，降低峰值内存约 100MB
    pct50 = _numpy_pct_change(matrix, 50)
    rps50_arr = _numpy_rank_pct_axis1(pct50) * 100
    del pct50

    pct120 = _numpy_pct_change(matrix, 120)
    rps120_arr = _numpy_rank_pct_axis1(pct120) * 100
    del pct120

    pct250 = _numpy_pct_change(matrix, 250)
    rps250_arr = _numpy_rank_pct_axis1(pct250) * 100
    del pct250

    _log.info(f"[加速引擎] numpy pct_change + rank 完成 (耗时 {time.time() - t1:.2f}s)")

    # ---- 组装返回字典 ----
    # 将 numpy datetime64 数组转为 date，用于过滤
    start_np = np.datetime64(start_dt)
    end_np = np.datetime64(end_dt)
    target_mask = (dates_arr >= start_np) & (dates_arr <= end_np)
    target_indices = np.where(target_mask)[0]

    # 【修复】如果是周末/节假日F5预计算，当天的K线不存在，会导致 target_indices 为空
    # 此时自动向左退化，提取价格矩阵里存在的最后一个最近交易日
    if len(target_indices) == 0 and len(dates_arr) > 0 and end_np >= dates_arr[0]:
        target_indices = [len(dates_arr) - 1]

    result = {}
    for idx in target_indices:
        d = dates_arr[idx]
        r50 = rps50_arr[idx]
        r120 = rps120_arr[idx]
        r250 = rps250_arr[idx]
        valid = ~np.isnan(r120) & ~np.isnan(r250)
        # numpy datetime64 -> 字符串
        if hasattr(d, "astype"):
            d_date = np.datetime64(d, "D").astype("datetime64[D]").astype(object)
        else:
            d_date = d
        d_str = d_date.strftime(DATE_FMT) if hasattr(d_date, "strftime") else str(d_date).replace("-", "")
        result[d_str] = {
            "rps50": {columns[j]: float(r50[j]) for j in range(len(columns)) if valid[j]},
            "rps120": {columns[j]: float(r120[j]) for j in range(len(columns)) if valid[j]},
            "rps250": {columns[j]: float(r250[j]) for j in range(len(columns)) if valid[j]},
        }

    # 释放 6 个大数组后再保存价格矩阵（推迟保存策略，降低峰值内存约 200MB）
    del rps50_arr, rps120_arr, rps250_arr, matrix, dates_arr
    import gc as _gc

    _gc.collect()

    # 延迟保存：此时 pct/rank 数组已释放，内存处于低谷
    if _deferred_save_data is not None:
        _save_prices_matrix(*_deferred_save_data)
        del _deferred_save_data

    elapsed_total = time.time() - t_total
    _log.info(
        f"[加速引擎] RPS 矩阵构建完成(纯Polars) — 参与标的 {len(columns)} 只 | "
        f"扫描交易日 {len(target_indices)} 个 | 总耗时 {elapsed_total:.2f}s"
    )

    if rps_cache is not None:
        cache_key = rps_cache_key(data_dict, start_date, end_date)
        rps_cache[cache_key] = result

    return result


# ================================================================
# 优化2: Parquet 缓存替代 pickle
# ================================================================

_PARQUET_CACHE_DIR = os.path.join(CACHE_DIR, "parquet")


def save_cache_parquet(cache_data: dict, date_str: str) -> bool:
    """将全市场数据保存为 Parquet 格式（纯 Polars 版本，替代 pickle 和 pandas concat）

    策略: 将每只股票的 DataFrame 合并为一个大表
    columns: datetime, open, high, low, close, volume, amount, _code
    用 Polars 写入，zstd 压缩
    """
    try:
        import polars as pl
    except ImportError:
        return False

    t0 = time.time()
    os.makedirs(_PARQUET_CACHE_DIR, exist_ok=True)

    import gc as _gc

    frames = []
    for code, df in cache_data.items():
        if df is None or getattr(df, "empty", False) or (hasattr(df, "height") and df.height == 0):
            continue
        try:
            import pandas as pd

            if isinstance(df, pd.DataFrame):
                temp = df.reset_index()
                idx_col = temp.columns[0]
                if idx_col != "datetime":
                    temp = temp.rename(columns={idx_col: "datetime"})
                temp["_code"] = code
                frames.append(pl.from_pandas(temp))
            else:
                temp = df.with_columns(pl.lit(code).alias("_code"))
                frames.append(temp)
        except _POLARS_DATA_ERRORS as _e:
            _log.debug(f"[加速引擎] Parquet 保存时转换失败: {_e}")
            continue

    if not frames:
        return False

    # 使用 Polars 高效垂直拼接
    pl_df = pl.concat(frames, how="vertical_relaxed")
    del frames
    _gc.collect()

    try:
        from infra.market_data.market_data_warehouse import get_default_market_data_warehouse

        status = get_default_market_data_warehouse().write_polars_dataset(
            pl_df,
            date_str,
            source="vipdoc",
            source_version="vcp.polars_engine.save_cache_parquet:v3",
        )
        if not status.ok:
            _log.error(f"[warehouse] snapshot publish failed: {status.data_status} {status.error}")
            return False
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        _log.error(f"[warehouse] snapshot publish failed: {exc}")
        return False
    del pl_df
    _gc.collect()

    elapsed = time.time() - t0
    file_mb = os.path.getsize(status.parquet_path) / 1024 / 1024
    _log.info(f"[加速引擎] Parquet 缓存已保存(纯Polars): {len(cache_data)} 只 | {file_mb:.1f}MB | 耗时 {elapsed:.2f}s")
    return True


def load_cache_parquet() -> tuple[dict, str] | None:
    """从 Parquet 加载全市场数据（纯Polars实现）

    返回: (cache_data dict[str, pl.DataFrame], date_str) 或 None
    """
    parquet_path = os.path.join(_PARQUET_CACHE_DIR, "market_data.parquet")
    meta_path = os.path.join(_PARQUET_CACHE_DIR, "meta.parquet")

    if not os.path.exists(parquet_path):
        return None

    try:
        import polars as pl
    except ImportError:
        return None

    t0 = time.time()
    try:
        # 读取元信息
        date_str = ""
        with _PARQUET_CACHE_LOCK:
            if os.path.exists(meta_path):
                meta = pl.read_parquet(meta_path)
                date_str = str(meta["date"][0])
                version = int(meta["version"][0])
                if version != 3:
                    _log.warning(f"[加速引擎] Parquet 缓存版本不匹配 (期望 3, 实际 {version})")
                    return None

            # 读取数据 — 用 Polars partition_by 高效分组
            pl_df = pl.read_parquet(parquet_path)

        cache_data = {}
        if "_code" in pl_df.columns:
            # Polars partition_by: C 级分组，避免 Pandas 的 Python dict 桥接瓶颈
            for part in pl_df.partition_by("_code", maintain_order=True):
                code = str(part["_code"][0])
                part_no_code = part.drop("_code")
                cache_data[code] = part_no_code

        elapsed = time.time() - t0
        _log.info(f"[加速引擎] Parquet 缓存加载完成(纯Polars): {len(cache_data)} 只 | 耗时 {elapsed:.2f}s")
        return cache_data, date_str

    except _POLARS_RUNTIME_ERRORS as e:
        _log.error(f"[加速引擎] Parquet 缓存加载失败: {e}")
        return None


# ================================================================
# 板块 RPS 加速 — 纯 Polars join/groupby
# ================================================================


def _sector_member_aliases(member, sector_name: str) -> set[tuple[str, str]]:
    member_text = str(member)
    bare = member_text.replace("sh", "").replace("sz", "")
    aliases = {(member_text, sector_name), (bare, sector_name)}
    if bare == member_text:
        prefix = "sh" if member_text.startswith(("6", "9")) else "sz"
        aliases.add((f"{prefix}{member_text}", sector_name))
    return aliases


def build_sector_rps_pl(
    sector_to_codes: dict[str, list[str]],
    all_data: dict,
    target_date,
    periods: list[int] | None = None,
) -> dict:
    """Polars 版板块 RPS 计算 — 完全去除 pandas 依赖"""
    if periods is None:
        periods = [5, 10, 15, 20, 50]

    # 统一目标日期为 datetime.date
    if isinstance(target_date, str):
        target_dt = datetime.strptime(target_date.replace("-", ""), "%Y%m%d").date()
    elif hasattr(target_date, "date") and callable(getattr(target_date, "date")):
        target_dt = target_date.date()
    else:
        target_dt = target_date

    max_lookback = max(periods) + 5
    t0 = time.time()

    records = []
    for code, df in all_data.items():
        if df is None or len(df) < max_lookback:
            continue
        try:
            pldf = _to_pldf(df)
            if pldf is None or pldf.height < max_lookback:
                continue
            if "close" not in pldf.columns or "datetime" not in pldf.columns:
                continue

            # 找到 target_date 对应的位置
            dates_col = _as_date(pldf["datetime"], pldf.schema.get("datetime"))
            if dates_col.is_sorted():
                loc = int(dates_col.search_sorted(target_dt, side="right")) - 1
            else:
                valid_indices = (dates_col <= target_dt).arg_true()
                loc = int(valid_indices[-1]) if valid_indices.len() else -1
            if loc < 0:
                continue

            close_col = pldf["close"]
            curr_close = float(close_col[loc])
            if curr_close <= 0:
                continue

            for p in periods:
                prev_loc = loc - p
                if prev_loc < 0:
                    continue
                prev_close = float(close_col[prev_loc])
                if prev_close > 0:
                    ret = (curr_close - prev_close) / prev_close
                    records.append((code, p, ret))
        except _POLARS_DATA_ERRORS as _e:
            _log.debug(f"[加速引擎] 板块RPS计算跳过一只: {_e}")
            continue

    if not records:
        return {}

    ret_df = pl.DataFrame(
        {
            "code": [r[0] for r in records],
            "period": [r[1] for r in records],
            "ret": [r[2] for r in records],
        }
    )

    code_sector_records = set()
    for sector_name, members in sector_to_codes.items():
        for member in members:
            code_sector_records.update(_sector_member_aliases(member, sector_name))

    if not code_sector_records:
        return {}

    cs_df = pl.DataFrame(
        {
            "code": [r[0] for r in code_sector_records],
            "sector": [r[1] for r in code_sector_records],
        }
    )

    joined = ret_df.join(cs_df, on="code", how="inner")

    sector_avg = (
        joined.group_by(["sector", "period"])
        .agg(
            [
                pl.col("ret").median().alias("median_ret"),
                pl.col("ret").count().alias("cnt"),
            ]
        )
        .filter(pl.col("cnt") >= 3)
    )

    sector_rps_df = sector_avg.with_columns(
        (pl.col("median_ret").rank("ordinal").over("period") / pl.col("median_ret").count().over("period") * 100)
        .round(1)
        .alias("rps")
    )

    from collections import defaultdict

    result = defaultdict(dict)
    for row in sector_rps_df.iter_rows(named=True):
        result[row["sector"]][row["period"]] = row["rps"]

    elapsed = time.time() - t0
    _log.info(f"[加速引擎] 板块 RPS 计算完成(纯Polars): {len(result)} 个板块 (耗时 {elapsed:.2f}s)")
    return dict(result)
