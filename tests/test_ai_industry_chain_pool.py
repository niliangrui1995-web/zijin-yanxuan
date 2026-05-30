# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

import core.ai_industry_chain_pool as ai_pool_module
from core.ai_industry_chain_pool import (
    filter_rows_to_ai_chain_codes,
    format_ai_industry_chain_context,
    load_ai_industry_chain_context_map,
    load_ai_industry_chain_rows,
    load_ai_industry_chain_stock_codes,
)


def _write_workbook(path: Path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "AI产业链"
    worksheet.append(["细分板块", "代码", "公司名称", "备注"])
    worksheet.append(["光模块", "300308", "中际旭创", "800G"])
    worksheet.append(["占位", "暂无", "暂无合格A股纯标的", "不硬凑"])
    worksheet.append(["PCB", 2384, "东山精密", "高速互联"])
    workbook.save(path)


def test_ai_industry_chain_pool_reads_a_share_codes(tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)

    rows = load_ai_industry_chain_rows(workbook_path)

    assert [row["代码"] for row in rows] == ["300308", "002384"]
    assert rows[0]["名称"] == "中际旭创"
    assert load_ai_industry_chain_stock_codes(workbook_path) == {"300308", "002384"}


def test_filter_rows_to_ai_chain_codes_uses_normalized_code_keys():
    rows = [
        {"证券代码": "300308", "名称": "中际旭创"},
        {"stock_code": 2384, "名称": "东山精密"},
        {"代码": "600000", "名称": "浦发银行"},
    ]

    filtered = filter_rows_to_ai_chain_codes(rows, stock_codes={"300308", "002384"})

    assert [row["名称"] for row in filtered] == ["中际旭创", "东山精密"]


def test_ai_industry_chain_context_map_uses_segment_and_remark(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    workbook = openpyxl.load_workbook(workbook_path)
    try:
        worksheet = workbook.active
        worksheet.append(["CPO", "300308", "中际旭创", "交换侧"])
        workbook.save(workbook_path)
    finally:
        workbook.close()

    context_map = load_ai_industry_chain_context_map(workbook_path)

    assert format_ai_industry_chain_context({"细分板块": "光模块", "备注": "800G"}) == "光模块 | 800G"
    assert context_map["300308"] == "光模块 | 800G；CPO | 交换侧"
    assert context_map["002384"] == "PCB | 高速互联"


def test_ai_industry_chain_default_stock_code_cache_reuses_matching_signature(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    codes_cache = tmp_path / "ai_industry_chain_stock_codes.json"
    _write_workbook(workbook_path)
    monkeypatch.setattr(ai_pool_module, "AI_CHAIN_FILE", workbook_path)
    monkeypatch.setattr(ai_pool_module, "AI_CHAIN_CODES_CACHE_FILE", codes_cache)

    assert ai_pool_module.load_ai_industry_chain_stock_codes() == {"300308", "002384"}

    def fail_rows(*_args, **_kwargs):
        raise AssertionError("matching signature should use stock-code cache")

    monkeypatch.setattr(ai_pool_module, "load_ai_industry_chain_rows", fail_rows)

    assert ai_pool_module.load_ai_industry_chain_stock_codes() == {"300308", "002384"}


def test_ai_industry_chain_default_context_cache_reuses_matching_signature(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    context_cache = tmp_path / "ai_industry_chain_context_map.json"
    _write_workbook(workbook_path)
    monkeypatch.setattr(ai_pool_module, "AI_CHAIN_FILE", workbook_path)
    monkeypatch.setattr(ai_pool_module, "AI_CHAIN_CONTEXT_CACHE_FILE", context_cache)

    context_map = ai_pool_module.load_ai_industry_chain_context_map()
    assert context_map["300308"] == "光模块 | 800G"

    def fail_rows(*_args, **_kwargs):
        raise AssertionError("matching signature should use context cache")

    monkeypatch.setattr(ai_pool_module, "load_ai_industry_chain_rows", fail_rows)

    assert ai_pool_module.load_ai_industry_chain_context_map()["300308"] == "光模块 | 800G"
