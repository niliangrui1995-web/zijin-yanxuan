# -*- coding: utf-8 -*-
"""
v3.3 终极版：RPS + 市值双因子增强
==================================
在v3.2发现的核弹级因子（深坑+弱RPS50, 测试集80.6%胜率）基础上，
叠加流通市值分层，看大票小票之间有没有进一步的分化。
"""
import os, struct, datetime
import pandas as pd, numpy as np
from typing import Dict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, "dzjy_2years_cache.csv")
OUTPUT_MD = os.path.join(BASE_DIR, "外资大宗量化报告_v3.3_终极版.md")
TDX_DIR = r"D:\HT\vipdoc"
FOREIGN_KEYWORDS = ["高盛", "摩根大通", "摩根士丹利", "瑞银", "法巴", "渣打", "野村", "汇丰", "星展", "大和"]
VIP_KEYWORDS = ["高盛", "瑞银", "摩根大通"]


def _parse_tdx(fp):
    rows = []
    with open(fp, "rb") as f:
        while True:
            c = f.read(32)
            if not c or len(c) < 32: break
            p = struct.unpack("<IIIIIfII", c)
            rows.append({"date": str(p[0]), "close": p[4]/100, "high": p[2]/100, "open": p[1]/100})
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def load_all_klines():
    print("📂 加载通达信本地K线...")
    cache = {}
    for m in ["sh", "sz"]:
        d = os.path.join(TDX_DIR, m, "lday")
        if not os.path.isdir(d): continue
        for fn in os.listdir(d):
            if not fn.endswith(".day"): continue
            code = fn.replace(m, "").replace(".day", "")
            if not (code.startswith("0") or code.startswith("3") or code.startswith("6")):
                continue
            df = _parse_tdx(os.path.join(d, fn))
            if not df.empty and len(df) > 60:
                cache[code] = df
    print(f"   ✅ {len(cache)} 只")
    return cache


def compute_rps(all_kl, date, lb=50):
    rets = {}
    for code, kl in all_kl.items():
        if date not in kl.index: continue
        pos = kl.index.get_loc(date)
        if pos < lb: continue
        pc = kl.iloc[pos - lb]['close']
        if pc > 0:
            rets[code] = (kl.iloc[pos]['close'] / pc - 1) * 100
    if not rets: return {}
    s = sorted(rets.keys(), key=lambda c: rets[c])
    n = len(s)
    return {c: (r / n) * 100 for r, c in enumerate(s)}


