# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

import app.services.ui_industry_chain_service as ai_pool_implementation
import core.ai_industry_chain_pool as ai_pool_module
import infra.storage.industry_chain_repository as industry_chain_repository
from core.ai_industry_chain_pool import (
    filter_rows_to_ai_chain_codes,
    format_ai_industry_chain_context,
    load_ai_industry_chain_context_map,
    load_ai_industry_chain_rows,
    load_ai_industry_chain_stock_codes,
    load_cached_ai_industry_chain_rows,
    refresh_ai_industry_chain_rows,
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
    rows_cache = tmp_path / "ai_industry_chain_rows.json"
    codes_cache = tmp_path / "ai_industry_chain_stock_codes.json"
    context_cache = tmp_path / "ai_industry_chain_context_map.json"
    _write_workbook(workbook_path)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_FILE", workbook_path)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_ROWS_CACHE_FILE", rows_cache)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_CODES_CACHE_FILE", codes_cache)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_CONTEXT_CACHE_FILE", context_cache)

    assert ai_pool_module.load_ai_industry_chain_stock_codes() == {"300308", "002384"}

    def fail_rows(*_args, **_kwargs):
        raise AssertionError("matching signature should use stock-code cache")

    monkeypatch.setattr(ai_pool_implementation, "load_ai_industry_chain_rows", fail_rows)

    assert ai_pool_module.load_ai_industry_chain_stock_codes() == {"300308", "002384"}


def test_ai_industry_chain_default_context_cache_reuses_matching_signature(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    rows_cache = tmp_path / "ai_industry_chain_rows.json"
    codes_cache = tmp_path / "ai_industry_chain_stock_codes.json"
    context_cache = tmp_path / "ai_industry_chain_context_map.json"
    _write_workbook(workbook_path)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_FILE", workbook_path)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_ROWS_CACHE_FILE", rows_cache)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_CODES_CACHE_FILE", codes_cache)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_CONTEXT_CACHE_FILE", context_cache)

    context_map = ai_pool_module.load_ai_industry_chain_context_map()
    assert context_map["300308"] == "光模块 | 800G"

    def fail_rows(*_args, **_kwargs):
        raise AssertionError("matching signature should use context cache")

    monkeypatch.setattr(ai_pool_implementation, "load_ai_industry_chain_rows", fail_rows)

    assert ai_pool_module.load_ai_industry_chain_context_map()["300308"] == "光模块 | 800G"


def test_ai_industry_chain_rows_cache_reuses_matching_signature(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    rows_cache = tmp_path / "ai_industry_chain_rows.json"
    codes_cache = tmp_path / "ai_industry_chain_stock_codes.json"
    context_cache = tmp_path / "ai_industry_chain_context_map.json"
    _write_workbook(workbook_path)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_FILE", workbook_path)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_ROWS_CACHE_FILE", rows_cache)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_CODES_CACHE_FILE", codes_cache)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_CONTEXT_CACHE_FILE", context_cache)

    refreshed = refresh_ai_industry_chain_rows()
    assert [row["代码"] for row in refreshed] == ["300308", "002384"]

    monkeypatch.setattr(
        ai_pool_implementation,
        "load_ai_industry_chain_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache hit must not read workbook")),
    )

    cached = load_cached_ai_industry_chain_rows()
    assert [row["代码"] for row in cached] == ["300308", "002384"]


def test_ai_industry_chain_rows_cache_rejects_stale_source_signature(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    rows_cache = tmp_path / "ai_industry_chain_rows.json"
    codes_cache = tmp_path / "ai_industry_chain_stock_codes.json"
    context_cache = tmp_path / "ai_industry_chain_context_map.json"
    _write_workbook(workbook_path)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_FILE", workbook_path)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_ROWS_CACHE_FILE", rows_cache)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_CODES_CACHE_FILE", codes_cache)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_CONTEXT_CACHE_FILE", context_cache)

    assert refresh_ai_industry_chain_rows()
    with workbook_path.open("ab") as handle:
        handle.write(b"stale-signature")

    assert load_cached_ai_industry_chain_rows() == []


def test_ai_industry_chain_cache_only_miss_never_reads_workbook(monkeypatch, tmp_path):
    workbook_path = tmp_path / "AI产业链.xlsx"
    _write_workbook(workbook_path)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_FILE", workbook_path)
    monkeypatch.setattr(ai_pool_implementation, "AI_CHAIN_ROWS_CACHE_FILE", tmp_path / "missing_rows_cache.json")
    monkeypatch.setattr(
        ai_pool_implementation,
        "load_ai_industry_chain_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cache-only path must not read workbook")),
    )

    assert load_cached_ai_industry_chain_rows() == []


def test_industry_chain_repository_records_signature_cache_write_failure(monkeypatch, tmp_path):
    source_path = tmp_path / "source.xlsx"
    source_path.write_bytes(b"fixture")
    blocked_parent = tmp_path / "blocked-parent"
    blocked_parent.write_text("not-a-directory", encoding="utf-8")
    repository = industry_chain_repository.IndustryChainRepository(
        workbook_path=source_path,
        rows_cache_path=blocked_parent / "rows.json",
        codes_cache_path=tmp_path / "codes.json",
        context_cache_path=tmp_path / "context.json",
    )
    events = []
    metrics = []
    monkeypatch.setattr(
        industry_chain_repository,
        "emit_structured_log",
        lambda event, **fields: events.append((event, fields)),
    )
    monkeypatch.setattr(
        industry_chain_repository,
        "record_metric",
        lambda name, value, **kwargs: metrics.append((name, value, kwargs)),
    )

    repository._write_signature_cache(repository.rows_cache_path, source_path, "rows", [])

    assert events[0][0] == "industry_chain.cache_write.failed"
    assert events[0][1]["payload_key"] == "rows"
    assert metrics == [
        (
            "industry_chain_cache_write_failures",
            1,
            {
                "unit": "count",
                "tags": {"payload_key": "rows", "error_type": "FileExistsError"},
                "logger": industry_chain_repository._log,
            },
        )
    ]
