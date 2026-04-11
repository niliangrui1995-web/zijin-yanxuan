# -*- coding: utf-8 -*-
"""
core/lhb_pool_manager.py
龙虎榜 20 日滚动关注池 — 数据引擎

负责：
- 多日龙虎榜数据的持久化存储（JSON 缓存）
- 从 20 个交易日的全量记录中筛选符合条件的标的
- 淘汰超出 20 日窗口的历史数据
- 迁移旧的单日缓存（lhb_cache.json）到新池

筛选条件：20 个交易日内至少有一天同时满足：
  ① 上榜净买额 > 0
  ② 机构净买额 > 0
"""

import gc
import json
import os

from core.logger import get_logger

log = get_logger(__name__)

# 为什么用 20：用户定义的滚动窗口长度（约一个自然月的交易日）
POOL_WINDOW = 20


class LhbPoolManager:
    """龙虎榜关注池数据引擎 — 线程不安全，仅限主线程操作"""

    def __init__(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._cache_path = os.path.join(project_root, 'data', 'Cache', 'lhb_pool_20d.json')
        self._old_cache_path = os.path.join(project_root, 'data', 'Cache', 'lhb_cache.json')
        self._data: dict[str, list[dict]] = {}  # date_str(yyyyMMdd) -> [records]
        self._last_auto_fetch_date: str = ""
        self._load()
        self._migrate_old_cache()

    # ================================================================
    # 持久化
    # ================================================================
    def _load(self):
        if not os.path.exists(self._cache_path):
            return
        try:
            with open(self._cache_path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            self._data = raw.get("daily_data", {})
            self._last_auto_fetch_date = raw.get("last_auto_fetch_date", "")
            log.info(f"[龙虎榜池] 缓存加载成功，包含 {len(self._data)} 个交易日数据")
        except Exception as e:
            log.warning(f"[龙虎榜池] 缓存加载失败，将重建: {e}")
            self._data = {}

    def save(self):
        """落盘保存"""
        try:
            os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
            payload = {
                "version": 1,
                "last_auto_fetch_date": self._last_auto_fetch_date,
                "daily_data": self._data,
            }
            with open(self._cache_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception as e:
            log.error(f"[龙虎榜池] 缓存保存失败: {e}")

    def _migrate_old_cache(self):
        """把旧的单日 lhb_cache.json 数据迁移到新池中，然后删除旧文件"""
        if not os.path.exists(self._old_cache_path):
            return
        try:
            with open(self._old_cache_path, 'r', encoding='utf-8') as f:
                old = json.load(f)
            date_str = old.get("date_str", "")
            rows = old.get("rows", [])
            if date_str and rows and date_str not in self._data:
                # 旧缓存直接平移，不再做格式转换（资金共振字段已废弃）
                self._data[date_str] = rows
                self.save()
                log.info(f"[龙虎榜池] 成功迁移旧缓存 {date_str}，{len(rows)} 条记录")
            # 清理旧缓存文件
            os.remove(self._old_cache_path)
            log.info("[龙虎榜池] 旧缓存 lhb_cache.json 已删除")
        except Exception as e:
            log.warning(f"[龙虎榜池] 旧缓存迁移失败（无影响）: {e}")

    # ================================================================
    # 数据管理
    # ================================================================
    def add_day(self, date_str: str, records: list[dict]):
        """写入某一天的龙虎榜数据"""
        self._data[date_str] = records
        # 不在这里 save()，由调用方决定何时批量保存（减少 IO）

    def get_cached_dates(self) -> set[str]:
        return set(self._data.keys())

    def get_missing_dates(self, required_dates: list[str]) -> list[str]:
        """找出 required_dates 中还没有缓存的日期"""
        cached = self.get_cached_dates()
        return [d for d in required_dates if d not in cached]

    def prune(self, valid_dates: list[str]):
        """裁剪掉不在 valid_dates 窗口内的历史数据"""
        valid_set = set(valid_dates)
        to_remove = [d for d in self._data if d not in valid_set]
        if to_remove:
            for d in to_remove:
                del self._data[d]
            self.save()
            log.info(f"[龙虎榜池] 裁剪了 {len(to_remove)} 天过期数据: {sorted(to_remove)}")

    def clear_all(self):
        """清空全部缓存数据（手动全量刷新时使用）"""
        self._data.clear()
        self.save()

    @property
    def last_auto_fetch_date(self) -> str:
        return self._last_auto_fetch_date

    @last_auto_fetch_date.setter
    def last_auto_fetch_date(self, value: str):
        self._last_auto_fetch_date = value

    # ================================================================
    # 池计算
    # ================================================================

    @staticmethod
    def _is_bse_code(code: str) -> bool:
        """判断是否为北交所或B股（代码首位为 4/8/9，或前两位为 43/83/87）"""
        return code[:2] in ("43", "83", "87") or code[0] == "9"

    @staticmethod
    def _is_st_stock(name: str) -> bool:
        """判断是否为 ST 股票（名称含 ST，不区分大小写）"""
        return "ST" in name.upper()

    def compute_pool(self, data_provider=None, engine=None) -> list[dict]:
        """从缓存的多日数据中计算关注池。

        筛选逻辑：
        1. 遍历所有日期的所有记录
        2. 剔除 ST 股、北交所股票（纯本地字符串判断）
        3. 找出在任何一天同时满足 上榜净买额>0 AND 机构净买>0 的股票代码
        4. 如果有 data_provider，剔除 K 线行数 < 250 的次新股
        5. 对符合条件的股票，提取最近一次上榜的详细数据
        6. 附加"上榜次数"字段（满足条件的天数）

        参数:
            data_provider: 可选，传入可用的 DataProvider 实例，
                           用于通过 K 线缓存行数判断上市天数。
                           没有传入则跳过次新股过滤。

        返回：按 买点✅优先 → 最近上榜日降序 → 涨幅%降序 排列的列表
        """
        if not self._data:
            return []

        # 第一轮扫描：找出所有满足条件的代码 + 计数
        qualifying_codes: set[str] = set()
        code_hit_count: dict[str, int] = {}

        for date_str, records in self._data.items():
            for rec in records:
                code = rec.get("代码", "")
                name = rec.get("名称", "")
                if not code:
                    continue

                # 过滤①：剔除北交所（代码前缀 43/83/87）
                if self._is_bse_code(code):
                    continue

                # 过滤②：剔除 ST 股
                if self._is_st_stock(name):
                    continue

                net_buy = 0.0
                jg_net = 0.0
                wz_net = 0.0
                try:
                    net_buy = float(rec.get("上榜净买额(万)", 0))
                except (ValueError, TypeError):
                    pass
                try:
                    jg_net = float(rec.get("机构净买(万)", 0))
                except (ValueError, TypeError):
                    pass
                try:
                    wz_net = float(rec.get("外资净买(万)", 0))
                except (ValueError, TypeError):
                    pass

                # 过滤合集：
                # 1. 榜单总净买入必须为正
                # 2. 机构净买入必须为正
                # 3. 外资净买入必须 >= 0（即：如果有外资出没它必须是净买的；如果没有外资参与它等于 0 也是允许放行的；唯一剔除的是外资砸盘流出的股票）
                if net_buy > 0 and jg_net > 0 and wz_net >= 0:
                    qualifying_codes.add(code)
                    code_hit_count[code] = code_hit_count.get(code, 0) + 1

        if not qualifying_codes:
            return []

        # 过滤③ & ④合并：要求 RPS250 >= 85，无此数据（如次新股）将被一并剔除
        # 数据来源：VCPEngine 的 _precomputed_rps_bundle，每次 F5 后自动计算
        # 如果 F5 还没跑过（rps_bundle 为空或系统刚启动），跳过此过滤
        if engine is not None:
            rps_bundle = engine.get_precomputed_rps()
            if rps_bundle is not None:
                rps250_dict = rps_bundle.get('rps250', {})
                if rps250_dict:
                    disqualified_rps: set[str] = set()
                    for code in qualifying_codes:
                        rps_val = rps250_dict.get(code)
                        # 【核心】如果完全没有 RPS250（例如上市期 < 250 天的次新股）或者 RPS250 < 85，一律视为不合格并剔除
                        if rps_val is None or rps_val < 85:
                            disqualified_rps.add(code)
                    if disqualified_rps:
                        qualifying_codes -= disqualified_rps
                        log.info(
                            f"[龙虎榜池] 剔除次新及RPS250<85共 {len(disqualified_rps)} 只"
                        )

        if not qualifying_codes:
            return []

        # 第二轮扫描：对每个合格股票，取最近一次上榜数据用于展示
        # 入池资格已由第一轮扫描保证（至少有一天双正），这里只管展示最新的
        sorted_dates = sorted(self._data.keys(), reverse=True)
        latest_records: dict[str, dict] = {}

        for date_str in sorted_dates:
            for rec in self._data[date_str]:
                code = rec.get("代码", "")
                if code in qualifying_codes and code not in latest_records:
                    # 获取该条记录的核心净买数据
                    try:
                        net_buy = float(rec.get("上榜净买额(万)", 0))
                        jg_net = float(rec.get("机构净买(万)", 0))
                        wz_net = float(rec.get("外资净买(万)", 0))
                    except (ValueError, TypeError):
                        net_buy = jg_net = wz_net = 0.0

                    # 【核心需求】：最后5列数据，必须显示【最近一次符合筛选条件的数据】
                    # 即要求：上榜净买额 > 0 且 机构净买 > 0 且 外资净买 >= 0
                    if not (net_buy > 0 and jg_net > 0 and wz_net >= 0):
                        continue

                    record = dict(rec)
                    record["上榜次数"] = code_hit_count.get(code, 1)
                    record["最近上榜"] = record.get("上榜日期", date_str)
                    
                    # === 计算股价位置 & 静态买点 ===
                    if data_provider is not None:
                        try:
                            df_k = data_provider.get_data(code)
                            if df_k is not None and not df_k.empty and len(df_k) >= 20:
                                # 处理日期列
                                if 'date' in df_k.columns:
                                    last_date = str(df_k['date'].iloc[-1])[:10]
                                elif '日期' in df_k.columns:
                                    last_date = str(df_k['日期'].iloc[-1])[:10]
                                else:
                                    last_date = str(df_k.index[-1])[:10]
                                
                                # 核心终极技：不再传任何玄学求和，直接传最干净的最后 20 根收盘价数组
                                # 一切留给 UI 渲染层去根据“当前时间”动态推导
                                hist_list = df_k['close'].tail(20).astype(float).tolist()
                                
                                record["_history_20"] = hist_list
                                record["_history_date"] = last_date
                                
                                # 静态回显 (用于在没有实时行情推送的初始化瞬间，把位置显示出来)
                                ma10 = sum(hist_list[-10:]) / 10 if len(hist_list) >= 10 else 0
                                ma20 = sum(hist_list[-20:]) / 20 if len(hist_list) >= 20 else 0
                                
                                # 提取开盘价（兼容盘后首次点开不跳动行情时的静态推断）
                                try:
                                    last_open = float(df_k.get('open', df_k['close']).iloc[-1])
                                except Exception:
                                    last_open = hist_list[-1]
                                
                                last_close = hist_list[-1]
                                is_red_candle = (last_close >= last_open)
                                # 新版买点定义：
                                # 1. 多头或纠缠准备金叉状态：MA10 > MA20
                                # 2. 开盘价被强行砸在均线以下吸筹：last_open < ma10
                                # 3. 终盘/现价必须收稳、守住均线支撑：last_close > ma20 * 0.95
                                # 4. 当天必须是红 K 线：last_close >= last_open
                                if is_red_candle and (ma10 > ma20) and (last_open < ma10) and (last_close > ma20 * 0.95):
                                    record["买点"] = "✅"
                        except Exception as e:
                            log.debug(f"[龙虎榜池] 计算 {code} 股价位置失败: {e}")

                    latest_records[code] = record

        # 排序：优先把位于买点（即打出 ✅ 的标的）排在最前面，其次按最近上榜日由近到远（降序），最后同一天按涨跌幅倒序（降序）
        result = list(latest_records.values())
        result.sort(
            key=lambda x: (
                1 if x.get("买点", "") != "" else 0,
                str(x.get("最近上榜", "")),
                float(x.get("涨幅%", 0)),
            ),
            reverse=True,
        )

        log.info(
            f"[龙虎榜池] 池计算完成: {len(self._data)} 天数据中，"
            f"{len(qualifying_codes)} 只标的入池"
        )
        
        # 挂机防漏：计算核心完成深层循环后，显式扫地出门，回收计算期产生的海量瞬态字典和列表残余
        gc.collect()
        
        return result

