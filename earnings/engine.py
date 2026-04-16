import os
import time
from datetime import datetime

import akshare as ak
import numpy as np
import pandas as pd

# 🌟 彻底封堵 tqdm 乱码进度条的“核弹级”补丁 🌟
# 因为 akshare 在多线程后台调用时会锁定真实的底层终端，简单的截断 stdout 没用。
# 我们直接在内存里“黑”掉 tqdm 进度条的底层核心构造函数。
try:
    import tqdm

    from core.logger import get_logger
    _hijack_logger = get_logger()

    _original_tqdm_init = tqdm.tqdm.__init__
    _original_tqdm_update = tqdm.tqdm.update

    def _silent_tqdm_init(self, *args, **kwargs):
        kwargs['disable'] = True  # 依然拔掉终端里的乱码输出线
        _original_tqdm_init(self, *args, **kwargs)
        self._my_n = 0

    def _my_tqdm_update(self, n=1):
        _original_tqdm_update(self, n)
        self._my_n += n
        total = getattr(self, 'total', None) or '?'
        # 逢 5 倍数或者最后一页，向 UI 广播一声心跳
        if self._my_n % 5 == 0 or self._my_n == total:
            _hijack_logger.info(f"[业绩引擎] 分页抓取中 {self._my_n}/{total}")

    tqdm.tqdm.__init__ = _silent_tqdm_init
    tqdm.tqdm.update = _my_tqdm_update
except (AttributeError, ImportError, RuntimeError, TypeError, ValueError) as _e:
    import logging as _logging
    _logging.getLogger(__name__).debug(f"[tqdm补丁] tqdm 劫持失败（非致命）: {_e}")

# akshare/pandas/numpy 已在文件顶部 import，此处不再重复
import json

from core.logger import get_logger
from core.market_calendar import MarketCalendar

logger = get_logger()
EARNINGS_QOQ_MIN_PCT = 30.0

_POOL_CACHE = {}
_AKSHARE_FETCH_ERRORS = (
    AttributeError,
    ConnectionError,
    KeyError,
    OSError,
    RuntimeError,
    TimeoutError,
    TypeError,
    ValueError,
    IndexError,
)
_EARNINGS_CACHE_ERRORS = (
    AttributeError,
    ImportError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    json.JSONDecodeError,
)
_EARNINGS_COMPUTE_ERRORS = _AKSHARE_FETCH_ERRORS + (ArithmeticError,)


def _parse_amount(value):
    """把财务字段统一转成元，兼容字符串中的 万/亿 单位。"""
    if pd.isna(value):
        return np.nan
    value_str = str(value).strip()
    if not value_str:
        return np.nan

    multiplier = 1.0
    if '万' in value_str:
        multiplier = 10000.0
        value_str = value_str.replace('万', '')
    elif '亿' in value_str:
        multiplier = 100000000.0
        value_str = value_str.replace('亿', '')

    digits = ''.join(filter(lambda x: x.isdigit() or x in '.-', value_str))
    if not digits or digits in ('.', '-', '-.'):
        return np.nan
    try:
        return float(digits) * multiplier
    except (ValueError, TypeError):
        return np.nan

