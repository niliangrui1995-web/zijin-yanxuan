from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path
from typing import Any

import pytest

from core.runtime_paths import APP_VERSION
from infra.market_data import asian_quote_provider
from infra.market_data import asian_realtime_provider as _legacy_provider
from infra.market_data.normalize import quote_normalizer
from infra.market_data.policies import fallback_policy
from infra.market_data.providers import asian_http_provider, yfinance_provider

legacy_provider: Any = _legacy_provider

_PATCHED_PRIVATE_HOOKS = {
    "_text",
    "_ticker_base",
    "_ticker_suffix",
    "_currency_for_ticker",
    "_first_present",
    "_first_float",
    "_positive_float",
    "_first_positive",
    "_first_mapping_item",
    "_mapping",
    "_kr_previous_close",
    "_decode_hk_response",
    "_jp_preloaded_quote",
    "_parse_jp_preloaded_page",
    "_parse_jp_indicator_page",
    "_find_twse_pe",
}


def test_legacy_facade_exports_yfinance_fetcher():
    assert "fetch_yfinance_realtime_quote" in legacy_provider.__all__
    assert legacy_provider.__all__ == asian_quote_provider.__all__
    assert legacy_provider.log is not None


def test_legacy_facade_delegates_with_versioned_private_api_contract():
    assert legacy_provider is not asian_quote_provider
    assert legacy_provider.__deprecated__ is True
    assert not hasattr(asian_quote_provider, "__deprecated__")
    assert legacy_provider.fetch_asian_realtime_quote is asian_quote_provider.fetch_asian_realtime_quote
    contract = legacy_provider.legacy_private_api_contract()

    assert contract["deprecated_since"] == "1.8.8"
    assert contract["removal_version"] == "2.0.0"
    assert contract["replacements"]["_asian_http"] == (
        "infra.market_data.providers.asian_http_provider"
    )
    with pytest.raises(TypeError):
        contract["replacements"]["_asian_http"] = "mutable"


def test_legacy_facade_removal_version_guard_tracks_application_version():
    def release(value: str) -> tuple[int, ...]:
        return tuple(int(part) for part in re.findall(r"\d+", value)[:3])

    assert release(APP_VERSION) < release(legacy_provider.__removal_version__)


def test_legacy_private_replacement_paths_are_importable():
    for replacement in asian_quote_provider.LEGACY_PRIVATE_API_CONTRACT.values():
        module_name, attribute = replacement.rsplit(".", 1)
        module = importlib.import_module(module_name)
        assert hasattr(module, attribute), replacement


@pytest.mark.parametrize("hook_name", sorted(_PATCHED_PRIVATE_HOOKS))
def test_legacy_private_hook_reads_warn_and_resolve_to_the_canonical_provider(hook_name):
    with pytest.warns(DeprecationWarning, match=hook_name):
        legacy_value = getattr(legacy_provider, hook_name)
    assert legacy_value is getattr(asian_quote_provider, hook_name)


def test_runtime_private_injection_uses_canonical_provider_modules(monkeypatch):
    calls: list[str] = []

    def transport(url, **_kwargs):
        calls.append(url)
        raise AssertionError("patched ticker base should prevent transport")

    monkeypatch.setattr(asian_quote_provider, "asian_market_get", transport)
    monkeypatch.setattr(asian_http_provider, "ticker_base", lambda _code: "")

    assert legacy_provider.fetch_tw_realtime_quote("2330.TW", object()) is None
    assert calls == []


def test_legacy_facade_private_write_does_not_change_the_canonical_target():
    original = asian_quote_provider._direct_quote

    def replacement(*_args, **_kwargs):
        return {"close": 99.0}

    try:
        legacy_provider._direct_quote = replacement

        assert asian_quote_provider._direct_quote is original
    finally:
        del legacy_provider._direct_quote


