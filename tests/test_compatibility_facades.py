"""Regression guards for ordinary compatibility facades."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("legacy_module_name", "canonical_module_name", "public_name"),
    [
        ("core.json_cache", "infra.storage.json_cache_repository", "save_json_file"),
        ("core.ui_signals", "ui.signals.ui_signal_bus", "ui_signals"),
        ("earnings.engine", "domains.earnings.engine", "EarningsEngine"),
        ("earnings.scheduler", "domains.earnings.scheduler", "EarningsScheduler"),
        (
            "infra.market_data.asian_realtime_provider",
            "infra.market_data.asian_quote_provider",
            "fetch_asian_realtime_quote",
        ),
    ],
)
def test_compatibility_facade_keeps_module_identity(
    legacy_module_name: str,
    canonical_module_name: str,
    public_name: str,
):
    legacy_module = importlib.import_module(legacy_module_name)
    canonical_module = importlib.import_module(canonical_module_name)
    source = (_REPO_ROOT / Path(*legacy_module_name.split("."))).with_suffix(".py").read_text(
        encoding="utf-8"
    )

    assert legacy_module is not canonical_module
    assert legacy_module.__name__ == legacy_module_name
    assert getattr(legacy_module, public_name) is getattr(canonical_module, public_name)
    assert "sys.modules[__name__]" not in source
    assert ".__class__ =" not in source
