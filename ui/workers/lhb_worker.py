import akshare as ak
import pandas as pd
from core.logger import get_logger

log = get_logger(__name__)

FOREIGN_KEYWORDS = [
    "深股通", "沪股通", "陆股通",  # 北向通
    "高盛", "摩根大通", "摩根士丹利", "瑞银", "法巴", 
    "渣打", "野村", "汇丰", "星展", "大和"
]

def fetch_lhb_data_for_date(date_str: str) -> list[dict]:
    """
    抓取指定日期的龙虎榜数据，并将 基础详情、机构统计、外资/知名游资参与情况聚合返回。
    """
    try:
        # 1. 抓取每日龙虎榜总表
        df_detail = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
        if df_detail is None or df_detail.empty:
            log.info(f"[龙虎榜抓取] {date_str} 基础榜单为空，可能无数据或尚未发布。")
            return []
            
        # 智能去重：同一天某只股票可能因为多种原因上榜，合并其原因并保留唯一行
        # 注意：必须同时根据“代码”和“龙虎榜买卖净额”作为复合主键去重！
        # 因为“日涨幅偏离”和“三日涨幅偏离”虽然是同一只股票，但背后买卖金额完全不同（一个是单日，一个是三日累计）。
        # 加入资金额作为分组键，可以完美隔离三日榜和单日榜，只合并真正属于同一数据维度的上榜原因。
        if all(c in df_detail.columns for c in ['代码', '上榜原因', '龙虎榜净买额']):
            group_keys = ['代码', '龙虎榜净买额']
            df_detail['上榜原因'] = df_detail.groupby(group_keys)['上榜原因'].transform(lambda x: ' | '.join(x.dropna().astype(str).unique()))
            df_detail = df_detail.drop_duplicates(subset=group_keys, keep='first')
    except Exception as e:
        log.error(f"[龙虎榜抓取] {date_str} 基础榜单异常: {e}")
        return []

    # 2. 抓取机构买卖追踪
    df_jg = pd.DataFrame()
    try:
        df_jg = ak.stock_lhb_jgmmtj_em(start_date=date_str, end_date=date_str)
    except Exception as e:
        log.warning(f"[龙虎榜抓取] 机构买卖详情抓取失败: {e}")
    
    # 构建机构速查字典
    jg_dict = {}
    if not df_jg.empty:
        for _, row in df_jg.iterrows():
            code = str(row.get('代码', '')).zfill(6)
            jg_dict[code] = {
                '买方机构数': int(row.get('买方机构数', 0) if pd.notna(row.get('买方机构数')) else 0),
                '卖方机构数': int(row.get('卖方机构数', 0) if pd.notna(row.get('卖方机构数')) else 0),
                '机构买入净额': float(row.get('机构买入净额', 0) if pd.notna(row.get('机构买入净额')) else 0)
            }

    # 3. 抓取活跃营业部，拦截外资痕迹
    df_yyb = pd.DataFrame()
    try:
        df_yyb = ak.stock_lhb_hyyyb_em(start_date=date_str, end_date=date_str)
    except Exception as e:
        log.warning(f"[龙虎榜抓取] 活跃营业部抓取失败: {e}")

    foreign_buys = {}   # code -> [席位...]
    foreign_sells = {}  # code -> [席位...]

    if not df_yyb.empty:
        for _, row in df_yyb.iterrows():
            branch_name = str(row.get('营业部名称', ''))
            
            # --- 简写外资营业部名称 (例如: 摩根大通证券(中国)有限公司 -> 摩根大通) ---
            matched_kw = None
            for kw in FOREIGN_KEYWORDS:
                if kw in branch_name:
                    matched_kw = kw
                    break
                    
            if not matched_kw:
                continue
                
            short_branch = matched_kw
                
            # 解析该外资席位买入了哪些股票
            buy_stocks_str = str(row.get('买入股票', ''))
            sell_stocks_str = str(row.get('卖出股票', ''))
            
            # 这里记录为纯净的简写，金额在后续逐票提取
            for s_name in buy_stocks_str.split():
                if not s_name.strip(): continue
                foreign_buys.setdefault(s_name.strip(), set()).add(short_branch)
                
            for s_name in sell_stocks_str.split():
                if not s_name.strip(): continue
                foreign_sells.setdefault(s_name.strip(), set()).add(short_branch)

    # 4. 缝合主表
    results = []
    for _, row in df_detail.iterrows():
        code = str(row.get('代码', '')).zfill(6)
        name = str(row.get('名称', ''))
        
        # 提取基本字段
        net_buy = float(row.get('龙虎榜净买额', 0) if pd.notna(row.get('龙虎榜净买额')) else 0)
        close_p = float(row.get('收盘价', 0) if pd.notna(row.get('收盘价')) else 0)
        pct = float(row.get('涨跌幅', 0) if pd.notna(row.get('涨跌幅')) else 0)
        turnover = float(row.get('换手率', 0) if pd.notna(row.get('换手率')) else 0)
        mk_cap = float(row.get('流通市值', 0) if pd.notna(row.get('流通市值')) else 0)
        reason = str(row.get('上榜原因', ''))
        
        # 关联机构数据
        jg_info = jg_dict.get(code, {'买方机构数': 0, '卖方机构数': 0, '机构买入净额': 0.0})
        
        # 关联外资数据 (通过简称匹配)
        f_buys = list(foreign_buys.get(name, set()))
        f_sells = list(foreign_sells.get(name, set()))
        
        has_jg = (jg_info['买方机构数'] > 0) or (jg_info['卖方机构数'] > 0)
        has_foreign = (len(f_buys) > 0) or (len(f_sells) > 0)
        
        # 核心过滤条件: 只有 (机构参与 或 外资参与) 并且 (涨跌幅 > 0) 才抓取显示
        if not ((has_jg or has_foreign) and (pct > 0)):
            continue
            
        # 此时确认我们需要这只股票，为了计算精准的外资净买额，再单独拉取明细
        final_f_buys = []
        final_f_sells = []
        foreign_net_sum = 0.0
        
        if has_foreign:
            try:
                # 拉取该股票当天的龙虎榜营业部明细
                df_stock_detail = ak.stock_lhb_stock_detail_em(symbol=code, date=date_str)
                if not df_stock_detail.empty:
                    for _, s_row in df_stock_detail.iterrows():
                        yyb_name = str(s_row.get("交易营业部名称", ""))
                        # 看看是不是外资关键字
                        matched_kw = None
                        for kw in FOREIGN_KEYWORDS:
                            if kw in yyb_name:
                                matched_kw = kw
                                break
                                
                        if matched_kw:
                            net_str = str(s_row.get("净额", "0"))
                            try:
                                net_val = float(net_str)
                            except:
                                net_val = 0.0
                                
                            net_wan = round(net_val / 10000.0)
                            if net_wan > 0:
                                final_f_buys.append(f"{matched_kw}({net_wan}万)")
                            elif net_wan < 0:
                                final_f_sells.append(f"{matched_kw}({abs(net_wan)}万)")
                            else:
                                final_f_buys.append(f"{matched_kw}(0万)")
                            
                            foreign_net_sum += net_wan
            except Exception as e:
                log.warning(f"[外资明细] 获取 {code} 失败: {e}")
                # 降级：如果获取失败，至少保留关键字
                final_f_buys = list(f_buys)
                final_f_sells = list(f_sells)
                # 预估一个净额，供降级逻辑使用
                foreign_net_sum = len(final_f_buys) - len(final_f_sells)
                
        # ================= 深度过滤与共振计算 =================
        # 用户需求1：总之一句话 至少有一个 是净买入(>0)的情况下 才抓取
        has_any_net_buy = False
        
        if has_jg and (jg_info['机构买入净额'] > 0):
            has_any_net_buy = True
        if has_foreign and (foreign_net_sum > 0):
            has_any_net_buy = True
            
        if not has_any_net_buy:
            continue
                
        # 用户需求2：只有机构和外资均是净买入的情况下才标记资金共振
        is_resonance = False
        if has_jg and has_foreign:
            if (jg_info['机构买入净额'] > 0) and (foreign_net_sum > 0):
                is_resonance = True        
        buy_str = "买：" + " | ".join(final_f_buys) if final_f_buys else ""
        sell_str = "卖：" + " | ".join(final_f_sells) if final_f_sells else ""
        if buy_str and sell_str:
            foreign_str = f"{buy_str}   {sell_str}"
        else:
            foreign_str = buy_str or sell_str or "--"
            
        # 构造给前端的平铺字典字段
        record = {
            "代码": code,
            "名称": name,
            "现价": round(close_p, 2),
            "涨幅%": round(pct, 2),
            "市值": round(mk_cap / 100000000.0, 2) if mk_cap > 0 else "--",
            "资金共振": is_resonance,
            "上榜日期": date_str,
            "上榜净买额(万)": round(net_buy / 10000.0, 2),
            "机构净买(万)": round(jg_info['机构买入净额'] / 10000.0, 2),
            "机构家数": f"买{jg_info['买方机构数']}/卖{jg_info['卖方机构数']}",
            "外资潜伏池": foreign_str,
            "换手率%": round(turnover, 2),
            "上榜原因": reason
        }
        results.append(record)
        
    log.info(f"[龙虎榜抓取] {date_str} 成功拉取 {len(results)} 条数据, 含共振 {sum([1 for x in results if x['资金共振']])} 笔")
    return results

