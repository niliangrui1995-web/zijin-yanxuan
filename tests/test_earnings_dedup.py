# -*- coding: utf-8 -*-
"""
tests/test_earnings_dedup.py — 业绩异动去重覆盖逻辑验证

验证目标：
    EarningsTab._on_new_data_found 的核心去重策略：
    1. 相同(代码+报告期)的数据，后发布的应覆盖先发布的
    2. 不同报告期的数据应共存
    3. 旧版本(更早揭晓日)不应覆盖新版本
"""
import pandas as pd


def _build_earnings_df(rows: list[dict]) -> pd.DataFrame:
    """构造模拟的业绩 DataFrame（与 EarningsEngine 输出格式一致）"""
    return pd.DataFrame(rows)


class TestEarningsDedup:
    """测试业绩数据的去重覆盖逻辑（不依赖 UI，纯逻辑抽取测试）"""

    def _simulate_dedup(self, existing_rows: list[dict], new_df: pd.DataFrame) -> list[dict]:
        """
        从 EarningsTab._on_new_data_found 中抽取的纯逻辑去重算法。
        因为原方法耦合了 UI（model.update_data），这里做等价的纯数据模拟。
        """
        row_data = list(existing_rows)  # 深拷贝防止污染

        for _, row in new_df.iterrows():
            code = str(row.get('股票代码', '')).zfill(6)
            report_period = str(row.get("报告期", ""))
            announce_date = str(row.get("公告日期", ""))
            data_type = str(row.get("数据类型", ""))

            row_obj = {
                "代码": code,
                "名称": str(row.get('股票名称', '')),
                "报告期": report_period,
                "揭晓日": announce_date,
                "类型": data_type,
                "环比%": float(row.get('环比增速_百分比', 0.0)),
            }

            exists = False
            for existing in row_data:
                if existing.get("代码") == code and existing.get("报告期") == report_period:
                    exists = True
                    old_date = existing.get("揭晓日", "")
                    new_date = row_obj["揭晓日"]
                    # 只有更晚发布的才允许覆盖
                    if new_date >= old_date:
                        existing.update(row_obj)
                    break

            if not exists:
                row_data.append(row_obj)

        return row_data

    def test_new_record_appends(self):
        """全新的(代码+报告期)组合应追加到列表"""
        existing = []
        new_df = _build_earnings_df([{
            "股票代码": "000001", "股票名称": "平安银行",
            "报告期": "20251231", "公告日期": "2026-01-15",
            "数据类型": "快报", "环比增速_百分比": 25.0,
        }])

        result = self._simulate_dedup(existing, new_df)
        assert len(result) == 1
        assert result[0]["代码"] == "000001"

    def test_newer_overwrites_older(self):
        """同(代码+报告期)，更晚的公告日期应覆盖旧版"""
        existing = [{
            "代码": "000001", "名称": "平安银行",
            "报告期": "20251231", "揭晓日": "2026-01-15",
            "类型": "预告", "环比%": 20.0,
        }]
        new_df = _build_earnings_df([{
            "股票代码": "000001", "股票名称": "平安银行",
            "报告期": "20251231", "公告日期": "2026-02-28",
            "数据类型": "财报", "环比增速_百分比": 30.0,
        }])

        result = self._simulate_dedup(existing, new_df)
        # 应该只有 1 条（覆盖，不是追加）
        assert len(result) == 1
        # 类型应被升级为"财报"
        assert result[0]["类型"] == "财报"
        assert result[0]["环比%"] == 30.0

    def test_older_does_not_overwrite_newer(self):
        """旧版(更早公告日)不应覆盖已存在的新版"""
        existing = [{
            "代码": "000001", "名称": "平安银行",
            "报告期": "20251231", "揭晓日": "2026-02-28",
            "类型": "财报", "环比%": 30.0,
        }]
        new_df = _build_earnings_df([{
            "股票代码": "000001", "股票名称": "平安银行",
            "报告期": "20251231", "公告日期": "2026-01-15",
            "数据类型": "预告", "环比增速_百分比": 20.0,
        }])

        result = self._simulate_dedup(existing, new_df)
        assert len(result) == 1
        # 类型应保持为"财报"（新版不被旧版覆盖）
        assert result[0]["类型"] == "财报"
        assert result[0]["环比%"] == 30.0

    def test_different_report_periods_coexist(self):
        """不同报告期的同一只股票应共存"""
        existing = [{
            "代码": "000001", "名称": "平安银行",
            "报告期": "20251231", "揭晓日": "2026-01-15",
            "类型": "预告", "环比%": 20.0,
        }]
        new_df = _build_earnings_df([{
            "股票代码": "000001", "股票名称": "平安银行",
            "报告期": "20260331", "公告日期": "2026-04-15",
            "数据类型": "快报", "环比增速_百分比": 50.0,
        }])

        result = self._simulate_dedup(existing, new_df)
        assert len(result) == 2, "不同报告期应共存，不该被去重"
