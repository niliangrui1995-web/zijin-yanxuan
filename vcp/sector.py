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
            tdx_root = os.path.dirname(vipdoc) if vipdoc else r"D:\HT"
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
            tdx_root = os.path.dirname(vipdoc) if vipdoc else r"D:\HT"
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
        # ---- numpy 原始路径（fallback）----
        if isinstance(target_date, str):
            target_dt = _datetime.strptime(target_date, "%Y%m%d").date()
        elif hasattr(target_date, "date") and callable(getattr(target_date, "date")):
            target_dt = target_date.date()
        else:
            target_dt = target_date
        max_lookback = max(periods) + 5  # 多留几天余量

        # 1. 计算每只股票在各周期的涨幅
        stock_returns = {}  # {代码: {周期: 涨幅}}
        for code, df in all_data.items():
            if df is None or len(df) < max_lookback:
                continue
            try:
                import polars as pl

                # 兼容 Polars 和 Pandas 输入
                if isinstance(df, pl.DataFrame):
                    if "close" not in df.columns or "datetime" not in df.columns:
                        continue
                    dates_col = df["datetime"].cast(pl.Date)
                    mask_list = (dates_col <= pl.lit(target_dt)).to_list()
                    valid_indices = [i for i, v in enumerate(mask_list) if v]
                    if not valid_indices:
                        continue
                    loc = valid_indices[-1]
                    curr_close = float(df["close"][loc])
                else:
                    # Pandas fallback（向下兼容旧缓存等极端情况）
                    import pandas as pd

                    _target_ts = pd.to_datetime(target_date)
                    if _target_ts in df.index:
                        loc = df.index.get_loc(_target_ts)
                    else:
                        valid = df.index[df.index <= _target_ts]
                        if len(valid) == 0:
                            continue
                        loc = df.index.get_loc(valid[-1])
                    if isinstance(loc, slice):
                        loc = loc.stop - 1 if loc.stop else loc.start
                    elif isinstance(loc, np.ndarray):
                        loc = int(loc[-1])
                    else:
                        loc = int(loc)
                    curr_close = float(df.iloc[loc]["close"])
            except (AttributeError, IndexError, KeyError, RuntimeError, TypeError, ValueError) as _e:
                _log.debug(f"[板块管理] 计算 {code} 涨幅时异常: {_e}")
                continue

            if curr_close <= 0:
                continue

            ret = {}
            for p in periods:
                prev_loc = loc - p
                if prev_loc < 0:
                    continue
                if isinstance(df, pl.DataFrame):
                    prev_close = float(df["close"][prev_loc])
                else:
                    prev_close = float(df.iloc[prev_loc]["close"])
                if prev_close > 0:
                    ret[p] = (curr_close - prev_close) / prev_close
            if ret:
                stock_returns[code] = ret
                # 兼容格式：all_data key 可能是纯数字(600000)，
                # 而 sector_to_codes 成员是 sh600000/sz000001
                # 同时存两种格式确保匹配
                bare = code.replace("sh", "").replace("sz", "")
                if bare == code:
                    # code 是纯数字，补上 sh/sz 前缀
                    prefix = "sh" if code.startswith(("6", "9")) else "sz"
                    stock_returns[f"{prefix}{code}"] = ret
                else:
                    # code 已有前缀，补上纯数字版
                    stock_returns[bare] = ret

        # 2. 计算每个板块的平均涨幅
        sector_returns = {}  # {板块名: {周期: 平均涨幅}}
        for sector_name, members in self.sector_to_codes.items():
            period_sums = defaultdict(list)
            for code in members:
                if code in stock_returns:
                    for p, r in stock_returns[code].items():
                        period_sums[p].append(r)
            if not period_sums:
                continue
            avg_ret = {}
            for p in periods:
                vals = period_sums.get(p, [])
                if len(vals) >= 3:  # 至少3只成分股有数据才计算
                    avg_ret[p] = float(np.median(vals))  # 用中位数更稳健
            if avg_ret:
                sector_returns[sector_name] = avg_ret

        # 3. 对每个周期，按涨幅排名得到 RPS (0~100)
        sector_rps = defaultdict(dict)
        for p in periods:
            # 收集所有板块在该周期的涨幅
            items = [(name, ret.get(p)) for name, ret in sector_returns.items() if p in ret]
            if not items:
                continue
            items.sort(key=lambda x: x[1])
            n = len(items)
            for rank, (name, _) in enumerate(items):
                # RPS = 排名百分位 * 100
                rps_val = round((rank + 1) / n * 100, 1)
                sector_rps[name][p] = rps_val

        return dict(sector_rps)

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