def test_legacy_facade_injects_monkeypatched_http_transport(monkeypatch):
    def transport(*args, **kwargs):
        return None

    captured: dict[str, Any] = {}

    def fake_fetch(code, session, *, cancellation_token, http_get):
        captured.update(
            code=code,
            session=session,
            cancellation_token=cancellation_token,
            http_get=http_get,
        )
        return {"close": 1.0}

    monkeypatch.setattr(asian_quote_provider, "asian_market_get", transport)
    monkeypatch.setattr(asian_http_provider, "fetch_tw_realtime_quote", fake_fetch)
    session = object()
    token = object()

    assert legacy_provider.fetch_tw_realtime_quote("2330.TW", session, cancellation_token=token) == {
        "close": 1.0
    }
    assert captured == {
        "code": "2330.TW",
        "session": session,
        "cancellation_token": token,
        "http_get": transport,
    }


def test_legacy_facade_injects_monkeypatched_normalizer_hook(monkeypatch):
    def resolver(**kwargs):
        return 9.5

    captured: dict[str, Any] = {}

    def fake_fetch(code, session, **kwargs):
        captured.update(kwargs)
        return {"close": 10.0}

    monkeypatch.setattr(asian_quote_provider, "resolve_previous_close", resolver)
    monkeypatch.setattr(yfinance_provider, "fetch_yfinance_realtime_quote", fake_fetch)

    assert legacy_provider.fetch_yfinance_realtime_quote("2330.TW", object()) == {"close": 10.0}
    assert captured["previous_close_resolver"] is resolver


def test_split_normalizer_preserves_numeric_and_ohlc_semantics():
    assert quote_normalizer.to_float("￥1,234.50%") == 1234.5
    assert quote_normalizer.daily_ohlc(
        {"close": 12.0, "open": 11.0, "high": 13.0, "low": 10.0},
        {},
        None,
    ) == (12.0, 11.0, 13.0, 10.0)


def test_split_fallback_policy_preserves_suffix_dispatch():
    calls: list[tuple[str, tuple[Any, ...]]] = []

    def fetcher(name):
        def fetch(*args):
            calls.append((name, args))
            return 1.0, name

        return fetch

    result = fallback_policy.dispatch_asian_pe_fallback(
        "5201.T",
        object(),
        fetchers={
            "TW": fetcher("TW"),
            "TWO": fetcher("TWO"),
            "KS": fetcher("KS"),
            "T": fetcher("T"),
            "T_KABUTAN": fetcher("T_KABUTAN"),
        },
        rate_limit_status=lambda: {"active": True},
    )

    assert result == (1.0, "T_KABUTAN")
    assert calls[0][0] == "T_KABUTAN"
    assert calls[0][1] == ("5201",)


def test_split_provider_dependency_direction_and_legacy_entrypoint_boundary():
    project_root = Path(__file__).resolve().parents[1]
    split_root = project_root / "infra" / "market_data"
    layer_rules = {
        "normalize": ("infra.market_data.providers", "infra.market_data.policies"),
        "providers": ("infra.market_data.policies",),
        "policies": (),
    }
    common_blocked = ("app", "ui", "PyQt", "PySide")

    for layer, blocked_layers in layer_rules.items():
        for path in (split_root / layer).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = [
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            ]
            imports.extend(
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            )
            for module_name in imports:
                assert not module_name.startswith(common_blocked), (path, module_name)
                assert not module_name.startswith(blocked_layers), (path, module_name)

    facade_path = split_root / "asian_realtime_provider.py"
    for package in ("app", "core", "domains", "infra", "ui"):
        for path in (project_root / package).rglob("*.py"):
            if path == facade_path:
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            imported_modules = {
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            }
            imported_modules.update(
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            )
            assert "infra.market_data.asian_realtime_provider" not in imported_modules, path
            assert not any(
                isinstance(node, ast.ImportFrom)
                and node.module == "infra.market_data"
                and any(alias.name == "asian_realtime_provider" for alias in node.names)
                for node in ast.walk(tree)
            ), path
