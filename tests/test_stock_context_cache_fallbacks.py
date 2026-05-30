# -*- coding: utf-8 -*-
from types import SimpleNamespace

from ui.workspaces.stock_context_service import StockContextService


def test_stock_context_missing_optional_json_caches_return_empty(monkeypatch, tmp_path):
    service = StockContextService(SimpleNamespace())
    monkeypatch.setattr(StockContextService, "_project_root", staticmethod(lambda: tmp_path))

    assert service._load_foreign_block_cache_rows() == []
    assert service._load_na_daily_cache_rows() == []
