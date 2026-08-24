# -*- coding: utf-8 -*-
"""板块数据管理与板块 RPS 计算模块

解析通达信本地文件：
  - tdxhy.cfg       → 行业分类（股票→行业映射）
  - infoharbor_block.dat → 概念板块（板块→成分股映射）

计算板块 RPS：
  对每个板块，取成分股收盘价均值得到"板块指数"，
  再按 5 / 10 / 15 / 20 / 50 日涨幅对所有板块排名，得到板块 RPS (0~100)。
"""

import os
import re
from collections import defaultdict
from datetime import datetime as _datetime

import numpy as np

from core.logger import get_logger
from core.runtime_paths import DEFAULT_TDX_ROOT

_log = get_logger(__name__)


class SectorManager:
    """板块管理器：解析板块映射，计算板块 RPS"""

    # 板块 RPS 计算的周期列表
    RPS_PERIODS = [5, 10, 15, 20, 50]

    _instance = None
    _instance_root = None

    @classmethod
    def get_instance(cls, tdx_root: str | None = None) -> "SectorManager":
        """获取单例实例（首次调用时解析板块文件，后续直接返回缓存）"""
        if tdx_root is None:
            # 动态获取通达信安装路径，不再硬编码特定机器的路径
            from core.app_config import app_config

            vipdoc = app_config.get("scan/tdx_vipdoc", "")
            tdx_root = os.path.dirname(vipdoc) if vipdoc else DEFAULT_TDX_ROOT
        if cls._instance is None or cls._instance_root != tdx_root:
            cls._instance = cls(tdx_root)
            cls._instance_root = tdx_root
        return cls._instance

    def __init__(self, tdx_root: str | None = None):
        """初始化：解析通达信板块文件

        参数:
            tdx_root: 通达信安装根目录
        """
        if tdx_root is None:
            from core.app_config import app_config

            vipdoc = app_config.get("scan/tdx_vipdoc", "")
            tdx_root = os.path.dirname(vipdoc) if vipdoc else DEFAULT_TDX_ROOT
        self.tdx_root = tdx_root
        # 股票代码 → 所属板块名列表
        self.code_to_sectors = defaultdict(list)
        # 板块名 → 成分股代码列表
        self.sector_to_codes = defaultdict(list)
        # 板块名列表（去重后）
        self.all_sector_names = []

        # 解析文件
        self._parse_industry(os.path.join(tdx_root, "T0002", "hq_cache", "tdxhy.cfg"))
        self._parse_concepts(os.path.join(tdx_root, "T0002", "hq_cache", "infoharbor_block.dat"))

        # 汇总所有板块名
        self.all_sector_names = sorted(self.sector_to_codes.keys())
        _log.info(
            f"[板块管理] 加载完成: {len(self.all_sector_names)} 个板块 | "
            f"行业映射 {self._hy_count} 条 | 概念板块 {self._gn_count} 个"
        )

    # ---------- 解析通达信行业分类文件 ----------
    def _parse_industry(self, filepath):
        """解析 tdxhy.cfg → 行业映射

        文件格式: 市场|代码|行业代码|||证监会代码
        市场: 0=深圳, 1=上海
        """
        self._hy_count = 0
        if not os.path.exists(filepath):
            _log.warning(f"[板块管理] ⚠ 未找到行业文件: {filepath}")
            return

        # 读取 incon.dat 获取行业代码→行业名称映射
        incon_path = os.path.join(self.tdx_root, "incon.dat")
        hy_name_map = {}  # 行业代码 → 名称
        if os.path.exists(incon_path):
            try:
                with open(incon_path, "rb") as f:
                    raw = f.read()
                text = raw.decode("gbk", errors="ignore")
                # 解析 #TDXNHY 段（通达信行业分类）
                in_tdx_section = False
                for line in text.split("\n"):
                    line = line.strip()
                    if line.startswith("#TDXNHY"):
                        in_tdx_section = True
                        continue
                    if line.startswith("#") and in_tdx_section:
                        break  # 下一个段落开始
                    if in_tdx_section and "|" in line:
                        parts = line.split("|")
                        if len(parts) >= 2:
                            hy_name_map[parts[0]] = parts[1]
            except (OSError, TypeError, UnicodeDecodeError, ValueError) as e:
                _log.error(f"[板块管理] ⚠ 解析 incon.dat 失败: {e}")
        try:
            with open(filepath, "r", encoding="gbk", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("|")
                    if len(parts) < 3:
                        continue
                    market = parts[0]  # 0=深圳, 1=上海
                    code = parts[1]  # 股票代码（6位）
                    hy_code = parts[2]  # 行业代码（如 T0703）

                    # 统一股票代码格式（与 data_provider 一致）
                    if market == "1":
                        full_code = f"sh{code}"
                    else:
                        full_code = f"sz{code}"

                    # 查找行业名称
                    hy_name = hy_name_map.get(hy_code, hy_code)
                    sector_name = f"行业_{hy_name}"

                    self.code_to_sectors[full_code].append(sector_name)
                    self.sector_to_codes[sector_name].append(full_code)
                    self._hy_count += 1
        except (OSError, TypeError, UnicodeDecodeError, ValueError) as e:
            _log.error(f"[板块管理] ⚠ 解析行业文件失败: {e}")

    # ---------- 解析概念板块文件 ----------
    def _parse_concepts(self, filepath):
        """解析 infoharbor_block.dat → 概念板块映射

        文件格式（GBK编码）:
        #GN_板块名,成分股数,板块代码,...
        市场#代码,市场#代码,...
        """
        self._gn_count = 0
        if not os.path.exists(filepath):
            _log.warning(f"[板块管理] ⚠ 未找到概念板块文件: {filepath}，尝试 fallback")

        text = ""
        try:
            if os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    raw = f.read()
                text = raw.decode("gbk", errors="replace")
        except (OSError, TypeError, UnicodeDecodeError, ValueError) as e:
            _log.error(f"[板块管理] ⚠ 读取 {filepath} 失败: {e}，尝试 fallback")

        if not text:
            fallback_path = os.path.join(self.tdx_root, "T0002", "hq_cache", "block.dat")
            if os.path.exists(fallback_path):
                try:
                    with open(fallback_path, "rb") as f:
                        raw = f.read()
                    text = raw.decode("gbk", errors="replace")
                    _log.info(f"[板块管理] 成功读取备用文件: {fallback_path}")
                except (OSError, TypeError, UnicodeDecodeError, ValueError) as e:
                    _log.error(f"[板块管理] ⚠ 备用文件 {fallback_path} 读取失败: {e}")
            else:
                _log.warning(f"[板块管理] ⚠ 备用文件也不存在: {fallback_path}")

        if not text:
            return

        try:
            # 按 #GN_ 分割为各板块段落
            sections = re.split(r"(?=#GN_)", text)
            for section in sections:
                section = section.strip()
                if not section.startswith("#GN_"):
                    continue

                # 提取板块名（第一行 #GN_板块名,... ）
                first_line_end = section.find("\n")
                if first_line_end < 0:
                    first_line_end = len(section)
                header = section[:first_line_end].strip()
                # 从 header 中提取板块名
                # 格式: #GN_板块名,数量,代码,...
                header_parts = header.split(",")
                sector_name = header_parts[0].replace("#", "")  # 如 "GN_人工智能"

                # 提取成分股代码
                body = section[first_line_end:]
                # 格式: 市场#代码  (0=深圳, 1=上海)
                codes = re.findall(r"([01])#(\d{6})", body)
                for market, code in codes:
                    if market == "1":
                        full_code = f"sh{code}"
                    else:
                        full_code = f"sz{code}"
                    self.code_to_sectors[full_code].append(sector_name)
                    self.sector_to_codes[sector_name].append(full_code)

                self._gn_count += 1
        except (AttributeError, IndexError, OSError, TypeError, ValueError) as e:
            _log.error(f"[板块管理] ⚠ 解析概念板块文件失败: {e}")

    # ---------- 查询接口 ----------
    def get_sectors(self, code):
        """返回该股票所属的所有板块名（行业+概念）

        参数:
            code: 股票代码，如 'sz300476' 或 '300476'
        返回:
            板块名列表，如 ['行业_半导体', 'GN_芯片概念', 'GN_人工智能']
        """
        # 兼容无前缀格式
        if not code.startswith(("sh", "sz")):
            for prefix in ["sz", "sh"]:
                full = f"{prefix}{code}"
                if full in self.code_to_sectors:
                    # 使用 dict.fromkeys 去重并保持顺序
                    return list(dict.fromkeys(self.code_to_sectors[full]))
            return []
        return list(dict.fromkeys(self.code_to_sectors.get(code, [])))

    @staticmethod
    def _normalize_rps_target_date(target_date):
        if isinstance(target_date, str):
            return _datetime.strptime(target_date, "%Y%m%d").date()
        if hasattr(target_date, "date") and callable(getattr(target_date, "date")):
            return target_date.date()
        return target_date

    @staticmethod
    def _locate_polars_close(df, target_dt, pl):
        if "close" not in df.columns or "datetime" not in df.columns:
            return None

        dates_col = df["datetime"].cast(pl.Date)
        if dates_col.is_sorted():
            loc = int(dates_col.search_sorted(target_dt, side="right")) - 1
        else:
            valid_indices = (dates_col <= target_dt).arg_true()
            loc = int(valid_indices[-1]) if valid_indices.len() else -1
        if loc < 0:
            return None
        return loc, float(df["close"][loc])

    @staticmethod
    def _locate_pandas_close(df, target_dt, pd):
        if "close" not in df.columns:
            return None

        target_ts = pd.Timestamp(target_dt)
        if target_ts in df.index:
            loc = df.index.get_loc(target_ts)
        else:
            valid = df.index[df.index <= target_ts]
            if len(valid) == 0:
                return None
            loc = df.index.get_loc(valid[-1])

        if isinstance(loc, slice):
            loc = loc.stop - 1 if loc.stop else loc.start
        elif isinstance(loc, np.ndarray):
            loc = int(np.flatnonzero(loc)[-1]) if loc.dtype == bool else int(loc[-1])
        else:
            loc = int(loc)
        return loc, float(df.iloc[loc]["close"])

    @staticmethod
    def _period_returns(df, loc: int, curr_close: float, periods, *, is_polars: bool) -> dict[int, float]:
        if curr_close <= 0:
            return {}

        returns = {}
        close_values = df["close"]
        for period in periods:
            prev_loc = loc - period
            if prev_loc < 0:
                continue
            prev_close = float(close_values[prev_loc] if is_polars else close_values.iloc[prev_loc])
            if prev_close > 0:
                returns[period] = (curr_close - prev_close) / prev_close
        return returns

    @staticmethod
    def _store_code_return_aliases(stock_returns: dict, code: str, returns: dict[int, float]) -> None:
        stock_returns[code] = returns
        bare = code.replace("sh", "").replace("sz", "")
        if bare == code:
            prefix = "sh" if code.startswith(("6", "9")) else "sz"
            stock_returns[f"{prefix}{code}"] = returns
        else:
            stock_returns[bare] = returns

    def _compute_stock_returns(self, all_data, target_dt, periods) -> dict[str, dict[int, float]]:
        try:
            import polars as pl
        except ImportError:
            pl = None
        import pandas as pd

        max_lookback = max(periods) + 5
        stock_returns: dict[str, dict[int, float]] = {}
        for code, df in all_data.items():
            if df is None or len(df) < max_lookback:
                continue
            try:
                is_polars = pl is not None and isinstance(df, pl.DataFrame)
                located = (
                    self._locate_polars_close(df, target_dt, pl)
                    if is_polars
                    else self._locate_pandas_close(df, target_dt, pd)
                )
                if located is None:
                    continue
                loc, curr_close = located
                returns = self._period_returns(df, loc, curr_close, periods, is_polars=is_polars)
            except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError) as exc:
                _log.debug(f"[板块管理] 计算 {code} 涨幅时异常: {exc}")
                continue
            if returns:
                self._store_code_return_aliases(stock_returns, str(code), returns)
        return stock_returns

    def _aggregate_sector_medians(self, stock_returns, periods) -> dict[str, dict[int, float]]:
        sector_returns = {}
        for sector_name, members in self.sector_to_codes.items():
            period_values = defaultdict(list)
            for code in members:
                for period, value in stock_returns.get(code, {}).items():
                    period_values[period].append(value)

            medians = {
                period: float(np.median(period_values[period]))
                for period in periods
                if len(period_values.get(period, [])) >= 3
            }
            if medians:
                sector_returns[sector_name] = medians
        return sector_returns

    @staticmethod
    def _rank_sector_returns(sector_returns, periods) -> dict[str, dict[int, float]]:
        sector_rps = defaultdict(dict)
        for period in periods:
            items = sorted(
                ((name, returns[period]) for name, returns in sector_returns.items() if period in returns),
                key=lambda item: item[1],
            )
            total = len(items)
            for rank, (name, _) in enumerate(items, start=1):
                sector_rps[name][period] = round(rank / total * 100, 1)
        return dict(sector_rps)

    # ---------- 板块 RPS 计算 ----------
    def build_sector_rps(self, all_data, target_date, periods=None):
        """计算所有板块在给定日期的 RPS

        参数:
            all_data: {股票代码: DataFrame} 全部股票日线数据
            target_date: 目标日期字符串 'YYYYMMDD' 或 pd.Timestamp
            periods: RPS周期列表，默认 [5, 10, 15, 20, 50]

        返回:
            {板块名: {周期: rps值, ...}, ...}
        """
        if periods is None:
            periods = self.RPS_PERIODS

        # ---- Polars 快速路径 ----
        try:
            from vcp.polars_engine import build_sector_rps_pl

            result = build_sector_rps_pl(dict(self.sector_to_codes), all_data, target_date, periods)
            if result:
                return result
        except ImportError:
            pass  # polars 未安装
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
            _log.error(f"[板块管理] Polars 板块 RPS 计算失败，回退 numpy: {e}")

        target_dt = self._normalize_rps_target_date(target_date)
        stock_returns = self._compute_stock_returns(all_data, target_dt, periods)
        sector_returns = self._aggregate_sector_medians(stock_returns, periods)
        return self._rank_sector_returns(sector_returns, periods)

    # ---------- 板块 RPS 约束检查 ----------
    def check_sector_rps(self, code, sector_rps_dict, threshold=70):
        """检查该股票的板块 RPS 是否通过约束

        参数:
            code: 股票代码
            sector_rps_dict: build_sector_rps 的返回值
            threshold: RPS 阈值（默认70）

        返回:
            (通过: bool, 热点板块信息字符串, 最高RPS值)
            热点板块信息: 最高的前3个板块，格式如 "GN_芯片(50d=92) | 行业_半导体(20d=85)"
        """
        sectors = self.get_sectors(code)
        if not sectors:
            return False, "", 0

        # 收集该股票所有板块的所有周期 RPS
        hits = []  # [(板块名, 最高RPS, 对应周期)]
        for sector in sectors:
            rps_data = sector_rps_dict.get(sector, {})
            if not rps_data:
                continue
            # 找该板块最高的 RPS
            best_period = max(rps_data, key=rps_data.get)
            best_rps = rps_data[best_period]
            hits.append((sector, best_rps, best_period))

        if not hits:
            return False, "", 0

        # 按 RPS 从高到低排序
        hits.sort(key=lambda x: x[1], reverse=True)

        # 检查是否有任一板块 RPS >= 阈值
        passed = hits[0][1] >= threshold

        # 取前3个板块组装显示字符串
        top3 = hits[:3]
        info_parts = []
        for name, rps, period in top3:
            # 简化板块名（去掉前缀 "GN_" "行业_" 太长的截断）
            short_name = name.replace("GN_", "").replace("行业_", "")
            if len(short_name) > 6:
                short_name = short_name[:6]
            info_parts.append(f"{short_name}({period}d={rps:.0f})")

        info_str = " | ".join(info_parts)
        max_rps = hits[0][1]

        return passed, info_str, max_rps
