# utils.py - 辅助工具函数
# 从 vcp_hunter.pyw 提取，零逻辑变更
import os
import json
import re
import numpy as np
import pandas as pd
from datetime import datetime

from vcp.constants import (
    AI_DIAG_CONFIG_PATH,
    MARKET_OPEN_AM, MARKET_CLOSE_AM, MARKET_OPEN_PM, MARKET_CLOSE_PM,
    PROJECT_ROOT,
)


def _text_to_pinyin_initials(text):
    """将中文转为拼音首字母串，用于模糊搜索。"""
    try:
        from pypinyin import lazy_pinyin
        s = str(text).strip()
        if not s:
            return ""
        return "".join((p[0] if p else "") for p in lazy_pinyin(s)).lower()
    except Exception:
        return ""

# ==========================================
# AI 诊断配置读写
# ==========================================
def _load_ai_diag_config():
    """读取 AI 诊断配置（Kimi API Key 等）。"""
    out = {"kimi_api_key": ""}
    try:
        if os.path.exists(AI_DIAG_CONFIG_PATH):
            with open(AI_DIAG_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            out["kimi_api_key"] = (data.get("kimi_api_key") or "").strip()
    except Exception:
        pass
    return out

def _save_ai_diag_config(cfg):
    """保存 AI 诊断配置。"""
    try:
        existing = _load_ai_diag_config()
        existing.update(cfg)
        with open(AI_DIAG_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _get_kimi_api_key():
    """获取 Kimi API Key：配置文件 → 环境变量 → 自动初始化默认值
    用户无需手动输入，首次运行自动创建配置文件。"""
    # 1. 优先读取配置文件
    cfg_key = _load_ai_diag_config().get('kimi_api_key', '').strip()
    if cfg_key:
        return cfg_key
    # 2. 其次读取环境变量
    env_key = os.environ.get('KIMI_API_KEY', '').strip()
    if env_key:
        return env_key
    # 3. 兜底：使用内置默认值，并自动写入配置文件以便后续管理
    _default_key = "sk-jtTBTLeEN6CHrOv6824AYWauI9keEYuhyOlYFWhE71mVSleR"
    try:
        _save_ai_diag_config({"kimi_api_key": _default_key})
        print("[AI配置] 已自动创建配置文件并写入默认 API Key")
    except Exception:
        pass
    return _default_key

# ==========================================
# 通达信本地路径配置
# ==========================================
def _check_vipdoc_valid(vipdoc):
    """检查 vipdoc 目录是否有效（含 sh/sz 子目录）"""
    return (os.path.isdir(vipdoc) and
            os.path.isdir(os.path.join(vipdoc, 'sh')) and
            os.path.isdir(os.path.join(vipdoc, 'sz')))

def _load_tdx_local_config():
    """读取通达信本地路径配置。"""
    candidates = [
        os.path.join('D:\\', 'vcp_qt', 'vcp_tdx_config.json'),
        os.path.join('D:\\', 'HT', 'vcp_tdx_config.json'),
        os.path.join(PROJECT_ROOT, 'vcp_tdx_config.json'),
    ]
    for cfg_path in candidates:
        try:
            if cfg_path and os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                root = (cfg.get('tdx_vipdoc_root') or '').strip().rstrip(os.sep)
                if not root:
                    continue
                if os.path.basename(root).lower() == 'vipdoc':
                    vipdoc = root
                else:
                    vipdoc = os.path.join(root, 'vipdoc')
                if _check_vipdoc_valid(vipdoc):
                    return vipdoc
        except Exception:
            continue
    default_ht = os.path.join('D:\\', 'HT', 'vipdoc')
    if _check_vipdoc_valid(default_ht):
        return default_ht
    return None

# ==========================================
# 通达信 .day 文件读取
# ==========================================
def read_tdx_day_file(filepath, price_div=100.0):
    """
    读取通达信 .day 日线文件，返回与 pytdx 兼容的 DataFrame。
    每 32 字节一条：日期(4)、开(4)、高(4)、低(4)、收(4)、成交额float(4)、成交量(4)、保留(4)。
    """
    if not os.path.isfile(filepath):
        return None
    try:
        with open(filepath, 'rb') as f:
            buf = f.read()
    except Exception as e:
        print(f"[Error] read_tdx_day_file: {str(e)}")
        return None
    n = len(buf) // 32
    if n == 0:
        return None
    try:
        dtype = np.dtype([('date', '<u4'), ('o', '<u4'), ('h', '<u4'), ('low', '<u4'), ('c', '<u4'), ('amount', '<f4'), ('vol', '<u4'), ('res', '<u4')])
        raw = np.frombuffer(buf[: n * 32], dtype=dtype)
        dates = pd.to_datetime(raw['date'].astype(str), format='%Y%m%d', errors='coerce')
        valid = dates.notna()
        if not valid.any():
            return None
        dates = dates[valid]
        o = (raw['o'][valid].astype(np.float64) / price_div).round(2)
        h = (raw['h'][valid].astype(np.float64) / price_div).round(2)
        low = (raw['low'][valid].astype(np.float64) / price_div).round(2)
        c = (raw['c'][valid].astype(np.float64) / price_div).round(2)
        amount = raw['amount'][valid].astype(np.float64)
        vol = raw['vol'][valid].astype(np.int64)
        df = pd.DataFrame({'datetime': dates.values, 'open': o, 'high': h, 'low': low, 'close': c, 'amount': amount, 'volume': vol})
        df.set_index('datetime', inplace=True)
        df = df.sort_index(ascending=True)
        return df
    except Exception as e:
        print(f"[Error] read_tdx_day_file: {str(e)}")
        return None

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

def is_market_hours_now():
    """当前是否在 A 股交易时间内"""
    now = datetime.now()
    h, m = now.hour, now.minute
    t = h * 60 + m
    am_open  = MARKET_OPEN_AM[0]  * 60 + MARKET_OPEN_AM[1]
    am_close = MARKET_CLOSE_AM[0] * 60 + MARKET_CLOSE_AM[1]
    pm_open  = MARKET_OPEN_PM[0]  * 60 + MARKET_OPEN_PM[1]
    pm_close = MARKET_CLOSE_PM[0] * 60 + MARKET_CLOSE_PM[1]
    return (am_open <= t <= am_close) or (pm_open <= t <= pm_close)

def is_after_930_today():
    """当日是否已过 9:30"""
    now = datetime.now()
    return now.hour > 9 or (now.hour == 9 and now.minute >= 30)

def is_after_market_close_today():
    """当日是否已过 15:00"""
    return datetime.now().hour >= 15

def fmt_date(s):
    """将日期字符串格式化为 YYYYMMDD"""
    s = str(s).strip().replace('-', '').replace('/', '')
    m = re.search(r'\d{8}', s)
    return m.group(0) if m else s

