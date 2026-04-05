import akshare as ak
import pandas as pd
import numpy as np
import logging
import json
import os
from datetime import datetime, timedelta

logger = logging.getLogger("EarningsEngine")

def current_active_report_dates() -> list:
    now = datetime.now()
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
        self.last_sync_date = datetime.now().strftime("%Y-%m-%d")
        self._load_cache()

    def _load_cache(self):
        """恢复全天候账本。清理超过 `keep_days` 天的老账"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.last_sync_date = data.get("last_sync_date", self.last_sync_date)
                    self.seen_fingerprints = set(data.get("seen", []))
                    all_records = data.get("records", [])
                    
                    # 清理过期数据保障性能（只保留距离今天内 N 天的数据）
                    valid_records = []
                    today_dt = datetime.now()
                    for r in all_records:
                        r_date = r.get("公告日期", "")
                        try:
                            r_dt = datetime.strptime(r_date, "%Y-%m-%d")
                            if (today_dt - r_dt).days <= self.keep_days:
                                valid_records.append(r)
                        except (ValueError, TypeError): pass
                    
                    self.local_records = valid_records
                    logger.info(f"💾 从硬盘无损恢复了最近 {self.keep_days} 天内的 {len(self.local_records)} 条超预期牛股，最后一次开机点为: {self.last_sync_date}")
            except Exception as e:
                logger.error(f"加载缓存失败: {e}")
        else:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)

    def _save_cache(self):
        """持久化所有追溯到的记录"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "last_sync_date": self.last_sync_date,
                    "seen": list(self.seen_fingerprints),
                    "records": self.local_records
                }, f, ensure_ascii=False)
        except Exception: pass

    def get_cached_records(self) -> pd.DataFrame:
        """从长线账本中读取出所有还在存续期内的好股"""
        if self.local_records:
            df = pd.DataFrame(self.local_records).sort_values(by=['公告日期', '环比增速_百分比'], ascending=[False, False])
            return df
        return pd.DataFrame()

    def fetch_daily_surprises(self, target_publish_date: str = None) -> pd.DataFrame:
        if target_publish_date is None:
            target_publish_date = datetime.now().strftime("%Y-%m-%d")
            
        logger.info(f"雷达扫射指定日期: {target_publish_date}")
        
        # 不再跨日清除缓存，只更新最后的同步天！
        if target_publish_date > self.last_sync_date:
            self.last_sync_date = target_publish_date
        
        report_dates = current_active_report_dates()
        all_candidates = []

        for r_date in report_dates:
            try:
                df_yg = ak.stock_yjyg_em(date=r_date)
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
                        if not is_koufei and '净利润' not in target_metric: continue
                        
                        all_candidates.append({
                            "股票代码": str(row['股票代码']).zfill(6), "股票名称": row.get('股票简称', ''),
                            "报告期": r_date, "数据类型": "预告", "基调": row.get('预告类型', ''),
                            "累计期末利润估算_元": float(est_profit), "公告日期": target_publish_date,
                            "is_koufei": is_koufei
                        })
            except Exception: pass

        for r_date in report_dates:
            try:
                df_bb = ak.stock_yjbb_em(date=r_date)
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
            except Exception: pass
            
            try:
                df_kb = ak.stock_yjkb_em(date=r_date)
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
            except Exception: pass

        valid_records = []
        new_found_flag = False
        
        # 强制将携带真实扣非数值的记录排在前面，以防同日被互斥锁误杀
        all_candidates.sort(key=lambda x: not x['is_koufei'])
        
        for cand in all_candidates:
            code = cand['股票代码']
            
            # --- 拦截逻辑：剔除北交所及新三板（保留沪深主板/创业板/科创板：0、3、6开头） ---
            if not (code.startswith('0') or code.startswith('3') or code.startswith('6')):
                continue
                
            r_date = cand['报告期']
            dtype = cand['数据类型']
            is_koufei = cand.pop('is_koufei', True)
            
            # --- 互斥过滤：只要某家公司由于预告通过审核或者被淘汰，后续同一报告期的快报、财报统统弹飞 ---
            fingerprint = f"SHOCK_{code}_{r_date}"
            
            if fingerprint in self.seen_fingerprints: continue
            
            must_wait_ths_koufei = (dtype in ['财报', '快报'])
            res = self.compute_single_quarter_qoq(code, cand['累计期末利润估算_元'], r_date, is_koufei, must_wait_ths_koufei)
            
            if res.get('error') == "THS_PENDING":
                continue  # 同花顺深度数据未同步，暂不拉黑此票，下次巡逻再来重试
                
            self.seen_fingerprints.add(fingerprint)
            new_found_flag = True
            
            if res.get('error') is None and res.get('环比增速_百分比', -1) > 0:
                cand.update(res)
                valid_records.append(cand)
                self.local_records.append(cand)
                
        if new_found_flag:
            self._save_cache()

        if valid_records:
            return pd.DataFrame(valid_records).sort_values(by=['公告日期', '环比增速_百分比'], ascending=[False, False])
        return pd.DataFrame()

    def compute_single_quarter_qoq(self, target_code: str, target_est_cum_profit: float, report_date: str, is_koufei: bool = True, must_wait_ths: bool = False) -> dict:
        try:
            df_fin = ak.stock_financial_benefit_ths(symbol=target_code)
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
                
                real_v_str = str(real_val).replace('万', '*10000').replace('亿', '*100000000')
                digits = ''.join(filter(lambda x: x.isdigit() or x in '.-*', real_v_str))
                try: target_est_cum_profit = float(pd.eval(digits))
                except (ValueError, TypeError, SyntaxError): return {"error": "THS_PENDING"}
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
            
            def safe_parse(v):
                v_str = str(v).replace('万', '*10000').replace('亿', '*100000000')
                digits = ''.join(filter(lambda x: x.isdigit() or x in '.-*', v_str))
                try: return float(pd.eval(digits)) 
                except (ValueError, TypeError, SyntaxError): return np.nan
            df_fin['累计扣非_元'] = df_fin[kf_col].apply(safe_parse)
            
            r_datetime = pd.to_datetime(report_date)
            year, month = r_datetime.year, r_datetime.month
            
            def get_cum_profit(target_date):
                match = df_fin[df_fin['报告期'] == pd.to_datetime(target_date)]
                if not match.empty: return match.iloc[0]['累计扣非_元']
                return np.nan

            q3_date, q2_date, q1_date = f"{year}-09-30", f"{year}-06-30", f"{year}-03-31"
            last_q4_date, last_q3_date = f"{year-1}-12-31", f"{year-1}-09-30"
            current_single, last_single = np.nan, np.nan

            if month == 12:
                q3_cum, q2_cum = get_cum_profit(q3_date), get_cum_profit(q2_date)
                if pd.isna(q3_cum) or pd.isna(q2_cum): return {"error": "缺记录"}
                current_single = target_est_cum_profit - q3_cum
                last_single = q3_cum - q2_cum
            elif month == 9:
                q2_cum, q1_cum = get_cum_profit(q2_date), get_cum_profit(q1_date)
                if pd.isna(q2_cum) or pd.isna(q1_cum): return {"error": "缺记录"}
                current_single = target_est_cum_profit - q2_cum
                last_single = q2_cum - q1_cum
            elif month == 6:
                q1_cum = get_cum_profit(q1_date)
                if pd.isna(q1_cum): return {"error": "缺记录"}
                current_single = target_est_cum_profit - q1_cum
                last_single = q1_cum
            elif month == 3:
                current_single = target_est_cum_profit
                last_q4_cum, last_q3_cum = get_cum_profit(last_q4_date), get_cum_profit(last_q3_date)
                if pd.isna(last_q4_cum) or pd.isna(last_q3_cum): return {"error": "缺记录"}
                last_single = last_q4_cum - last_q3_cum

            if pd.isna(current_single) or pd.isna(last_single): return {"error": "空值"}
            if last_single == 0: return {"error": "基数0"}
            
            qoq = (current_single - last_single) / abs(last_single) * 100
            return {
                "单季净利润_新增": current_single, "单季净利润_上期": last_single,
                "环比增速_百分比": round(qoq, 2), "error": None
            }
        except Exception as e:
            logger.error(f"[业绩预告] 获取失败: {e}")
            return {"error": "抛锚"}
