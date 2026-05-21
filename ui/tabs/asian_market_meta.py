# -*- coding: utf-8 -*-
"""Helpers for Asian market display metadata."""

from __future__ import annotations

from pathlib import Path

from core.logger import get_logger

log = get_logger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PIPELINE_INDUSTRY_DICT = _PROJECT_ROOT.parent / "每日战报" / "每日战报" / "industry_dict.py"


def get_role_mapping():
    roles_mapping = {
        "2330.TW": "龙头｜先进制程代工",
        "3661.TW": "头部｜AI ASIC服务",
        "3711.TW": "龙头｜全球封测OSAT",
        "3037.TW": "龙头｜ABF载板",
        "8046.TW": "二线｜AI服务器载板",
        "2383.TW": "龙头｜高阶AI CCL",
        "6213.TW": "头部｜高速CCL弹性",
        "6274.TWO": "头部｜高速CCL放量",
        "1888.HK": "龙头｜规模CCL一体化",
        "1303.TW": "头部｜CCL/玻纤/铜箔",
        "2316.TW": "二线｜AI高速PCB弹性",
        "2313.TW": "头部｜高阶HDI PCB",
        "3044.TW": "头部｜车用/服务器PCB",
        "2308.TW": "龙头｜数据中心电源散热",
        "3324.TWO": "头部｜服务器散热模组",
        "3017.TW": "龙头｜服务器液冷模组",
        "2449.TW": "头部｜晶圆测试代工",
        "6223.TWO": "二线｜RF探针测试弹性",
        "2454.TW": "头部｜边缘AI芯片",
        "8035.T": "龙头｜涂胶显影设备",
        "4063.T": "龙头｜硅片半导体材料",
        "3436.T": "头部｜半导体硅片",
        "4062.T": "头部｜ABF载板",
        "2802.T": "龙头｜ABF绝缘膜",
        "5802.T": "头部｜光器件上游",
        "5803.T": "头部｜光纤连接组件",
        "6752.T": "龙头｜MEGTRON低损基板",
        "3110.T": "龙头｜T-Glass玻纤布",
        "3407.T": "头部｜超薄玻纤材料",
        "4004.T": "头部｜封装基板芯材",
        "4182.T": "龙头｜BT树脂材料",
        "5706.T": "龙头｜HVLP铜箔",
        "5801.T": "头部｜HVLP铜箔",
        "5201.T": "头部｜玻璃/CPO材料",
        "6981.T": "龙头｜MLCC被动元件",
        "7735.T": "头部｜PCB直接成像",
        "6594.T": "头部｜PCB电测检测",
        "6113.T": "头部｜PCB激光钻孔",
        "6278.T": "龙头｜PCB精密微钻",
        "6925.T": "头部｜PCB曝光光源",
        "6857.T": "龙头｜半导体ATE",
        "7729.T": "头部｜探针台/计量",
        "6871.T": "头部｜存储探针卡",
        "6146.T": "龙头｜划片研磨设备",
        "000660.KS": "龙头｜HBM高端DRAM",
        "005930.KS": "头部｜存储/代工/封装",
        "042700.KS": "龙头｜HBM TC Bonder",
        "009150.KS": "头部｜MLCC/IC载板",
        "011790.KS": "头部｜玻璃基板先行",
        "000150.KS": "头部｜高端CCL材料",
        "0522.HK": "头部｜先进封装设备",
    }

    dict_path = _PIPELINE_INDUSTRY_DICT
    if not dict_path.exists():
        return roles_mapping
    try:
        with dict_path.open("r", encoding="utf-8") as f:
            for line in f:
                if "#" in line and any(marker in line for marker in ('.T"', '.TW"', '.TWO"', '.KS"', '.HK"')):
                    import re

                    match = re.search(r"\"([A-Z0-9\.]+)\"", line)
                    if match:
                        code = match.group(1)
                        if code not in roles_mapping:
                            comment = line.split("#")[-1].strip()
                            role_match = re.search(r"[(（](.*?)[)）]", comment)
                            if role_match:
                                comment = role_match.group(1).strip()
                            roles_mapping[code] = comment
    except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError) as exc:
        log.error(f"[AsianTab] 解析角色字典失败: {exc}")
    return roles_mapping


def get_ch_names_mapping() -> dict:
    return {
        "2330.TW": "台积电",
        "2317.TW": "鸿海",
        "2454.TW": "联发科",
        "2308.TW": "台达电",
        "2382.TW": "广达",
        "3231.TW": "纬创",
        "2356.TW": "英业达",
        "3017.TW": "奇鋐",
        "3324.TWO": "双鸿",
        "5201.T": "旭硝子",
        "6981.T": "村田制作所",
        "7735.T": "SCREEN",
        "6594.T": "尼得科",
        "6113.T": "天田",
        "6278.T": "Union Tool",
        "6925.T": "牛尾电机",
        "4063.T": "信越化学",
        "3436.T": "SUMCO",
        "7729.T": "东京精密",
        "6871.T": "日本微电子",
        "000660.KS": "SK海力士",
        "005930.KS": "三星电子",
        "011790.KS": "SKC",
        "0522.HK": "ASMPT",
        "2449.TW": "京元电子",
        "6223.TWO": "旺矽",
        "6857.T": "爱德万测试",
        "6146.T": "迪思科",
        "3661.TW": "世芯",
        "8035.T": "东京电子",
        "3044.TW": "健鼎",
        "2383.TW": "台光电",
        "6213.TW": "联茂",
        "6274.TWO": "台燿",
        "1888.HK": "建滔积层板",
        "2316.TW": "楠梓电",
        "2313.TW": "华通",
        "3407.T": "旭化成",
        "5801.T": "古河电工",
        "4182.T": "三菱瓦斯化学",
        "5706.T": "三井金属",
        "3110.T": "日东纺",
        "6752.T": "松下",
        "4004.T": "力森诺科",
        "000150.KS": "斗山",
        "5802.T": "住友电工",
        "5803.T": "藤仓",
        "1303.TW": "南亚塑胶",
        "8046.TW": "南亚电路板",
        "3037.TW": "欣兴",
        "2802.T": "味之素",
        "042700.KS": "韩美半导体",
        "3711.TW": "日月光",
        "009150.KS": "三星电机",
        "4062.T": "揖斐电",
    }


def get_market_status(market: str) -> str:
    from app.services.ui_market_calendar_service import MarketCalendar
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
