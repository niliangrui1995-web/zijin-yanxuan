# -*- coding: utf-8 -*-
"""
core/lhb_pool_manager.py
龙虎榜 30 日滚动关注池 — 数据引擎

负责：
- 多日龙虎榜数据的持久化存储（JSON 缓存）
- 从 30 个交易日的全量记录中筛选符合条件的标的
- 淘汰超出 30 日窗口的历史数据
- 迁移旧的单日缓存（lhb_cache.json）到新池

筛选条件：30 个交易日内至少有一天同时满足：
  ① 上榜净买额 > 0
  ② 机构净买额 >= 0
"""

import copy
import json
import os
import threading

from core.ai_industry_chain_pool import load_ai_industry_chain_stock_codes, normalize_ai_chain_code
from core.buy_point import BUY_POINT_STYLE_TEXT, calculate_buy_point_from_history
from core.logger import get_logger

log = get_logger(__name__)

# 用户定义的滚动窗口长度。
POOL_WINDOW = 30


class LhbPoolManager:
    """龙虎榜关注池数据引擎 — 线程不安全，仅限主线程操作"""

    _loaded_payload_lock = threading.RLock()
    _loaded_payload_cache: dict[str, tuple[tuple[int, int], dict]] = {}
    _stock_universe_provider = staticmethod(load_ai_industry_chain_stock_codes)

    def __init__(self):
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._cache_path = os.path.join(project_root, "data", "Cache", "lhb_pool_30d.json")
        self._legacy_pool_cache_path = os.path.join(project_root, "data", "Cache", "lhb_pool_20d.json")
        self._old_cache_path = os.path.join(project_root, "data", "Cache", "lhb_cache.json")
        self._data: dict[str, list[dict]] = {}  # date_str(yyyyMMdd) -> [records]
        self._day_meta: dict[str, dict] = {}  # date_str(yyyyMMdd) -> cache metadata
        self._last_auto_fetch_date: str = ""
        self._load()
        self._migrate_old_cache()

    # ================================================================
    # 持久化
    # ================================================================
    @staticmethod
    def _cache_file_signature(cache_path: str) -> tuple[int, int] | None:
        try:
            stat = os.stat(cache_path)
        except OSError:
            return None
        return (int(stat.st_size), int(stat.st_mtime_ns))

    @classmethod
    def _load_json_payload(cls, cache_path: str) -> dict:
        signature = cls._cache_file_signature(cache_path)
        if signature is None:
            return {}
        cache_key = os.path.abspath(cache_path)
        with cls._loaded_payload_lock:
            cached = cls._loaded_payload_cache.get(cache_key)
            if cached is not None and cached[0] == signature:
                return copy.deepcopy(cached[1])

        with open(cache_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raw = {}
        with cls._loaded_payload_lock:
            cls._loaded_payload_cache[cache_key] = (signature, copy.deepcopy(raw))
        return raw

    @classmethod
    def _remember_json_payload(cls, cache_path: str, payload: dict) -> None:
        signature = cls._cache_file_signature(cache_path)
        if signature is None:
            return
        cache_key = os.path.abspath(cache_path)
        with cls._loaded_payload_lock:
            cls._loaded_payload_cache[cache_key] = (signature, copy.deepcopy(payload))

    def _load(self):
        cache_path = self._cache_path
        if not os.path.exists(cache_path) and os.path.exists(self._legacy_pool_cache_path):
            cache_path = self._legacy_pool_cache_path
            log.info("[龙虎榜池] 检测到旧 20 日缓存，将作为 30 日窗口种子加载")
        if not os.path.exists(cache_path):
            return
        try:
            raw = self._load_json_payload(cache_path)
            self._data = raw.get("daily_data", {})
            self._day_meta = raw.get("day_meta", {})
            self._last_auto_fetch_date = raw.get("last_auto_fetch_date", "")
            self._repair_day_meta()
            migrated_count = self._upgrade_legacy_foreign_display_cache()
            if migrated_count:
                self.save()
                log.info(f"[龙虎榜池] 已升级 {migrated_count} 条旧版外资席位摘要缓存")
            log.info(f"[龙虎榜池] 缓存加载成功，包含 {len(self._data)} 个交易日数据")
        except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError, json.JSONDecodeError) as e:
            log.warning(f"[龙虎榜池] 缓存加载失败，将重建: {e}")
            self._data = {}

    @staticmethod
    def _build_full_foreign_display_from_tooltip(tooltip: str) -> str:
        tooltip_text = str(tooltip or "").strip()
        if not tooltip_text:
            return ""
        if tooltip_text == "当日未发现外资席位上榜":
            return "未现身"

        lines = [line.strip() for line in tooltip_text.splitlines() if line.strip()]
        if not lines:
            return ""

        summary_prefix = "外资合计："
        summary_line = lines[0]
        if not summary_line.startswith(summary_prefix):
            return ""

        summary = summary_line[len(summary_prefix) :].strip()
        short_parts: list[str] = []
        for line in lines[1:]:
            if "：" not in line:
                continue
            branch, detail = line.split("：", 1)
            detail = detail.strip()
            if detail.startswith("净买"):
                short_parts.append(f"{branch}+{detail[2:]}")
            elif detail.startswith("净卖"):
                short_parts.append(f"{branch}-{detail[2:]}")
            elif detail.startswith("平衡"):
                short_parts.append(f"{branch}±0")

        if short_parts:
            return f"{summary} | {' / '.join(short_parts)}"
        return summary

    def _upgrade_legacy_foreign_display_cache(self) -> int:
        updated_count = 0
        for records in self._data.values():
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                display = str(record.get("外资净买入") or "").strip()
                if "等" not in display or "席" not in display:
                    continue
                tooltip = record.get("_外资净买入_tooltip")
                full_display = self._build_full_foreign_display_from_tooltip(tooltip)
                if full_display and full_display != display:
                    record["外资净买入"] = full_display
                    updated_count += 1
        return updated_count

    @staticmethod
    def _to_int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            if isinstance(value, str):
                value = value.strip().replace("%", "").replace("+", "")
                if value in {"", "-", "--"}:
                    return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _pool_sort_key(cls, row: dict) -> tuple:
        has_buy_point = 1 if str((row or {}).get("买点", "") or "").strip() else 0
        pct = cls._to_float((row or {}).get("涨幅%"), 0.0)
        recent_date = str((row or {}).get("_最近上榜_raw") or (row or {}).get("最近上榜", "") or "")
        if has_buy_point:
            return (1, pct, recent_date, 0.0)
        return (0, 0.0, recent_date, pct)

    @classmethod
    def sort_pool_rows_for_display(cls, rows) -> list[dict]:
        return sorted(list(rows or []), key=cls._pool_sort_key, reverse=True)

    def _build_day_meta(
        self,
        records: list[dict],
        *,
        source_count: int | None = None,
        validation_ref_date: str = "",
        probe_status: str = "unverified",
    ) -> dict:
        record_count = len(records) if isinstance(records, list) else 0
        return {
            "record_count": record_count,
            "source_count": record_count if source_count is None else self._to_int(source_count, record_count),
            "last_probe_ref_date": str(validation_ref_date or ""),
            "probe_status": str(probe_status or "unverified"),
        }

    def _normalize_day_meta_item(self, meta: dict | None, records: list[dict]) -> dict:
        normalized = self._build_day_meta(records)
        if not isinstance(meta, dict):
            return normalized

        actual_count = len(records) if isinstance(records, list) else 0
        normalized["record_count"] = actual_count
        normalized["source_count"] = self._to_int(meta.get("source_count"), actual_count)
        normalized["last_probe_ref_date"] = str(meta.get("last_probe_ref_date", "") or "")
        normalized["probe_status"] = str(
            meta.get("probe_status", normalized["probe_status"]) or normalized["probe_status"]
        )
        return normalized

    def _repair_day_meta(self):
        if not isinstance(self._day_meta, dict):
            self._day_meta = {}

        repaired_meta: dict[str, dict] = {}
        for date_str, records in self._data.items():
            safe_records = records if isinstance(records, list) else []
            repaired_meta[date_str] = self._normalize_day_meta_item(self._day_meta.get(date_str), safe_records)
        self._day_meta = repaired_meta

    def save(self):
        """落盘保存"""
        try:
            os.makedirs(os.path.dirname(self._cache_path), exist_ok=True)
            payload = {
                "version": 2,
                "last_auto_fetch_date": self._last_auto_fetch_date,
                "daily_data": self._data,
                "day_meta": self._day_meta,
            }
            with open(self._cache_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            self._remember_json_payload(self._cache_path, payload)
        except (PermissionError, OSError, TypeError, ValueError) as e:
            log.error(f"[龙虎榜池] 缓存保存失败: {e}")

    def _migrate_old_cache(self):
        """把旧的单日 lhb_cache.json 数据迁移到新池中，然后删除旧文件"""
        if not os.path.exists(self._old_cache_path):
            return
        try:
            with open(self._old_cache_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            date_str = old.get("date_str", "")
            rows = old.get("rows", [])
            if date_str and rows and date_str not in self._data:
                # 旧缓存直接平移，不再做格式转换（资金共振字段已废弃）
                self.add_day(date_str, rows)
                self.save()
                log.info(f"[龙虎榜池] 成功迁移旧缓存 {date_str}，{len(rows)} 条记录")
            # 清理旧缓存文件
            os.remove(self._old_cache_path)
            log.info("[龙虎榜池] 旧缓存 lhb_cache.json 已删除")
        except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError, json.JSONDecodeError) as e:
            log.warning(f"[龙虎榜池] 旧缓存迁移失败（无影响）: {e}")

    # ================================================================
    # 数据管理
    # ================================================================
    @staticmethod
    def _record_stock_code(record: dict) -> str:
        if not isinstance(record, dict):
            return ""
        return normalize_ai_chain_code(
            record.get("代码")
            or record.get("股票代码")
            or record.get("证券代码")
            or record.get("stock_code")
            or record.get("code")
        )

    def _resolve_stock_universe_codes(self) -> set[str]:
        provider = getattr(self, "stock_universe_provider", None)
        if not callable(provider):
            provider = getattr(type(self), "_stock_universe_provider", None)
        if not callable(provider):
            return set()
        try:
            return {code for code in (normalize_ai_chain_code(value) for value in provider()) if code}
        except (FileNotFoundError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.warning(f"[龙虎榜池] AI产业链股票池不可用，按空股票池处理: {exc}")
            return set()

    def _filter_records_to_stock_universe(self, records: list[dict]) -> list[dict]:
        stock_codes = self._resolve_stock_universe_codes()
        if not stock_codes:
            return []
        return [record for record in (records or []) if self._record_stock_code(record) in stock_codes]

    def add_day(self, date_str: str, records: list[dict], meta: dict | None = None):
        """写入某一天的龙虎榜数据"""
        safe_records = self._filter_records_to_stock_universe(records if isinstance(records, list) else [])
        self._data[date_str] = safe_records
        self._day_meta[date_str] = self._normalize_day_meta_item(meta, safe_records)
        # 不在这里 save()，由调用方决定何时批量保存（减少 IO）

    def get_cached_dates(self) -> set[str]:
        return set(self._data.keys())

    def get_cached_record_count(self, date_str: str) -> int:
        records = self._data.get(date_str, [])
        return len(records) if isinstance(records, list) else 0

    def get_day_meta(self, date_str: str) -> dict:
        meta = self._day_meta.get(date_str, {})
        return dict(meta) if isinstance(meta, dict) else {}

    def get_missing_dates(self, required_dates: list[str]) -> list[str]:
        """找出 required_dates 中还没有缓存的日期"""
        cached = self.get_cached_dates()
        return [d for d in required_dates if d not in cached]

    def get_dates_pending_validation(self, required_dates: list[str], validation_ref_date: str) -> list[str]:
        """找出当前窗口里需要做轻量校验的日期。"""
        pending_dates: list[str] = []
        validation_ref = str(validation_ref_date or "")

        for date_str in required_dates:
            if date_str not in self._data:
                continue

            cached_count = self.get_cached_record_count(date_str)
            meta = self._day_meta.get(date_str)
            if not isinstance(meta, dict):
                pending_dates.append(date_str)
                continue

            if self._to_int(meta.get("record_count"), -1) != cached_count:
                pending_dates.append(date_str)
                continue

            if str(meta.get("last_probe_ref_date", "") or "") != validation_ref:
                pending_dates.append(date_str)

        return pending_dates

    def mark_day_probe(self, date_str: str, source_count: int, validation_ref_date: str, status: str = "ok"):
        """记录某一天最新一次轻量校验结果。"""
        if date_str not in self._data:
            return

        meta = self._normalize_day_meta_item(self._day_meta.get(date_str), self._data.get(date_str, []))
        meta["source_count"] = self._to_int(source_count, meta["record_count"])
        meta["last_probe_ref_date"] = str(validation_ref_date or "")
        meta["probe_status"] = str(status or "ok")
        self._day_meta[date_str] = meta

    def prune(self, valid_dates: list[str]):
        """裁剪掉不在 valid_dates 窗口内的历史数据"""
        valid_set = set(valid_dates)
        to_remove = [d for d in self._data if d not in valid_set]
        if to_remove:
            for d in to_remove:
                del self._data[d]
                self._day_meta.pop(d, None)
            self.save()
            log.info(f"[龙虎榜池] 裁剪了 {len(to_remove)} 天过期数据: {sorted(to_remove)}")

    def clear_all(self):
        """清空全部缓存数据（手动全量刷新时使用）"""
        self._data.clear()
        self._day_meta.clear()
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

    @staticmethod
    def _count_rps250_eligible_symbols(data_provider) -> int:
        cache_data = getattr(data_provider, "cache_data", {}) or {}
        count = 0
        for df in cache_data.values():
            try:
                if df is not None and len(df) >= 250:
                    count += 1
            except TypeError:
                continue
        return count

    def compute_pool(self, data_provider=None, engine=None) -> list[dict]:
        """从缓存的多日数据中计算关注池。

        筛选逻辑：
        1. 遍历所有日期的所有记录
        2. 剔除 ST 股、北交所股票（纯本地字符串判断）
        3. 找出在任何一天同时满足 上榜净买额>0 AND 机构净买>=0 的股票代码
        4. 如果有 data_provider，剔除 K 线行数 < 250 的次新股
        5. 对符合条件的股票，提取最近一次上榜的详细数据
        6. 附加"上榜次数"字段（满足条件的天数）

        参数:
            data_provider: 可选，传入可用的 DataProvider 实例，
                           用于通过 K 线缓存行数判断上市天数。
                           没有传入则跳过次新股过滤。

        返回：按 买点触发优先 → 买点组内涨幅%降序 → 非买点按最近上榜日降序 排列的列表
        """
        if not self._data:
            return []

        stock_universe_codes = self._resolve_stock_universe_codes()
        if not stock_universe_codes:
            return []

        # 第一轮扫描：找出所有满足条件的代码 + 计数
        qualifying_codes: set[str] = set()
        code_hit_count: dict[str, int] = {}

        for date_str, records in self._data.items():
            for rec in records:
                code = self._record_stock_code(rec)
                name = rec.get("名称", "")
                if not code:
                    continue
                if code not in stock_universe_codes:
                    continue

                # 过滤①：剔除北交所（代码前缀 43/83/87）
                if self._is_bse_code(code):
                    continue

                # 过滤②：剔除 ST 股
                if self._is_st_stock(name):
                    continue

                net_buy = 0.0
                jg_net = 0.0
                try:
                    net_buy = float(rec.get("上榜净买额(万)", 0))
                except (ValueError, TypeError):
                    pass
                try:
                    jg_net = float(rec.get("机构净买(万)", 0))
                except (ValueError, TypeError):
                    pass

                # 过滤合集：
                # 1. 榜单总净买入必须为正
                # 2. 机构净买入必须非负
                if net_buy > 0 and jg_net >= 0:
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
                rps250_dict = rps_bundle.get("rps250", {})
                if rps250_dict:
                    disqualify_missing_rps = True
                    eligible_count = self._count_rps250_eligible_symbols(data_provider)
                    minimum_coverage = max(1000, int(eligible_count * 0.5)) if eligible_count >= 500 else 0
                    if eligible_count >= 500 and len(rps250_dict) < minimum_coverage:
                        disqualify_missing_rps = False
                        log.warning(
                            f"[龙虎榜池] RPS缓存覆盖不足({len(rps250_dict)}/{eligible_count})，本次缺失RPS不按次新剔除"
                        )

                    disqualified_rps: set[str] = set()
                    below_threshold_rps: set[str] = set()
                    for code in qualifying_codes:
                        rps_val = rps250_dict.get(code)
                        # 【核心】如果 RPS 缓存覆盖正常，缺失 RPS 仍按次新/无效处理；
                        # 若缓存覆盖明显异常，则只剔除明确 RPS250 < 85 的标的，避免误杀。
                        if rps_val is None:
                            if disqualify_missing_rps:
                                disqualified_rps.add(code)
                            continue
                        if rps_val < 85:
                            disqualified_rps.add(code)
                            below_threshold_rps.add(code)
                    if disqualified_rps:
                        qualifying_codes -= disqualified_rps
                        if disqualify_missing_rps:
                            log.info(f"[龙虎榜池] 剔除次新及RPS250<85共 {len(disqualified_rps)} 只")
                        else:
                            log.info(f"[龙虎榜池] RPS缓存覆盖异常，当前仅剔除RPS250<85共 {len(below_threshold_rps)} 只")

        if not qualifying_codes:
            return []

        # 第二轮扫描：对每个合格股票，取最近一次上榜数据用于展示
        # 入池资格已由第一轮扫描保证（至少有一天榜单净买为正且机构净买非负），这里只管展示最新的
        sorted_dates = sorted(self._data.keys(), reverse=True)
        latest_records: dict[str, dict] = {}

        for date_str in sorted_dates:
            for rec in self._data[date_str]:
                code = self._record_stock_code(rec)
                if code in qualifying_codes and code not in latest_records:
                    # 获取该条记录的核心净买数据
                    try:
                        net_buy = float(rec.get("上榜净买额(万)", 0))
                        jg_net = float(rec.get("机构净买(万)", 0))
                    except (ValueError, TypeError):
                        net_buy = jg_net = 0.0

                    # 【核心需求】：最后 5 列数据，必须显示【最近一次符合筛选条件的数据】
                    # 当前口径：上榜净买额 > 0 且 机构净买 >= 0，外资不设门槛
                    if not (net_buy > 0 and jg_net >= 0):
                        continue

                    record = dict(rec)
                    record["代码"] = code
                    record["买点"] = ""
                    record["上榜次数"] = code_hit_count.get(code, 1)
                    record["最近上榜"] = record.get("上榜日期", date_str)

                    # === 计算股价位置 & 静态买点 ===
                    if data_provider is not None:
                        try:
                            df_k = data_provider.get_data(code)
                            if df_k is not None and not df_k.empty and len(df_k) >= 20:
                                # 处理日期列
                                if "date" in df_k.columns:
                                    last_date = str(df_k["date"].iloc[-1])[:10]
                                elif "日期" in df_k.columns:
                                    last_date = str(df_k["日期"].iloc[-1])[:10]
                                else:
                                    last_date = str(df_k.index[-1])[:10]

                                # 核心终极技：不再传任何玄学求和，直接传最干净的最后 20 根收盘价数组
                                # 一切留给 UI 渲染层去根据“当前时间”动态推导
                                hist_list = df_k["close"].tail(20).astype(float).tolist()

                                record["_history_20"] = hist_list
                                record["_history_date"] = last_date

                                # 静态回显 (用于在没有实时行情推送的初始化瞬间，把位置显示出来)
                                # 提取开盘价（兼容盘后首次点开不跳动行情时的静态推断）
                                try:
                                    last_open = float(df_k.get("open", df_k["close"]).iloc[-1])
                                except (AttributeError, KeyError, IndexError, TypeError, ValueError):
                                    last_open = hist_list[-1]

                                last_close = hist_list[-1]
                                record["买点"] = calculate_buy_point_from_history(
                                    history=hist_list,
                                    open_price=last_open,
                                    close_price=last_close,
                                    style=BUY_POINT_STYLE_TEXT,
                                )
                        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
                            log.debug(f"[龙虎榜池] 计算 {code} 股价位置失败: {e}")

                    latest_records[code] = record

        # 排序：优先展示买点触发，买点组内按涨跌幅倒序；非买点仍按最近上榜日由近到远。
        result = list(latest_records.values())
        result = self.sort_pool_rows_for_display(result)

        log.debug(f"[龙虎榜池] 池计算完成: {len(self._data)} 天数据中，{len(qualifying_codes)} 只标的入池")

        return result
