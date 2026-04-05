# polars_engine.py - 高性能加速引擎 v2
# 三大优化: ①numpy 并行 pct_change+rank ②Parquet 缓存 ③增量 RPS

import os
import time
import numpy as np
import pandas as pd

from vcp.constants import DATE_FMT, RPS_BUFFER_DAYS, CACHE_DIR

from core.logger import get_logger
_log = get_logger(__name__)


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
    n_rows, n_cols = matrix.shape
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
            ranks = rankdata(valid_vals, method='ordinal')
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
# 优化1: 价格矩阵快速构建
# ================================================================

def build_prices_matrix_fast(
    data_dict: dict[str, pd.DataFrame],
    min_start: pd.Timestamp,
    end_ts: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """高性能价格矩阵构建 — pd.DataFrame(dict) 一次性构建"""
    t0 = time.time()

    series_dict = {}
    for code, df in data_dict.items():
        if df is None or df.empty:
            continue
        try:
            mask = df.index >= min_start
            if end_ts is not None:
                mask = mask & (df.index <= end_ts)
            sliced = df.loc[mask, 'close']
            if not sliced.empty:
                series_dict[code] = sliced
        except Exception:
            continue

    if not series_dict:
        return pd.DataFrame()

    prices = pd.DataFrame(series_dict)
    prices = prices.sort_index()
    prices = prices.ffill(limit=5)

    elapsed = time.time() - t0
    _log.info(f"[加速引擎] 价格矩阵构建完成: {prices.shape[1]} 只 × {prices.shape[0]} 日 (耗时 {elapsed:.2f}s)")
    return prices


# ================================================================
# 优化1+3: RPS 矩阵 — numpy 向量化 + 增量缓存
# ================================================================

# 增量 RPS 价格矩阵磁盘缓存路径
_PRICES_MATRIX_CACHE = os.path.join(CACHE_DIR, 'vcp_prices_matrix.parquet')


def _save_prices_matrix(prices: pd.DataFrame) -> None:
    """将价格矩阵保存为 Parquet（增量 RPS 基底）"""
    try:
        import polars as pl
        pl_df = pl.from_pandas(prices.reset_index())
        pl_df.write_parquet(_PRICES_MATRIX_CACHE, compression='zstd')
    except Exception:
        # fallback: 直接用 pandas
        try:
            prices.to_parquet(_PRICES_MATRIX_CACHE, engine='pyarrow', compression='zstd')
        except Exception as e:
            _log.error(f"[加速引擎] 价格矩阵缓存保存失败: {e}")
def _load_prices_matrix() -> pd.DataFrame | None:
    """从 Parquet 加载历史价格矩阵"""
    if not os.path.exists(_PRICES_MATRIX_CACHE):
        return None
    try:
        import polars as pl
        pl_df = pl.read_parquet(_PRICES_MATRIX_CACHE)
        pdf = pl_df.to_pandas()
        # 恢复索引: reset_index() 产生的列名为 'index' 或原始 index.name
        if 'index' in pdf.columns:
            pdf.set_index('index', inplace=True)
            pdf.index = pd.DatetimeIndex(pdf.index)
            pdf.index.name = None
        elif 'datetime' in pdf.columns:
            pdf.set_index('datetime', inplace=True)
            pdf.index = pd.DatetimeIndex(pdf.index)
        return pdf
    except Exception:
        try:
            pdf = pd.read_parquet(_PRICES_MATRIX_CACHE, engine='pyarrow')
            return pdf
        except Exception as e:
            _log.error(f"[加速引擎] 价格矩阵缓存加载失败: {e}")
            return None


def build_rps_matrix_pl(
    data_dict: dict[str, pd.DataFrame],
    start_date: str,
    end_date: str,
    rps_cache: dict | None = None,
) -> dict:
    """高性能 RPS 矩阵计算 v2

    三重优化:
    1. 价格矩阵: dict 构建 + 增量复用（Parquet 缓存历史矩阵）
    2. pct_change: numpy 向量化（C 级除法替代 pandas 逐列）
    3. rank: numpy argsort（避免 pandas Python 对象层开销）
    """
    num_stocks = len(data_dict)

    # 缓存检查
    if rps_cache is not None:
        cache_key = (str(start_date), str(end_date))
        if cache_key in rps_cache:
            _log.warning(f"\n[加速引擎] RPS 矩阵命中缓存 (区间 {start_date} ~ {end_date})，跳过重算")
            return rps_cache[cache_key]

    _log.info(f"\n[加速引擎] 正在计算全市场 RPS 强度矩阵... (标的数: {num_stocks})")
    t_total = time.time()

    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date)
    min_start = start_ts - pd.Timedelta(days=RPS_BUFFER_DAYS)

    # ---- 增量复用: 尝试加载历史矩阵 ----
    prices = None
    cached_matrix = _load_prices_matrix()
    if cached_matrix is not None and not cached_matrix.empty:
        existing_end = cached_matrix.index.max()
        if existing_end >= end_ts:
            # 完全覆盖，直接复用
            prices = cached_matrix.loc[min_start:end_ts]
            _log.info(f"[加速引擎] 增量复用: 价格矩阵全命中 (已有到 {existing_end.strftime('%Y%m%d')})")
        elif existing_end >= min_start:
            # 部分覆盖，追加新日期的数据
            t_inc = time.time()
            new_min = existing_end + pd.Timedelta(days=1)
            new_series = {}
            for code, df in data_dict.items():
                if df is None or df.empty:
                    continue
                try:
                    new_data = df.loc[new_min:end_ts, 'close']
                    if not new_data.empty:
                        new_series[code] = new_data
                except Exception:
                    continue

            if new_series:
                new_chunk = pd.DataFrame(new_series).sort_index()
                # 合并：保留所有列（包括新上市股票）
                prices = pd.concat([cached_matrix, new_chunk], axis=0)
                prices = prices[~prices.index.duplicated(keep='last')]
                prices = prices.sort_index()
                prices = prices.ffill(limit=5)
                prices = prices.loc[min_start:end_ts]
                _log.info(f"[加速引擎] 增量追加: +{len(new_chunk)} 日新数据 (耗时 {time.time()-t_inc:.2f}s)")
            else:
                prices = cached_matrix.loc[min_start:end_ts]

    # 如果增量失败，全量构建
    if prices is None or prices.empty:
        prices = build_prices_matrix_fast(data_dict, min_start, end_ts)

    if prices.empty:
        _log.warning(f"[加速引擎] ⚠ 无可用价格数据")
        return {}

    # 保存矩阵缓存供下次增量复用
    _save_prices_matrix(prices)

    # ---- numpy 向量化 pct_change + rank ----
    t1 = time.time()
    matrix = prices.values  # (n_dates, n_stocks) numpy array

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

    _log.info(f"[加速引擎] numpy pct_change + rank 完成 (耗时 {time.time()-t1:.2f}s)")
    # ---- 组装返回字典 ----
    columns = prices.columns.tolist()
    date_index = prices.index
    target_mask = (date_index >= start_ts) & (date_index <= end_ts)
    target_indices = np.where(target_mask)[0]

    result = {}
    for idx in target_indices:
        d = date_index[idx]
        r50 = rps50_arr[idx]
        r120 = rps120_arr[idx]
        r250 = rps250_arr[idx]
        valid = ~np.isnan(r120) & ~np.isnan(r250)
        d_str = d.strftime(DATE_FMT)
        result[d_str] = {
            'rps50':  {columns[j]: float(r50[j]) for j in range(len(columns)) if valid[j]},
            'rps120': {columns[j]: float(r120[j]) for j in range(len(columns)) if valid[j]},
            'rps250': {columns[j]: float(r250[j]) for j in range(len(columns)) if valid[j]},
        }

    elapsed_total = time.time() - t_total
    _log.info(f"[加速引擎] RPS 矩阵构建完成 — 参与标的 {prices.shape[1]} 只 | "
          f"扫描交易日 {len(target_indices)} 个 | 总耗时 {elapsed_total:.2f}s")

    if rps_cache is not None:
        cache_key = (str(start_date), str(end_date))
        rps_cache[cache_key] = result

    return result