def build_samples(df_raw, all_kl):
    is_fb = lambda b: any(kw in str(b) for kw in FOREIGN_KEYWORDS)
    is_vip = lambda b: any(kw in str(b) for kw in VIP_KEYWORDS)

    df = df_raw[df_raw['买方营业部'].apply(is_fb)].copy()
    df['is_vip'] = df['买方营业部'].apply(is_vip)
    df['premium_pct'] = df['折溢率'].fillna(0) * 100
    df['amt_wan'] = df['成交额'].fillna(0) / 10000.0
    df['trade_date'] = df['交易日期'].astype(str).str.replace("-", "")

    # 反算流通市值（亿）
    ratio = df['成交额/流通市值'].replace(0, np.nan)
    df['float_cap_yi'] = (df['成交额'] / ratio) / 1e8

    print(f"📊 外资买单: {len(df)} 笔")

    # 批量RPS
    dates = sorted(df['trade_date'].unique())
    print(f"   计算 {len(dates)} 个交易日的RPS...")
    rps50_cache, rps120_cache = {}, {}
    for i, td in enumerate(dates):
        if i % 50 == 0: print(f"   进度: {i}/{len(dates)}")
        rps50_cache[td] = compute_rps(all_kl, td, 50)
        rps120_cache[td] = compute_rps(all_kl, td, 120)
    print("   ✅ RPS完成")

    results = []
    for _, row in df.iterrows():
        code = str(row['证券代码']).zfill(6)
        td = row['trade_date']
        if code not in all_kl: continue
        kl = all_kl[code]
        if td not in kl.index: continue
        pos = kl.index.get_loc(td)
        n = len(kl)
        if pos + 1 >= n: continue
        entry = kl.iloc[pos + 1]['open']
        if entry <= 0: continue
        t0c = kl.iloc[pos]['close']

        def _mom(lb):
            if pos - lb < 0: return None
            pc = kl.iloc[pos - lb]['close']
            return (t0c / pc - 1) * 100 if pc > 0 else None

        lb60 = max(0, pos - 60)
        h60 = kl.iloc[lb60:pos + 1]['high'].max()
        pp = t0c / h60 if h60 > 0 else None

        def _ret(off):
            tp = pos + 1 + off
            if tp >= n: return None, None
            cr = (kl.iloc[tp]['close'] / entry - 1) * 100
            hm = kl.iloc[pos + 1:tp + 1]['high'].max()
            return cr, (hm / entry - 1) * 100

        r5, h5 = _ret(5); r10, h10 = _ret(10); r20, h20 = _ret(20)

        results.append({
            'code': code, 'name': row['证券简称'], 'trade_date': td,
            'is_vip': row['is_vip'], 'premium_pct': row['premium_pct'],
            'amt_wan': row['amt_wan'],
            'float_cap_yi': row.get('float_cap_yi', None),
            'pre_mom_20': _mom(20), 'price_position': pp,
            'rps50': rps50_cache.get(td, {}).get(code),
            'rps120': rps120_cache.get(td, {}).get(code),
            'ret_t5': r5, 'high_t5': h5,
            'ret_t10': r10, 'high_t10': h10,
            'ret_t20': r20, 'high_t20': h20,
        })

    print(f"   ✅ 有效: {len(results)} 笔")
    return pd.DataFrame(results) if results else pd.DataFrame()


def build_baseline(df_raw, all_kl, n=600):
    s = df_raw.sample(n=min(n, len(df_raw)), random_state=42)
    res = []
    for _, row in s.iterrows():
        code = str(row['证券代码']).zfill(6)
        td = str(row['交易日期']).replace("-", "")
        if code not in all_kl: continue
        kl = all_kl[code]
        if td not in kl.index: continue
        pos = kl.index.get_loc(td)
        if pos + 1 >= len(kl): continue
        ep = kl.iloc[pos + 1]['open']
        if ep <= 0: continue
        def _cr(off):
            tp = pos + 1 + off
            return (kl.iloc[tp]['close'] / ep - 1) * 100 if tp < len(kl) else None
        res.append({'ret_t5': _cr(5), 'ret_t10': _cr(10), 'ret_t20': _cr(20)})
    return pd.DataFrame(res)


def grp(df, name, bl_wr=None):
    c = len(df)
    if c == 0: return f"| {name} | 0 | - | - | - | - | - |\n"
    lines = []
    for label, rc, hc in [('T+5', 'ret_t5', 'high_t5'), ('T+10', 'ret_t10', 'high_t10'), ('T+20', 'ret_t20', 'high_t20')]:
        v = df[rc].dropna()
        if v.empty: continue
        wr = (v > 0).mean() * 100
        avg = v.mean()
        w = v[v > 0]; l = v[v < 0]
        plr = w.mean() / abs(l.mean()) if len(w) > 0 and len(l) > 0 else 0
        exp = (wr / 100 * w.mean() if len(w) > 0 else 0) - ((1 - wr / 100) * abs(l.mean()) if len(l) > 0 else 0)
        a = f" (α={wr - bl_wr:+.1f})" if bl_wr and label == 'T+10' else ""
        lines.append(f"| {name} | {c} | {label} | **{wr:.1f}%**{a} | {avg:+.2f}% | {plr:.2f} | {exp:+.2f}% |")
    return "\n".join(lines) + "\n"


