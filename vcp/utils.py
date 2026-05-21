# utils.py - 辅助工具函数
# 从 vcp_hunter.pyw 提取，零逻辑变更
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

from core.logger import get_logger
from vcp.constants import PROJECT_ROOT

_log = get_logger(__name__)


# ==========================================
# 通达信本地路径配置
# ==========================================
def _check_vipdoc_valid(vipdoc):
    """检查 vipdoc 目录是否有效（含 sh/sz 子目录）"""
    return (
        os.path.isdir(vipdoc)
        and os.path.isdir(os.path.join(vipdoc, "sh"))
        and os.path.isdir(os.path.join(vipdoc, "sz"))
    )


def _load_tdx_local_config():
    """读取通达信本地路径配置。"""
    candidates = [
        os.path.join("D:\\", "vcp_qt", "vcp_tdx_config.json"),
        os.path.join("D:\\", "HT", "vcp_tdx_config.json"),
        os.path.join(PROJECT_ROOT, "vcp_tdx_config.json"),
    ]
    for cfg_path in candidates:
        try:
            if cfg_path and os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                root = (cfg.get("tdx_vipdoc_root") or "").strip().rstrip(os.sep)
                if not root:
                    continue
                if os.path.basename(root).lower() == "vipdoc":
                    vipdoc = root
                else:
                    vipdoc = os.path.join(root, "vipdoc")
                if _check_vipdoc_valid(vipdoc):
                    return vipdoc
        except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError, json.JSONDecodeError) as _e:
            _log.debug(f"[配置] 通达信配置文件 {cfg_path} 读取异常: {_e}")
            continue
    default_ht = os.path.join("D:\\", "HT", "vipdoc")
    if _check_vipdoc_valid(default_ht):
        return default_ht
    return None


# ==========================================
# 通达信 .day 文件读取
# ==========================================
def read_tdx_day_file(filepath, price_div=100.0):
    """
    读取通达信 .day 日线文件，返回与 pytdx 兼容的 Pandas DataFrame。
    每 32 字节一条：日期(4)、开(4)、高(4)、低(4)、收(4)、成交额float(4)、成交量(4)、保留(4)。

    为什么直接返回 Pandas 而不返回 Polars？
    因为 sync_market_data 用 20 线程并发调此函数，Polars 的 .to_pandas()
    底层走 PyArrow C++ 桥接，多线程并发调用时会在无 GIL 状态下触发竞态段错误。
    直接在此函数内（单线程上下文）完成转换，彻底消除下游的并发 .to_pandas() 调用。
    """
    if not os.path.isfile(filepath):
        return None
    try:
        with open(filepath, "rb") as f:
            buf = f.read()
    except (FileNotFoundError, PermissionError, OSError) as e:
        _log.error(f"[Error] read_tdx_day_file: {str(e)}")
        return None
    n = len(buf) // 32
    if n == 0:
        return None
    try:
        dtype = np.dtype(
            [
                ("date", "<u4"),
                ("o", "<u4"),
                ("h", "<u4"),
                ("low", "<u4"),
                ("c", "<u4"),
                ("amount", "<f4"),
                ("vol", "<u4"),
                ("res", "<u4"),
            ]
        )
        raw = np.frombuffer(buf[: n * 32], dtype=dtype)
        dates = pd.to_datetime(raw["date"].astype(str), format="%Y%m%d", errors="coerce")
        valid = dates.notna()
        if not valid.any():
            return None
        dates = dates[valid]
        o = (raw["o"][valid].astype(np.float64) / price_div).round(2)
        h = (raw["h"][valid].astype(np.float64) / price_div).round(2)
        low = (raw["low"][valid].astype(np.float64) / price_div).round(2)
        c = (raw["c"][valid].astype(np.float64) / price_div).round(2)
        amount = raw["amount"][valid].astype(np.float64)
        vol = raw["vol"][valid].astype(np.int64)

        # 直接构建 Pandas DataFrame 并排序，不经过 Polars→PyArrow 桥接
        pdf = pd.DataFrame(
            {
                "datetime": dates.values,
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "amount": amount,
                "volume": vol,
            }
        )
        pdf = pdf.sort_values("datetime").reset_index(drop=True)
        return pdf
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
        _log.error(f"[Error] read_tdx_day_file: {str(e)}")
        return None


def ensure_pandas_dataframe(df, *, datetime_col: str = "datetime", set_datetime_index: bool = True):
    """将常见 DataFrame 实现尽量归一化为 pandas.DataFrame。"""
    if df is None:
        return None
    if isinstance(df, pd.DataFrame):
        pdf = df
    else:
        pdf = None
        if hasattr(df, "to_pandas"):
            try:
                pdf = df.to_pandas()
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pdf = None
        if pdf is None and hasattr(df, "to_dicts"):
            try:
                pdf = pd.DataFrame(df.to_dicts())
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pdf = None
        if pdf is None and hasattr(df, "to_dict"):
            try:
                pdf = pd.DataFrame(df.to_dict(orient="records"))
            except TypeError:
                try:
                    pdf = pd.DataFrame(df.to_dict())
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    pdf = None
            except (AttributeError, RuntimeError, ValueError):
                pdf = None
        if pdf is None:
            return df

    if datetime_col in getattr(pdf, "columns", []) and not isinstance(pdf.index, pd.DatetimeIndex):
        try:
            normalized = pdf.copy()
            normalized[datetime_col] = pd.to_datetime(normalized[datetime_col], errors="coerce")
            normalized = normalized.dropna(subset=[datetime_col])
            if set_datetime_index:
                normalized = normalized.set_index(datetime_col)
            pdf = normalized
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
            pass
    return pdf


# ==========================================
# 交易时间判断
# ==========================================
def is_trading_day(date=None):
    """简易判断是否为交易日（仅排除周末，不排除节假日）。
    注意：主窗口 VCPDesktopTerminal._is_trading_day() 有增强版（含节假日），
    核心业务逻辑应使用主窗口版本，本函数仅用于启动阶段等辅助场景。
    """
    d = date if date else datetime.now()
    return d.weekday() < 5
