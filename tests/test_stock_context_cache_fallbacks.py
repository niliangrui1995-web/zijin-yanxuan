# -*- coding: utf-8 -*-
from app.services.stock_context_model_service import StockContextReadPolicy, StockContextSnapshot
from app.services.stock_context_query_service import StockContextQueryService


def test_stock_context_missing_optional_json_caches_return_empty(monkeypatch, tmp_path):
    snapshot = StockContextSnapshot(available_sources=frozenset({"foreign_block", "na_daily"}))
    policy = StockContextReadPolicy.build(sources={"foreign_block", "na_daily"})

    assert StockContextQueryService(snapshot, root=tmp_path).query_by_code(policy) == {}


def test_stock_context_policy_can_forbid_synchronous_fund_store_query(monkeypatch):
    monkeypatch.setattr(
        "app.services.stock_context_query_service.load_fund_holding_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("fund store query must stay off the UI path")),
    )
    snapshot = StockContextSnapshot(available_sources=frozenset({"fund_holdings"}))
    policy = StockContextReadPolicy.build(
        sources={"fund_holdings"},
        target_codes={"300750"},
        allow_fund_store_query=False,
    )

    assert StockContextQueryService(snapshot).query_by_code(policy) == {}
