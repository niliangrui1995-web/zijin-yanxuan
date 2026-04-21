# -*- coding: utf-8 -*-
"""Helpers for Asian market display metadata."""

from __future__ import annotations

import os

from core.logger import get_logger

log = get_logger(__name__)


def get_role_mapping():
    roles_mapping = {
        '2330.TW': '先进制程晶圆代工龙头',
        '3661.TW': 'ASIC设计服务龙头',
        '3711.TW': '全球封测龙头',
        '3037.TW': 'ABF载板双寡头之一',
        '8046.TW': '转型AI服务器载板',
        '2383.TW': '高频CCL龙头',
        '6213.TW': 'M9级CCL核心',
        '6274.TWO': '超低损耗CCL/HPC交换机材料',
        '1303.TW': 'CCL/PP大宗供应',
        '2313.TW': '高阶HDI/PCB',
        '3044.TW': '车用PCB龙头',
        '2308.TW': '服务器电源+散热',
        '3324.TWO': 'GPU散热模组龙头',
        '3017.TW': '散热模组/热管',
        '2449.TW': '晶圆测试代工龙头',
        '6223.TWO': 'RF探针卡/高频测试',
        '2454.TW': '边缘AI芯片巨头',
        '8035.T': '光刻后道设备霸主',
        '4062.T': 'ABF载板双寡头之一',
        '2802.T': 'ABF绝缘膜独家供应',
        '5802.T': '光纤+光模块上游核心',
        '6752.T': 'MEGTRON高频CCL',
        '3110.T': 'T-Glass全球垄断',
        '3407.T': '石英布/电子材料',
        '4004.T': 'CCL核心供应',
        '4182.T': 'BT树脂核心供应',
        '5706.T': '极低轮廓铜箔寡头',
        '5801.T': 'HVLP4铜箔双寡头',
        '5201.T': '氟化液冷+CPO材料',
        '6594.T': '液冷循环泵+精密马达',
        '6857.T': 'ATE测试设备龙头',
        '6146.T': '划片机全球垄断',
        '000660.KS': 'HBM绝对龙头',
        '005930.KS': '存储+代工+封装全能',
        '042700.KS': 'HBM TBonder垄断',
        '009150.KS': '载板+MLCC',
        '000150.KS': '韩系CCL寡头',
        '0522.HK': '先进封装设备龙头',
    }

    dict_path = r"D:\vcp_hunter\每日战报\每日战报\industry_dict.py"
    if not os.path.exists(dict_path):
        return roles_mapping
    try:
        with open(dict_path, 'r', encoding='utf-8') as f:
            for line in f:
                if "#" in line and any(
                    marker in line for marker in (".T\"", ".TW\"", ".TWO\"", ".KS\"", ".HK\"")
                ):
                    import re

                    match = re.search(r'\"([A-Z0-9\.]+)\"', line)
                    if match:
                        code = match.group(1)
                        if code not in roles_mapping:
                            comment = line.split('#')[-1].strip()
                            role_match = re.search(r'[(（](.*?)[)）]', comment)
                            if role_match:
                                comment = role_match.group(1).strip()
                            roles_mapping[code] = comment
    except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError) as exc:
        log.error(f"[AsianTab] 解析角色字典失败: {exc}")
    return roles_mapping


def get_ch_names_mapping() -> dict:
    return {
        '2330.TW': '台积电',
        '2317.TW': '鸿海',
        '2454.TW': '联发科',
        '2308.TW': '台达电',
        '2382.TW': '广达',
        '3231.TW': '纬创',
        '2356.TW': '英业达',
        '3017.TW': '双鸿',
        '3324.TWO': '奇鋐',
        '5201.T': '旭硝子',
        '6594.T': '日本电产',
        '000660.KS': 'SK海力士',
        '005930.KS': '三星电子',
        '2449.TW': '京元电子',
        '6223.TWO': '旺矽',
        '6857.T': '爱德万测试',
        '6146.T': '迪思科',
        '3661.TW': '世芯',
        '8035.T': '东京电子',
        '3044.TW': '健鼎',
        '2383.TW': '台光电',
        '6213.TW': '联茂',
        '6274.TWO': '台燿',
        '2313.TW': '华通',
        '3407.T': '旭化成',
        '5801.T': '古河电工',
        '4182.T': '三菱瓦斯化学',
        '5706.T': '三井金属',
        '3110.T': '日东纺',
        '6752.T': '松下',
        '4004.T': '力森诺科',
        '000150.KS': '斗山',
        '5802.T': '住友电工',
        '1303.TW': '南亚塑胶',
        '8046.TW': '南亚电路板',
        '3037.TW': '欣兴',
        '2802.T': '味之素',
        '042700.KS': '韩美半导体',
        '3711.TW': '日月光',
        '009150.KS': '三星电机',
        '4062.T': '揖斐电',
    }


def get_market_status(market: str) -> str:
    from app.services.ui_runtime_service import MarketCalendar
    from ui.status_registry import resolve_market_status_badge

    canonical = MarketCalendar.normalize_market(market)
    raw_status = MarketCalendar.get_market_status(canonical)
    return resolve_market_status_badge(raw_status, canonical)["text"]


def format_market_display(market_value: str, code: str = "") -> str:
    raw_market = str(market_value or "").strip()
    market_map = {
        "T": "日本",
        "JP": "日本",
        "日本": "日本",
        "KS": "韩国",
        "KR": "韩国",
        "韩国": "韩国",
        "TW": "台湾",
        "TWO": "台湾",
        "台湾": "台湾",
        "台湾上柜": "台湾",
        "HK": "香港",
        "香港": "香港",
    }
    market_map.update(
        {
            "中华民国": "台湾",
            "中华民国上柜": "台湾",
        }
    )
    if raw_market in market_map:
        return market_map[raw_market]

    suffix = str(code or "").strip().split(".")[-1] if "." in str(code or "") else ""
    if suffix in market_map:
        return market_map[suffix]

    return raw_market or "未知"