def main():
    print("=" * 60)
    print("v3.3 终极版: RPS + 市值")
    print("=" * 60)

    df_raw = pd.read_csv(CACHE_FILE, dtype={'证券代码': str})
    all_kl = load_all_klines()
    samples = build_samples(df_raw, all_kl)
    if samples.empty: print("❌ 无样本"); return
    baseline = build_baseline(df_raw, all_kl)
    bl_wr = (baseline['ret_t10'].dropna() > 0).mean() * 100

    samples = samples.sort_values('trade_date')
    split = int(len(samples) * 0.7)
    train = samples.iloc[:split]
    test = samples.iloc[split:]

    md = []
    md.append("# 外资大宗量化因子 v3.3 终极版报告（RPS + 市值）")
    md.append(f"\n> 入场: T+1开盘 | 训练:{len(train)}笔 | 测试:{len(test)}笔 | 基准T+10: **{bl_wr:.1f}%** | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # ---- 市值单因子分层 ----
    md.append("\n## 一、流通市值分层（外资买入全局）\n")
    md.append("| 策略 | 样本 | 周期 | 胜率 | 均值 | 盈亏比 | 期望 |")
    md.append("|------|------|------|------|------|--------|------|")
    cap_bins = [
        ("微盘(<30亿)", samples[samples['float_cap_yi'].fillna(999) < 30]),
        ("小盘(30-100亿)", samples[(samples['float_cap_yi'].fillna(999) >= 30) & (samples['float_cap_yi'].fillna(999) < 100)]),
        ("中盘(100-300亿)", samples[(samples['float_cap_yi'].fillna(999) >= 100) & (samples['float_cap_yi'].fillna(999) < 300)]),
        ("大盘(300-1000亿)", samples[(samples['float_cap_yi'].fillna(999) >= 300) & (samples['float_cap_yi'].fillna(999) < 1000)]),
        ("超大盘(>=1000亿)", samples[samples['float_cap_yi'].fillna(999) >= 1000]),
    ]
    for label, sub in cap_bins:
        md.append(grp(sub, label, bl_wr))

    # ---- v3.2冠军因子 + 市值交叉 ----
    md.append("## 二、核心交叉：v3.2冠军（深坑+弱RPS50）× 市值\n")
    md.append("| 策略 | 样本 | 周期 | 胜率 | 均值 | 盈亏比 | 期望 |")
    md.append("|------|------|------|------|------|--------|------|")
    winner = samples[(samples['price_position'].fillna(1) < 0.8) & (samples['rps50'].fillna(50) < 30)]
    cross_bins = [
        ("冠军因子+微盘(<30亿)", winner[winner['float_cap_yi'].fillna(999) < 30]),
        ("冠军因子+小盘(30-100亿)", winner[(winner['float_cap_yi'].fillna(999) >= 30) & (winner['float_cap_yi'].fillna(999) < 100)]),
        ("冠军因子+中盘(100-300亿)", winner[(winner['float_cap_yi'].fillna(999) >= 100) & (winner['float_cap_yi'].fillna(999) < 300)]),
        ("冠军因子+大盘(>=300亿)", winner[winner['float_cap_yi'].fillna(999) >= 300]),
    ]
    for label, sub in cross_bins:
        md.append(grp(sub, label, bl_wr))

    # ---- 全策略矩阵 ----
    strategies = [
        ("基线: 外资全局", lambda d: d),
        ("v3.1冠军: 距高点>20%", lambda d: d[d['price_position'].fillna(1) < 0.8]),
        ("v3.2冠军: 深坑+弱RPS50", lambda d: d[(d['price_position'].fillna(1) < 0.8) & (d['rps50'].fillna(50) < 30)]),
        # 市值分层
        ("深坑+弱RPS+微盘(<30亿)", lambda d: d[(d['price_position'].fillna(1)<0.8)&(d['rps50'].fillna(50)<30)&(d['float_cap_yi'].fillna(999)<30)]),
        ("深坑+弱RPS+小盘(30-100亿)", lambda d: d[(d['price_position'].fillna(1)<0.8)&(d['rps50'].fillna(50)<30)&(d['float_cap_yi'].fillna(999)>=30)&(d['float_cap_yi'].fillna(999)<100)]),
        ("深坑+弱RPS+中大盘(>=100亿)", lambda d: d[(d['price_position'].fillna(1)<0.8)&(d['rps50'].fillna(50)<30)&(d['float_cap_yi'].fillna(999)>=100)]),
        # 纯市值
        ("外资+微盘(<30亿)", lambda d: d[d['float_cap_yi'].fillna(999) < 30]),
        ("外资+大盘(>=300亿)", lambda d: d[d['float_cap_yi'].fillna(999) >= 300]),
        # RPS+市值（不含深坑条件）
        ("弱RPS50+微盘(<30亿)", lambda d: d[(d['rps50'].fillna(50)<30)&(d['float_cap_yi'].fillna(999)<30)]),
        ("弱RPS50+大盘(>=300亿)", lambda d: d[(d['rps50'].fillna(50)<30)&(d['float_cap_yi'].fillna(999)>=300)]),
        # VIP版本
        ("VIP+深坑+弱RPS+小盘", lambda d: d[(d['is_vip']==True)&(d['price_position'].fillna(1)<0.8)&(d['rps50'].fillna(50)<30)&(d['float_cap_yi'].fillna(999)<100)]),
    ]

    md.append("\n## 三、训练集回测")
    md.append("| 策略 | 样本 | 周期 | 胜率 | 均值 | 盈亏比 | 期望 |")
    md.append("|------|------|------|------|------|--------|------|")
    for name, filt in strategies:
        md.append(grp(filt(train), name, bl_wr))

    md.append("\n## 四、测试集验证")
    md.append("| 策略 | 样本 | 周期 | 胜率 | 均值 | 盈亏比 | 期望 |")
    md.append("|------|------|------|------|------|--------|------|")
    for name, filt in strategies:
        md.append(grp(filt(test), name, bl_wr))

    # ---- 双杀赢家 ----
    md.append("\n## 五、终极裁决\n")
    winners = []
    for name, filt in strategies:
        tv = filt(train)['ret_t10'].dropna()
        sv = filt(test)['ret_t10'].dropna()
        if len(tv) < 5 or len(sv) < 5: continue
        twr = (tv > 0).mean() * 100
        swr = (sv > 0).mean() * 100
        if twr > bl_wr and swr > bl_wr:
            winners.append({'name': name, 'tn': len(tv), 'sn': len(sv),
                            'twr': twr, 'swr': swr,
                            'ta': twr - bl_wr, 'sa': swr - bl_wr,
                            'savg': sv.mean()})
    if winners:
        winners.sort(key=lambda x: x['sa'], reverse=True)
        md.append("| 策略 | 训练样本 | 训练胜率 | 训练α | 测试样本 | 测试胜率 | 测试α | 测试均值 |")
        md.append("|------|---------|---------|-------|---------|---------|-------|---------|")
        for w in winners:
            md.append(f"| {w['name']} | {w['tn']} | {w['twr']:.1f}% | +{w['ta']:.1f} | "
                       f"{w['sn']} | **{w['swr']:.1f}%** | **+{w['sa']:.1f}** | {w['savg']:+.2f}% |")
        b = winners[0]
        md.append(f"\n### 🏆 最终胜出: **{b['name']}**")
        md.append(f"- 测试集胜率 **{b['swr']:.1f}%**, α **+{b['sa']:.1f}**, 均值 **{b['savg']:+.2f}%**")
        if b['sa'] > 20:
            md.append("\n> ✅ 具有极强的实战信号价值。建议集成到实时监控系统中！")
    else:
        md.append("❌ 无双杀赢家")

    with open(OUTPUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"\n🎉 v3.3 报告已生成: {OUTPUT_MD}")


if __name__ == "__main__":
    main()
