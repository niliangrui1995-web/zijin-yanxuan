# -*- coding: utf-8 -*-
import json
import os

from ui.services.na_daily_service import (
    build_na_daily_rows,
    parse_battle_report,
    parse_recommendations,
    parse_report_identity,
)


def test_parse_battle_report_and_recommendations_without_qt_widget():
    content = """
# 北美战报

## 二、标的狙击表

### 🔴 先进封装（赛道A）
| 标的 | 代码 | 近3月 | 分位 | 弹性 | RS | 周线 | 催化剂 | 风控 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **长电科技** | **600584** | 12% | 80 | 高弹性：订单 | 90 | 向上 | 北美订单 | 🟡关注 |
| 无效 | ABC | 0 | 0 | 低 | 0 | - | - | - |

## 三、别的章节

## 四、今日操作建议
| 优先级 | 名称 | 代码 | 理由 | 策略 |
| --- | --- | --- | --- | --- |
| **P1** | 长电科技 | 600584 | 北美订单催化 | 回踩关注 |
"""

    stocks = parse_battle_report(content)
    recommendations = parse_recommendations(content)

    assert stocks == [
        {
            "行业": "先进封装",
            "名称": "长电科技",
            "代码": "600584",
            "近3月": "12%",
            "分位": "80",
            "弹性": "高弹性：订单",
            "RS强度": "90",
            "周线趋势": "向上",
            "催化剂": "北美订单",
            "风控": "🟡关注",
        }
    ]
    assert recommendations["600584"] == {
        "priority": "P1",
        "reason": "北美订单催化",
        "strategy": "回踩关注",
    }


def test_build_na_daily_rows_uses_service_file_parsing_and_signature(tmp_path):
    report_file = tmp_path / "战报_202604150930.md"
    report_file.write_text("# json sidecar wins\n", encoding="utf-8")
    json_file = report_file.with_suffix(".json")
    json_file.write_text(
        json.dumps(
            {
                "sniper_tables": [
                    {
                        "track_name": "AI安防（赛道B）",
                        "targets": [
                            {
                                "name": "海康威视",
                                "code": "002415",
                                "elasticity": "中弹性（订单）",
                                "catalyst": "北美需求",
                                "risk": "🟢正常 文字会被去掉",
                            }
                        ],
                    }
                ],
                "today_advice": [{"code": "002415", "priority": "P2"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    rows, report_files, signature = build_na_daily_rows([str(report_file)])

    assert report_files == [str(report_file)]
    assert signature == (f"{report_file.name}:{int(os.path.getmtime(report_file))}",)
    assert rows == [
        {
            "代码": "002415",
            "名称": "海康威视",
            "现价": "--",
            "涨幅%": "--",
            "市值": "--",
            "日报时间": "20260415",
            "细分板块": "AI安防",
            "股价弹性": "中弹性",
            "催化剂": "北美需求",
            "风控": "🟢",
            "评级": "P2",
            "_report_ts": 20260415093000,
            "_report_row_rank": 0,
        }
    ]


def test_parse_report_identity_falls_back_to_mtime_for_date_only_name(tmp_path):
    report_file = tmp_path / "战报_20260415.md"
    report_file.write_text("# test\n", encoding="utf-8")
    os.utime(report_file, (1776211200, 1776211200))

    report_date, report_ts, basename = parse_report_identity(str(report_file))

    assert report_date == "20260415"
    assert str(report_ts).startswith("20260415")
    assert basename == report_file.name
