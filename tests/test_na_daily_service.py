# -*- coding: utf-8 -*-
import json
import os

from ui.services.na_daily_service import (
    build_na_daily_history_payload,
    build_na_daily_refresh_payload,
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


def test_na_daily_history_payload_loads_requested_date_without_cross_day_overwrite(tmp_path):
    def write_report(date, code, catalyst, timestamp="083002"):
        report_dir = tmp_path / date
        report_dir.mkdir(exist_ok=True)
        report_file = report_dir / f"战报_{date}{timestamp}.md"
        report_file.write_text("# structured sidecar\n", encoding="utf-8")
        report_file.with_suffix(".json").write_text(
            json.dumps(
                {
                    "sniper_tables": [
                        {
                            "track_name": "历史测试",
                            "targets": [
                                {
                                    "name": f"标的{code}",
                                    "code": code,
                                    "catalyst": catalyst,
                                    "risk": "🟢",
                                }
                            ],
                        }
                    ],
                    "today_advice": [{"code": code, "priority": "P1"}],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return report_file

    write_report("20260810", "000003", "同日旧运行")
    old_file = write_report("20260810", "000001", "旧日催化", timestamp="093002")
    write_report("20260812", "000001", "新日催化")
    latest_file = write_report("20260814", "000002", "最新催化")
    missing_dir = tmp_path / "20260813"
    missing_dir.mkdir()
    (missing_dir / "run_manifest.json").write_text(
        json.dumps({"status": "failed_exception", "status_reason": "no meaningful upstream evidence"}),
        encoding="utf-8-sig",
    )

    history_payload = build_na_daily_history_payload(str(tmp_path), "20260810")
    missing_payload = build_na_daily_history_payload(str(tmp_path), "20260813")
    latest_payload = build_na_daily_refresh_payload(str(tmp_path), limit=2)

    assert history_payload["status"] == "success"
    assert history_payload["report_files"] == [str(old_file)]
    assert [(row["代码"], row["日报时间"], row["催化剂"]) for row in history_payload["rows"]] == [
        ("000001", "20260810", "旧日催化")
    ]
    assert missing_payload["status"] == "missing"
    assert missing_payload["rows"] == []
    assert missing_payload["message"] == "no meaningful upstream evidence"
    assert [os.path.basename(path) for path in latest_payload["report_files"]] == [
        "战报_20260812083002.md",
        "战报_20260814083002.md",
    ]
    assert [(row["代码"], row["日报时间"]) for row in latest_payload["rows"]] == [
        ("000002", "20260814"),
        ("000001", "20260812"),
    ]
    assert latest_file.name in latest_payload["report_signature"][-1]