def safe_ak_fetch(fetch_func, *args, **kwargs):
    """带退避的强力护甲 + 大白话进度解说"""
    retries = 3
    delay = 2.0

    # 翻译文言文函数名
    fname = fetch_func.__name__
    func_cn = "未知金矿"
    if "yjyg" in fname: func_cn = "【业绩预告池】"
    elif "yjbb" in fname: func_cn = "【正式财报池】"
    elif "yjkb" in fname: func_cn = "【业绩快报池】"
    elif "financial_benefit" in fname: func_cn = "【同花顺历史底稿】"

    # 提取报备日期供打印
    param_str = kwargs.get('date', kwargs.get('symbol', '全局获取'))

    # ==== 极速内存缓存过滤（仅针对大池子，同花顺个股不管） ====
    if "financial_benefit" not in fname:
        cache_key = f"{fname}_{param_str}"
        if cache_key in _POOL_CACHE:
            cached_time, cached_df = _POOL_CACHE[cache_key]
            if time.time() - cached_time < 600:  # 10 分钟 TTL，足以覆盖一次深度扫描
                # 过滤掉冗杂的打卡日志，保持清爽
                return cached_df.copy()
    # ==========================================================

    for i in range(retries):
        try:
            # 只有抓历史底稿时过于频繁，为了不刷屏少打印开始。大型池子打印。
            if "financial_benefit" not in fname:
                logger.info(f"[业绩引擎] 拉取 {func_cn} ({param_str})...")

            res = fetch_func(*args, **kwargs)

            if "financial_benefit" not in fname:
                logger.info(f"[业绩引擎] ✅ {func_cn} ({param_str}) 拉取完成")
                _POOL_CACHE[f"{fname}_{param_str}"] = (time.time(), res.copy() if not res.empty else res)

            return res

        except _AKSHARE_FETCH_ERRORS as e:
            error_msg = str(e)
            if "NoneType" in error_msg or "not subscriptable" in error_msg:
                if "financial_benefit" not in fname:
                    logger.info(f"[业绩引擎] {func_cn} ({param_str}) 暂无数据，跳过")
                return pd.DataFrame()

            if i == retries - 1:
                logger.error(f"[业绩引擎] ❌ {func_cn} ({param_str}) 重试 {retries} 次后仍失败: {e}")
                raise e

            logger.warning(f"[业绩引擎] ⚠️ {func_cn} 请求失败({e})，{delay:.0f}s 后第 {i+2} 次重试")
            time.sleep(delay)
            delay *= 1.5


def current_active_report_dates() -> list:
    now = MarketCalendar.now("CN")
    year = now.year
    month = now.month
    dates = []
    if 1 <= month <= 4: dates.extend([f"{year-1}1231", f"{year}0331"])
    elif 7 <= month <= 8: dates.append(f"{year}0630")
    elif month == 10: dates.append(f"{year}0930")
    return dates if dates else [f"{year-1}1231", f"{year}0331", f"{year}0630", f"{year}0930"]

