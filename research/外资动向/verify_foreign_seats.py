import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

def search_keywords_in_df(df, keywords, found_seats):
    if df is None or df.empty:
        return
        
    for col in df.columns:
        if '营业部' in str(col) or '名称' in str(col) or '买方' in str(col) or '卖方' in str(col):
            for seat in df[col].dropna().unique():
                seat_str = str(seat)
                for kw in keywords:
                    if kw in seat_str:
                        found_seats.add(seat_str)

def verify_foreign_seats():
    keywords = ["高盛", "瑞银", "摩根大通", "摩根士丹利", "野村", "汇丰"]
    found_seats = set()
    
    print("🚀 正在通过 AkShare 连接东方财富数据接口...")
    print("目标关键词:", keywords)
    print("=" * 50)
    
    # 获取日期区间
    end_date = datetime.now()
    start_date = end_date - timedelta(days=90) # 查询过去90天历史数据
    date_end = end_date.strftime("%Y%m%d")
    date_start = start_date.strftime("%Y%m%d")

    # [尝试1] 近一月营业部排行统计
    try:
        print("💡 [尝试 1] 获取近一月龙虎榜营业部排行...")
        # 近一月数据较少，应该比较快
        df_yyb = ak.stock_lhb_traderstatistic_em(symbol="近一月")
        search_keywords_in_df(df_yyb, keywords, found_seats)
        print("✅ 尝试 1 完成！")
    except Exception as e:
        print(f"⚠️ 尝试 1 失败: {e}")

    # [尝试2] 机构席位每日明细 (可能含有包含外资名字的席位)
    try:
        print("💡 [尝试 2] 抽取大宗交易活跃数据...")
        # stock_dzjy_mrtj 每日大宗交易
        # 用循环获取最近10个交易日的大宗交易
        test_dates = [ (end_date - timedelta(days=i)).strftime("%Y%m%d") for i in range(15) ]
        for dt in test_dates:
            try:
                df_dzjy = ak.stock_dzjy_mrtj(start_date=dt, end_date=dt)
                search_keywords_in_df(df_dzjy, keywords, found_seats)
            except:
                pass
        print("✅ 尝试 2 完成！")
    except Exception as e:
        print(f"⚠️ 尝试 2 失败: {e}")
        
    # 如果没找到满意的结果，我们查一下整个市场的龙虎榜百强或者历史活跃
    if len(found_seats) < 3:
        try:
            print("💡 [尝试 3] 查找最全的百强营业部或类似列表...")
            df_hy = ak.stock_lhb_hyyyb_em(start_date=date_start, end_date=date_end)
            search_keywords_in_df(df_hy, keywords, found_seats)
            print("✅ 尝试 3 完成！")
        except Exception as e:
            pass

    print("\n" + "=" * 50)
    print("🎯 匹配到的官方准确营业部名称如下：")
    print("=" * 50)
    
    if not found_seats:
        print("没有找到符合这些关键词的营业部。可能近期没有上榜或者名称有缩写/不同之处。")
    else:
        categorized = {kw: set() for kw in keywords}
        for seat in found_seats:
            for kw in keywords:
                if kw in seat:
                    categorized[kw].add(seat)
                    # 不 break 是为了防止同时匹配（不过一般不会）
                    break 
                    
        for kw, seats in categorized.items():
            if seats:
                print(f"\n【{kw}系】:")
                for s in sorted(list(seats)):
                    print(f"  - {s}")
            else:
                print(f"\n【{kw}系】: 未在榜单中发现记录")

if __name__ == "__main__":
    verify_foreign_seats()