# ================================================================
# 优化2: Parquet 缓存替代 pickle
# ================================================================

_PARQUET_CACHE_DIR = os.path.join(CACHE_DIR, 'parquet')


def save_cache_parquet(cache_data: dict[str, pd.DataFrame], date_str: str) -> bool:
    """将全市场数据保存为 Parquet 格式（替代 pickle）

    策略: 将每只股票的 DataFrame 合并为一个大表
    columns: date, code, open, high, low, close, volume, amount, ...
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
        if df is None or df.empty:
            continue
        try:
            # 优化：reset_index 已生成副本，无需额外 copy
            temp = df.reset_index()
            idx_col = temp.columns[0]
            if idx_col != 'datetime':
                temp = temp.rename(columns={idx_col: 'datetime'})
            temp['_code'] = code
            frames.append(temp)
        except Exception:
            continue

    if not frames:
        return False

    parquet_path = os.path.join(_PARQUET_CACHE_DIR, 'market_data.parquet')
    meta_path = os.path.join(_PARQUET_CACHE_DIR, 'meta.parquet')

    # 分批 concat：避免一次性合并 5000 个 DataFrame 导致内存峰值和 GIL 长期占用
    import time as _time
    _BATCH_SIZE = 500
    batch_results = []
    for batch_start in range(0, len(frames), _BATCH_SIZE):
        batch = frames[batch_start:batch_start + _BATCH_SIZE]
        batch_results.append(pd.concat(batch, ignore_index=True))
        _time.sleep(0)  # 释放 GIL，让 UI 线程有机会响应
    del frames
    combined = pd.concat(batch_results, ignore_index=True)
    del batch_results
    _gc.collect()

    pl_df = pl.from_pandas(combined)
    del combined  # 立即释放 combined
    _gc.collect()

    pl_df.write_parquet(parquet_path, compression='zstd')
    del pl_df
    _gc.collect()

    # 保存元信息
    meta = pl.DataFrame({
        'date': [date_str],
        'n_stocks': [len(cache_data)],
        'version': [3],
    })
    meta.write_parquet(meta_path, compression='zstd')

    elapsed = time.time() - t0
    file_mb = os.path.getsize(parquet_path) / 1024 / 1024
    _log.info(f"[加速引擎] Parquet 缓存已保存: {len(cache_data)} 只 | {file_mb:.1f}MB | 耗时 {elapsed:.2f}s")
    return True


def load_cache_parquet() -> tuple[dict[str, pd.DataFrame], str] | None:
    """从 Parquet 加载全市场数据（替代 pickle）

    返回: (cache_data dict, date_str) 或 None
    """
    parquet_path = os.path.join(_PARQUET_CACHE_DIR, 'market_data.parquet')
    meta_path = os.path.join(_PARQUET_CACHE_DIR, 'meta.parquet')

    if not os.path.exists(parquet_path):
        return None

    try:
        import polars as pl
    except ImportError:
        return None

    t0 = time.time()
    try:
        # 读取元信息
        date_str = ''
        if os.path.exists(meta_path):
            meta = pl.read_parquet(meta_path)
            date_str = str(meta['date'][0])
            version = int(meta['version'][0])
            if version != 3:
                _log.warning(f"[加速引擎] Parquet 缓存版本不匹配 (期望 3, 实际 {version})")
                return None

        # 读取数据 — 用 Polars partition_by 高效分组
        pl_df = pl.read_parquet(parquet_path)

        cache_data = {}
        if '_code' in pl_df.columns:
            # Polars partition_by: C 级分组，比 pandas groupby 快 5x+
            for part in pl_df.partition_by('_code', maintain_order=True):
                code = str(part['_code'][0])
                part_no_code = part.drop('_code')
                pdf = part_no_code.to_pandas()
                if 'datetime' in pdf.columns:
                    pdf.set_index('datetime', inplace=True)
                    pdf.index = pd.DatetimeIndex(pdf.index)
                cache_data[code] = pdf

        elapsed = time.time() - t0
        _log.info(f"[加速引擎] Parquet 缓存加载完成: {len(cache_data)} 只 | 耗时 {elapsed:.2f}s")
        return cache_data, date_str

    except Exception as e:
        _log.error(f"[加速引擎] Parquet 缓存加载失败: {e}")
        return None


# ================================================================
# 技术指标批量预算 — 多线程 pandas
# ================================================================

def calculate_indicators_batch_pl(cache_data: dict[str, pd.DataFrame]) -> None:
    """多线程 pandas 指标预算"""
    from vcp.engine import VCPEngine
    import concurrent.futures

    t0 = time.time()

    def _calc_one(item):
        _code, _df = item
        if _df is None or len(_df) < 10:
            return 'skip'
        if hasattr(_df, 'attrs') and (_df.attrs.get('vcp_indicators_ready', False) or _df.attrs.get('vcp_core_ready', False)):
            return 'skip'
        try:
            # 批量预算仅计算核心指标，跳过 MACD/RSI/BB 节省约 400MB 内存
            VCPEngine.calculate_indicators(_df, include_chart=False)
            return 'ok'
        except Exception:
            return 'fail'

    items = list(cache_data.items())
    max_workers = min(8, (os.cpu_count() or 4))

    results = {'ok': 0, 'skip': 0, 'fail': 0}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for status in ex.map(_calc_one, items):
            results[status] = results.get(status, 0) + 1

    elapsed = time.time() - t0
    _log.info(f"[加速引擎] 批量指标预算完成: {results['ok']} 只计算 | "
          f"{results['skip']} 只跳过 | {results['fail']} 只失败 | 耗时 {elapsed:.2f}s")


# ================================================================
# 板块 RPS 加速 — Polars join/groupby
# ================================================================

def build_sector_rps_pl(
    sector_to_codes: dict[str, list[str]],
    all_data: dict[str, pd.DataFrame],
    target_date,
    periods: list[int] | None = None,
) -> dict:
    """Polars 版板块 RPS 计算"""
    try:
        import polars as pl
    except ImportError:
        return {}

    if periods is None:
        periods = [5, 10, 15, 20, 50]

    target_dt = pd.to_datetime(target_date)
    max_lookback = max(periods) + 5
    t0 = time.time()

    records = []
    for code, df in all_data.items():
        if df is None or len(df) < max_lookback:
            continue
        try:
            if target_dt in df.index:
                loc = df.index.get_loc(target_dt)
            else:
                valid = df.index[df.index <= target_dt]
                if len(valid) == 0:
                    continue
                loc = df.index.get_loc(valid[-1])

            if isinstance(loc, slice):
                loc = loc.stop - 1 if loc.stop else loc.start
            elif isinstance(loc, np.ndarray):
                loc = int(loc[-1])
            else:
                loc = int(loc)

            curr_close = float(df.iloc[loc]['close'])
            if curr_close <= 0:
                continue

            for p in periods:
                prev_loc = loc - p
                if prev_loc < 0:
                    continue
                prev_close = float(df.iloc[prev_loc]['close'])
                if prev_close > 0:
                    ret = (curr_close - prev_close) / prev_close
                    records.append((code, p, ret))
                    bare = code.replace('sh', '').replace('sz', '')
                    if bare == code:
                        prefix = 'sh' if code.startswith(('6', '9')) else 'sz'
                        records.append((f"{prefix}{code}", p, ret))
                    else:
                        records.append((bare, p, ret))
        except Exception:
            continue

    if not records:
        return {}

    ret_df = pl.DataFrame({
        'code': [r[0] for r in records],
        'period': [r[1] for r in records],
        'ret': [r[2] for r in records],
    })

    code_sector_records = []
    for sector_name, members in sector_to_codes.items():
        for member in members:
            code_sector_records.append((member, sector_name))

    if not code_sector_records:
        return {}

    cs_df = pl.DataFrame({
        'code': [r[0] for r in code_sector_records],
        'sector': [r[1] for r in code_sector_records],
    })

    joined = ret_df.join(cs_df, on='code', how='inner')

    sector_avg = (
        joined
        .group_by(['sector', 'period'])
        .agg([
            pl.col('ret').median().alias('median_ret'),
            pl.col('ret').count().alias('cnt'),
        ])
        .filter(pl.col('cnt') >= 3)
    )

    sector_rps_df = sector_avg.with_columns(
        (pl.col('median_ret').rank('ordinal').over('period') /
         pl.col('median_ret').count().over('period') * 100)
        .round(1)
        .alias('rps')
    )

    from collections import defaultdict
    result = defaultdict(dict)
    for row in sector_rps_df.iter_rows(named=True):
        result[row['sector']][row['period']] = row['rps']

    elapsed = time.time() - t0
    _log.info(f"[加速引擎] 板块 RPS 计算完成: {len(result)} 个板块 (耗时 {elapsed:.2f}s)")
    return dict(result)
