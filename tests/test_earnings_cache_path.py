from __future__ import annotations

from pathlib import Path

from core.runtime_paths import PROJECT_ROOT
from domains.earnings.engine import resolve_legacy_earnings_cache_path


def test_legacy_earnings_cache_path_is_anchored_to_project_data_directory():
    assert Path(resolve_legacy_earnings_cache_path("data/earnings_state.json")) == (
        Path(PROJECT_ROOT) / "data" / "earnings_state.json"
    ).resolve()


def test_legacy_earnings_cache_path_preserves_an_absolute_override(tmp_path):
    override = tmp_path / "legacy-state.json"

    assert Path(resolve_legacy_earnings_cache_path(override)) == override.resolve()