class EarningsEngine:
    def __init__(self, cache_file='data/earnings_state.json', keep_days=30):
        if not os.path.isabs(cache_file):
            root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cache_file = os.path.join(root_dir, cache_file)

        self.cache_file = cache_file
        self.keep_days = keep_days
        self.seen_fingerprints = set()
        self.local_records = []
        self.last_sync_date = MarketCalendar.today("CN").strftime("%Y-%m-%d")
        self._quick_report_profit_cache = {}
        self._load_cache()

    @staticmethod
    def _build_fingerprint(code: str, report_date: str, data_type: str) -> str:
        return f"SHOCK_{str(code).zfill(6)}_{report_date}_{data_type}"

    def _record_to_fingerprint(self, record: dict):
        code = str(record.get("股票代码") or record.get("代码") or "").zfill(6)
        report_date = str(record.get("报告期", "") or "")
        data_type = str(record.get("数据类型") or record.get("类型") or "")
        if not code or not report_date or not data_type:
            return None
        return self._build_fingerprint(code, report_date, data_type)

    def _prune_retryable_seen_fingerprints(self) -> bool:
        """
        清理可重试的旧预告指纹：
        - 当前活跃报告期内的“预告”指纹
        - 但本地有效记录里并没有对应落表结果
        这类指纹通常来自旧逻辑下的“缺记录/空值”等失败计算，不应永久阻断重试。
        """
        active_report_dates = set(current_active_report_dates())
        persisted_success = {
            fp for fp in (self._record_to_fingerprint(r) for r in self.local_records) if fp
        }

        cleaned = 0
        kept = set()
        for fp in self.seen_fingerprints:
            parts = fp.split("_", 3)
            if len(parts) != 4 or parts[0] != "SHOCK":
                kept.add(fp)
                continue

            _, code, report_date, data_type = parts
            if report_date in active_report_dates and fp not in persisted_success:
                cleaned += 1
                continue
            kept.add(fp)

        if cleaned:
            self.seen_fingerprints = kept
            logger.info(f"[业绩引擎] 清理 {cleaned} 条过期预告指纹")
            return True
        return False

    def _load_cache(self):
        """恢复全天候账本。清理超过 `keep_days` 天的老账"""
        from core.data_store import data_store

        data = data_store.load_earnings_state()

        # 向下兼容：首次启动如果 SQLite 无数据但旧 JSON 存在，自动迁入
        if not data and os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                logger.info("[业绩引擎] 检测到旧 JSON 缓存，自动迁入 SQLite")
                data_store.save_earnings_state(
                    data.get("last_sync_date", ""),
                    data.get("seen", []),
                    data.get("records", []),
                )
                # 旧文件重命名为 .migrated 保留 30 天后由 DataStore 自动清理
                try:
                    migrated_path = self.cache_file + ".migrated"
                    os.rename(self.cache_file, migrated_path)
                    logger.info(f"[业绩引擎] 旧 JSON 已重命名为 {migrated_path}，30 天后自动清理")
                except OSError as _e:
                    logger.debug(f"[业绩引擎] 旧 JSON 缓存重命名失败: {_e}")
            except _EARNINGS_CACHE_ERRORS as e:
                logger.error(f"[业绩引擎] 旧 JSON 迁入失败: {e}")

        if data:
            self.last_sync_date = data.get("last_sync_date", self.last_sync_date)
            self.seen_fingerprints = set(data.get("seen", []))
            all_records = data.get("records", [])

            # 清理过期数据保障性能（只保留距离今天内 N 天的数据）
            valid_records = []
            today_dt = MarketCalendar.now("CN")
            for r in all_records:
                # 强力清真过滤：剔除最新单季扣非利润为负或为 0，环比增速不足 30%，或同比为负的垃圾股
                if float(r.get("单季净利润_新增", 0.0)) <= 0 or float(r.get("环比增速_百分比", 0.0)) < EARNINGS_QOQ_MIN_PCT:
                    continue
                # 同比必须为正（即去年同期对比必须是增长的），否则说明公司在走下坡路
                if float(r.get("同比增速_百分比", -1.0)) <= 0:
                    continue

                r_date = r.get("公告日期", "")
                try:
                    r_dt = datetime.strptime(r_date, "%Y-%m-%d")
                    if (today_dt - r_dt).days <= self.keep_days:
                        valid_records.append(r)
                except (ValueError, TypeError):
                    pass

            self.local_records = valid_records
            cache_changed = self._prune_retryable_seen_fingerprints()
            if cache_changed:
                self._save_cache()
            logger.info(
                f"[业绩引擎] 💾 已加载近 {self.keep_days} 天 {len(self.local_records)} 条记录，"
                f"上次同步: {self.last_sync_date}"
            )
        else:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)

    def _save_cache(self):
        """持久化所有追溯到的记录（写入 SQLite）"""
        try:
            from core.data_store import data_store
            data_store.save_earnings_state(
                self.last_sync_date,
                list(self.seen_fingerprints),
                self.local_records,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
            logger.error(f"[业绩引擎] SQLite 持久化失败: {e}")

    def _get_quick_report_cum_profit(self, target_code: str, report_date: str) -> float:
        """
        当正式财报尚未落到同花顺历史底稿时，尝试用同报告期的业绩快报净利润回填累计值。
        注意：快报口径只有“净利润-净利润”，并不提供扣非净利润字段。
        """
        cache = getattr(self, "_quick_report_profit_cache", None)
        if cache is None:
            cache = {}
            self._quick_report_profit_cache = cache

        if report_date not in cache:
            quick_profit_map = {}
            try:
                df_kb = safe_ak_fetch(ak.stock_yjkb_em, date=report_date)
            except _AKSHARE_FETCH_ERRORS as _e:
                logger.debug(f"[业绩引擎] 快报回填抓取失败({report_date}): {_e}")
                df_kb = pd.DataFrame()

            if not df_kb.empty and '股票代码' in df_kb.columns and '净利润-净利润' in df_kb.columns:
                df_work = df_kb.copy()
                if '公告日期' in df_work.columns:
                    df_work['公告日期'] = pd.to_datetime(df_work['公告日期'], errors='coerce')
                    df_work = df_work.sort_values(by='公告日期', ascending=True, na_position='first')
                for _, row in df_work.iterrows():
                    code = str(row.get('股票代码', '')).zfill(6)
                    if not code:
                        continue
                    profit = _parse_amount(row.get('净利润-净利润', np.nan))
                    if pd.notna(profit):
                        # 同一只股票若存在多次快报修订，保留最新一次公告的净利润。
                        quick_profit_map[code] = float(profit)
            cache[report_date] = quick_profit_map

        return cache[report_date].get(str(target_code).zfill(6), np.nan)

    def _inject_sectors(self, records: list) -> list:
        if not records:
            return records
        # === 灌注通达信板块与概念基因 ===
        try:
            # 瞬间从本地挂载全市场 A 股基因字典
            # 动态获取通达信安装路径，不再硬编码
            from core.app_config import app_config
            from vcp.sector import SectorManager
            _tdx_vipdoc = app_config.get('scan/tdx_vipdoc', r'D:\HT\vipdoc')
            _tdx_root = os.path.dirname(_tdx_vipdoc) if _tdx_vipdoc else r'D:\HT'
            sm = SectorManager.get_instance(_tdx_root)
            for rec in records:
                raw_sectors = sm.get_sectors(rec['股票代码'])
                if not raw_sectors:
                    rec['所属行业与概念'] = '--'
                    continue

                industry = ''
                concepts = []
                for s in raw_sectors:
                    if s.startswith('行业_'):
                        industry = s.replace('行业_', '')
                    elif s.startswith('GN_'):
                        concepts.append(s.replace('GN_', ''))

                parts = []
                if industry:
                    parts.append(f"【{industry}】")
                if concepts:
                    parts.append(", ".join(concepts))

                rec['所属行业与概念'] = " ".join(parts) if parts else '--'
        except (AttributeError, ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
            logger.error(f"[业绩引擎] 板块数据加载失败: {e}")
            for rec in records:
                if '所属行业与概念' not in rec:
                    rec['所属行业与概念'] = '--'
        return records

    def get_cached_records(self) -> pd.DataFrame:
        """从长线账本中读取出所有还在存续期内的好股"""
        if self.local_records:
            # 读取时补齐动态的本地盘后基因
            self._inject_sectors(self.local_records)
            df = pd.DataFrame(self.local_records).sort_values(by=['公告日期', '环比增速_百分比'], ascending=[False, False])
            return df
        return pd.DataFrame()

    def fetch_daily_surprises(self, target_publish_date: str = None) -> pd.DataFrame:
        if target_publish_date is None:
            target_publish_date = MarketCalendar.today("CN").strftime("%Y-%m-%d")

        logger.info(f"[业绩引擎] 扫描目标日期: {target_publish_date}")

        sync_date_advanced = False
        should_advance_sync_date = False
        if target_publish_date > self.last_sync_date:
            should_advance_sync_date = True

        report_dates = current_active_report_dates()
        all_candidates = []
        has_critical_error = False

        for r_date in report_dates:
            try:
                df_yg = safe_ak_fetch(ak.stock_yjyg_em, date=r_date)
                if not df_yg.empty and '公告日期' in df_yg.columns:
                    df_target = df_yg[df_yg['公告日期'].astype(str).str.startswith(target_publish_date)]
                    for _, row in df_target.iterrows():
                        est_profit = pd.to_numeric(row.get('预测数值', np.nan), errors='coerce')
                        target_metric = str(row.get('预测指标', ''))

                        # 向下兼容处理旧版接口逻辑
                        if pd.isna(est_profit) and '预计净利润-下限' in row:
                            v_min = pd.to_numeric(row.get('预计扣非净利润-下限', np.nan), errors='coerce')
                            v_max = pd.to_numeric(row.get('预计扣非净利润-上限', np.nan), errors='coerce')
                            if pd.notna(v_min) and pd.notna(v_max): est_profit = (v_min + v_max) / 2
                            elif pd.notna(v_min): est_profit = v_min
                            elif pd.notna(v_max): est_profit = v_max
                            target_metric = '扣非'

                            if pd.isna(est_profit):
                                v_min = pd.to_numeric(row.get('预计净利润-下限', np.nan), errors='coerce')
                                v_max = pd.to_numeric(row.get('预计净利润-上限', np.nan), errors='coerce')
                                if pd.notna(v_min) and pd.notna(v_max): est_profit = (v_min + v_max) / 2
                                target_metric = '净利润'

                        if pd.isna(est_profit): continue

                        is_koufei = ('扣非' in target_metric or '扣除非经常性损益' in target_metric)
                        # 严格卡口：只要拿不到扣非数据就直接丢弃，不允许用归母净利润混进来
                        if not is_koufei: continue

                        all_candidates.append({
                            "股票代码": str(row['股票代码']).zfill(6), "股票名称": row.get('股票简称', ''),
                            "报告期": r_date, "数据类型": "预告", "基调": row.get('预告类型', ''),
                            "累计期末利润估算_元": float(est_profit), "公告日期": target_publish_date,
                            "is_koufei": is_koufei
                        })
            except _AKSHARE_FETCH_ERRORS as e:
                logger.error(f"[业绩引擎] 业绩预告({r_date})拉取失败: {e}")
                has_critical_error = True

        for r_date in report_dates:
            try:
                df_bb = safe_ak_fetch(ak.stock_yjbb_em, date=r_date)
                if not df_bb.empty and '最新公告日期' in df_bb.columns:
                    df_target = df_bb[df_bb['最新公告日期'].astype(str).str.startswith(target_publish_date)]
                    for _, row in df_target.iterrows():
                        est_profit = pd.to_numeric(row.get('净利润-净利润', np.nan), errors='coerce')
                        if pd.notna(est_profit):
                            all_candidates.append({
                                "股票代码": str(row['股票代码']).zfill(6), "股票名称": row.get('股票简称', ''),
                                "报告期": r_date, "数据类型": "财报", "基调": "正式出炉",
                                "累计期末利润估算_元": float(est_profit), "公告日期": target_publish_date,
                                "is_koufei": False
                            })
            except _AKSHARE_FETCH_ERRORS as e:
                logger.error(f"[业绩引擎] 财报({r_date})拉取失败: {e}")
                has_critical_error = True

            try:
                df_kb = safe_ak_fetch(ak.stock_yjkb_em, date=r_date)
                if not df_kb.empty and '公告日期' in df_kb.columns:
                    df_target = df_kb[df_kb['公告日期'].astype(str).str.startswith(target_publish_date)]
                    for _, row in df_target.iterrows():
                        est_profit = pd.to_numeric(row.get('净利润-净利润', np.nan), errors='coerce')
                        if pd.notna(est_profit):
                            all_candidates.append({
                                "股票代码": str(row['股票代码']).zfill(6), "股票名称": row.get('股票简称', ''),
                                "报告期": r_date, "数据类型": "快报", "基调": "快报速递",
                                "累计期末利润估算_元": float(est_profit), "公告日期": target_publish_date,
                                "is_koufei": False
                            })
            except _AKSHARE_FETCH_ERRORS as e:
                logger.error(f"[业绩引擎] 业绩快报({r_date})拉取失败: {e}")
                has_critical_error = True

        valid_records = []
        new_found_flag = False

        # 强制将携带真实扣非数值的记录排在前面，以防同日被互斥锁误杀
        all_candidates.sort(key=lambda x: not x['is_koufei'])

        # 初筛：把根本不用查水表的股票直接踢掉，算出真实的待审名单
        pending_candidates = []
        for cand in all_candidates:
            code = cand['股票代码']
            if not (code.startswith('0') or code.startswith('3') or code.startswith('6')): continue
            fingerprint = self._build_fingerprint(code, cand['报告期'], cand['数据类型'])
            if fingerprint in self.seen_fingerprints: continue
            pending_candidates.append(cand)

        total_pending = len(pending_candidates)
        if total_pending > 0:
            logger.info(f"[业绩引擎] 🔍 初筛完成，{total_pending} 只待深度验证")

        processed_count = 0
        import concurrent.futures

        def _check_cand(cand):
            # 将原来单次循环的闭包抽离，便于多线程投递
            code_ = cand['股票代码']
            r_date_ = cand['报告期']
            dtype_ = cand['数据类型']
            is_koufei_ = cand.pop('is_koufei', True)
            must_wait_ = (dtype_ in ['财报', '快报'])
            fingerprint_ = self._build_fingerprint(code_, r_date_, dtype_)

            res_ = self.compute_single_quarter_qoq(code_, cand['累计期末利润估算_元'], r_date_, is_koufei_, must_wait_)
            return (cand, fingerprint_, res_)

        if total_pending > 0:
            # 加入并发线程池（同花顺反爬较严，保守开 3 个线程刚刚好）
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                future_to_candidate = {
                    executor.submit(_check_cand, candidate): candidate
                    for candidate in pending_candidates
                }
                for future in concurrent.futures.as_completed(future_to_candidate):
                    processed_count += 1
                    failed_candidate = future_to_candidate[future]
                    try:
                        cand, fingerprint, res = future.result()
                        code = cand['股票代码']

                        # --- 节奏感极强的白话心跳 ---
                        if total_pending >= 50 and processed_count % 20 == 0:
                            logger.info(f"[业绩引擎] 验证进度 {processed_count}/{total_pending}")
                        elif 10 < total_pending < 50 and processed_count % 10 == 0:
                            logger.info(f"[业绩引擎] 验证进度 {processed_count}/{total_pending}")
                        elif 0 < total_pending <= 10:
                            logger.info(f"[业绩引擎] 验证 {processed_count}/{total_pending}: {code} {cand.get('股票名称', '')}")

                        error_code = res.get('error')
                        if error_code in ["THS_PENDING", "抛锚"]:
                            continue

                        if error_code is not None:
                            continue

                        # 三重硬门槛：① 单季利润为正 ② 环比>=30% ③ 同比为正（扣非同比增长）
                        yoy_pct = res.get('同比增速_百分比', -1)
                        if res.get('环比增速_百分比', -1) >= EARNINGS_QOQ_MIN_PCT and res.get('单季净利润_新增', -1) > 0 and yoy_pct > 0:
                            cand.update(res)
                            valid_records.append(cand)
                            self.local_records.append(cand)
                            self.seen_fingerprints.add(fingerprint)
                            new_found_flag = True
                    except _EARNINGS_COMPUTE_ERRORS as _e:
                        logger.debug(
                            f"[业绩引擎] {failed_candidate.get('股票代码', '?')} 并发计算异常: {_e}"
                        )

        # 致命判断：本轮雷达扫描如果没有遭遇伤筋动骨的异常断连，才允许它推移游标。
        if should_advance_sync_date and not has_critical_error:
            self.last_sync_date = target_publish_date
            sync_date_advanced = True

        if new_found_flag or sync_date_advanced:
            self._save_cache()

        if valid_records:
            # === 调用刚抽离的方法，一键灌注通达信板块与概念基因 ===
            self._inject_sectors(valid_records)

            return pd.DataFrame(valid_records).sort_values(by=['公告日期', '环比增速_百分比'], ascending=[False, False])
        return pd.DataFrame()

    def compute_single_quarter_qoq(self, target_code: str, target_est_cum_profit: float, report_date: str, is_koufei: bool = True, must_wait_ths: bool = False) -> dict:
        try:
            df_fin = safe_ak_fetch(ak.stock_financial_benefit_ths, symbol=target_code)
            if df_fin.empty: return {"error": "无历史"}
            df_fin['报告期'] = pd.to_datetime(df_fin['报告期'])
            df_fin = df_fin.sort_values(by='报告期', ascending=False)

            # --- 核心拦截：如果强制要求纯粹的扣非财报，抛弃之前传进来的虚假预估值，直接从底层提 ---
            if must_wait_ths:
                cols = [c for c in df_fin.columns if '扣除' in c]
                if not cols: return {"error": "无找点字段"}
                match_current = df_fin[df_fin['报告期'] == pd.to_datetime(report_date)]
                if match_current.empty:
                    return {"error": "THS_PENDING"}
                real_val = match_current.iloc[0][cols[0]]
                if pd.isna(real_val): return {"error": "THS_PENDING"}

                real_v_str = str(real_val).strip()
                multiplier = 1.0
                if '万' in real_v_str:
                    multiplier = 10000.0
                    real_v_str = real_v_str.replace('万', '')
                elif '亿' in real_v_str:
                    multiplier = 100000000.0
                    real_v_str = real_v_str.replace('亿', '')
                digits = ''.join(filter(lambda x: x.isdigit() or x in '.-', real_v_str))
                try:
                    target_est_cum_profit = float(digits) * multiplier
                except (ValueError, TypeError) as _e:
                    logger.debug(f"[业绩引擎] 数字解析失败({digits}): {_e}")
                    return {"error": "THS_PENDING"}
                if pd.isna(target_est_cum_profit): return {"error": "THS_PENDING"}
                is_koufei = True

            if is_koufei:
                cols = [c for c in df_fin.columns if '扣除' in c]
                if not cols: cols = [c for c in df_fin.columns if '归属于母公司' in c or '归属' in c]
                if not cols: cols = [c for c in df_fin.columns if '净利润' in c]
            else:
                cols = [c for c in df_fin.columns if '归属于母公司' in c or '归属' in c]
                if not cols: cols = [c for c in df_fin.columns if '净利润' in c and '扣除' not in c]
                if not cols: cols = [c for c in df_fin.columns if '净利润' in c]

            if not cols: return {"error": "无利润字段"}
            kf_col = cols[0]

            df_fin['累计扣非_元'] = df_fin[kf_col].apply(_parse_amount)

            r_datetime = pd.to_datetime(report_date)
            year, month = r_datetime.year, r_datetime.month

            # 【性能优化】将 dataframe 的查找时间复杂度从 O(N) 降维到 O(1) 的 Hash 查找
            df_fin.set_index('报告期', inplace=True)

            def get_cum_profit(target_date):
                td = pd.to_datetime(target_date)
                if td in df_fin.index:
                    return df_fin.at[td, '累计扣非_元']
                return np.nan

            def get_cum_profit_with_quick(target_date, basis_desc):
                value = get_cum_profit(target_date)
                if pd.notna(value):
                    return value, False

                quick_report_period = pd.to_datetime(target_date).strftime("%Y%m%d")
                quick_report_cum = self._get_quick_report_cum_profit(target_code, quick_report_period)
                if pd.notna(quick_report_cum):
                    logger.info(
                        f"[业绩引擎] {target_code} 缺 {target_date} 财报，"
                        f"回退用快报 {quick_report_period} 估算{basis_desc}"
                    )
                    return quick_report_cum, True
                return np.nan, False

            q3_date, q2_date, q1_date = f"{year}-09-30", f"{year}-06-30", f"{year}-03-31"
            last_q4_date, last_q3_date = f"{year-1}-12-31", f"{year-1}-09-30"
            # 去年同期需要的日期（用于计算单季度同比）
            last_q2_date, last_q1_date = f"{year-1}-06-30", f"{year-1}-03-31"
            current_single, last_single = np.nan, np.nan
            last_single_basis = "财报"
            # yoy_base_single: 去年同一季度的单季利润，用来算同比
            yoy_base_single = np.nan

            if month == 12:
                q3_cum, q3_quick = get_cum_profit_with_quick(q3_date, "本期累计基数")
                q2_cum, q2_quick = get_cum_profit_with_quick(q2_date, "上一季基数")
                if pd.isna(q3_cum) or pd.isna(q2_cum): return {"error": "缺记录"}
                current_single = target_est_cum_profit - q3_cum
                last_single = q3_cum - q2_cum
                if q3_quick or q2_quick:
                    last_single_basis = "快报净利润回填"
                # 去年Q4单季 = 去年全年累计 - 去年Q3累计
                ly_q4_cum, _ = get_cum_profit_with_quick(last_q4_date, "去年同期基数")
                ly_q3_cum, _ = get_cum_profit_with_quick(last_q3_date, "去年同期基数")
                if pd.notna(ly_q4_cum) and pd.notna(ly_q3_cum):
                    yoy_base_single = ly_q4_cum - ly_q3_cum
            elif month == 9:
                q2_cum, q2_quick = get_cum_profit_with_quick(q2_date, "本期累计基数")
                q1_cum, q1_quick = get_cum_profit_with_quick(q1_date, "上一季基数")
                if pd.isna(q2_cum) or pd.isna(q1_cum): return {"error": "缺记录"}
                current_single = target_est_cum_profit - q2_cum
                last_single = q2_cum - q1_cum
                if q2_quick or q1_quick:
                    last_single_basis = "快报净利润回填"
                # 去年Q3单季 = 去年Q3累计 - 去年Q2累计
                ly_q3_cum, _ = get_cum_profit_with_quick(last_q3_date, "去年同期基数")
                ly_q2_cum, _ = get_cum_profit_with_quick(last_q2_date, "去年同期基数")
                if pd.notna(ly_q3_cum) and pd.notna(ly_q2_cum):
                    yoy_base_single = ly_q3_cum - ly_q2_cum
            elif month == 6:
                q1_cum, q1_quick = get_cum_profit_with_quick(q1_date, "上一季基数")
                if pd.isna(q1_cum): return {"error": "缺记录"}
                current_single = target_est_cum_profit - q1_cum
                last_single = q1_cum
                if q1_quick:
                    last_single_basis = "快报净利润回填"
                # 去年Q2单季 = 去年Q2累计 - 去年Q1累计
                ly_q2_cum, _ = get_cum_profit_with_quick(last_q2_date, "去年同期基数")
                ly_q1_cum, _ = get_cum_profit_with_quick(last_q1_date, "去年同期基数")
                if pd.notna(ly_q2_cum) and pd.notna(ly_q1_cum):
                    yoy_base_single = ly_q2_cum - ly_q1_cum
            elif month == 3:
                current_single = target_est_cum_profit
                last_q4_cum, q4_quick = get_cum_profit_with_quick(last_q4_date, "上一季基数")
                last_q3_cum, q3_quick = get_cum_profit_with_quick(last_q3_date, "上一季基数")
                if q4_quick or q3_quick:
                    last_single_basis = "快报净利润回填"
                if pd.isna(last_q4_cum) or pd.isna(last_q3_cum): return {"error": "缺记录"}
                last_single = last_q4_cum - last_q3_cum
                # 去年Q1单季 = 去年Q1的累计值（Q1本身就是单季）
                ly_q1_cum, _ = get_cum_profit_with_quick(last_q1_date, "去年同期基数")
                if pd.notna(ly_q1_cum):
                    yoy_base_single = ly_q1_cum

            if pd.isna(current_single) or pd.isna(last_single): return {"error": "空值"}
            if last_single == 0: return {"error": "基数0"}

            qoq = (current_single - last_single) / abs(last_single) * 100

            # 计算单季度同比增速：当季扣非 vs 去年同季度扣非
            yoy = np.nan
            if pd.notna(yoy_base_single) and yoy_base_single != 0:
                yoy = (current_single - yoy_base_single) / abs(yoy_base_single) * 100

            result = {
                "单季净利润_新增": current_single, "单季净利润_上期": last_single,
                "单季净利润_去年同期": yoy_base_single if pd.notna(yoy_base_single) else 0.0,
                "环比增速_百分比": round(qoq, 2),
                "同比增速_百分比": round(yoy, 2) if pd.notna(yoy) else 0.0,
                "error": None
            }
            if last_single_basis != "财报":
                result["上季基数口径"] = last_single_basis
            return result
        except _EARNINGS_COMPUTE_ERRORS as e:
            logger.error(f"[业绩预告] 获取失败: {e}")
            return {"error": "抛锚"}
