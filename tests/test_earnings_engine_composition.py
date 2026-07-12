from __future__ import annotations

from types import SimpleNamespace

from app.services import ui_industry_chain_service
from app.services.ui_earnings_service import _create_default_engine
from domains.earnings import engine as engine_module
from domains.earnings import refresh_cache


def test_default_ui_earnings_engine_uses_app_industry_chain_providers(monkeypatch):
    captured = {}

    def _fake_engine(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(engine_module, "EarningsEngine", _fake_engine)

    engine = _create_default_engine()

    assert engine.stock_universe_provider is ui_industry_chain_service.load_cached_ai_industry_chain_stock_codes
    assert engine.stock_context_provider is ui_industry_chain_service.load_cached_ai_industry_chain_context_map
    assert captured["stock_universe_provider"] is engine.stock_universe_provider
    assert captured["stock_context_provider"] is engine.stock_context_provider


def test_refresh_process_composes_industry_chain_repository_ports(monkeypatch):
    universe_provider = lambda: {"000001"}  # noqa: E731 - stable identity for composition assertion.
    context_provider = lambda: {"000001": "示例链条"}  # noqa: E731 - stable identity for assertion.
    repository = SimpleNamespace(
        load_cached_stock_codes=universe_provider,
        load_cached_context_map=context_provider,
    )
    captured = {}

    monkeypatch.setattr(refresh_cache, "IndustryChainRepository", lambda: repository)
    monkeypatch.setattr(
        refresh_cache,
        "EarningsEngine",
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(**kwargs),
    )

    engine = refresh_cache._create_engine()

    assert engine.stock_universe_provider is universe_provider
    assert engine.stock_context_provider is context_provider
    assert captured == {
        "stock_universe_provider": universe_provider,
        "stock_context_provider": context_provider,
    }
