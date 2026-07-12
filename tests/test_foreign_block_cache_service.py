from __future__ import annotations

import pytest

from app.services import foreign_block_cache_service
from core.exceptions import DataFormatError
from infra.storage import foreign_block_repository


def test_foreign_block_cache_service_builds_and_saves_schema(monkeypatch):
    saved = []
    monkeypatch.setattr(
        foreign_block_cache_service.foreign_block_repository,
        "save_foreign_block_cache_payload",
        saved.append,
    )

    payload = foreign_block_cache_service.save_foreign_block_cache(
        [
            {"代码": "600000", "交易日期": "2026-05-07"},
            {"代码": "000001", "交易日期": "2026-05-08"},
        ],
        days_to_fetch=30,
    )

    assert payload["days_to_fetch"] == 30
    assert payload["latest_trade_date"] == "2026-05-08"
    assert payload["saved_at"]
    assert saved == [payload]


def test_foreign_block_cache_service_loads_and_filters_rows(monkeypatch):
    monkeypatch.setattr(
        foreign_block_cache_service.foreign_block_repository,
        "load_foreign_block_cache_payload",
        lambda: {
            "saved_at": "2026-05-08T20:00:00",
            "days_to_fetch": 30,
            "latest_trade_date": "2026-05-08",
            "rows": [{"代码": "600000"}, {"代码": "000001"}],
        },
    )

    payload = foreign_block_cache_service.load_foreign_block_cache(
        row_filter=lambda rows: [row for row in rows if row["代码"] == "600000"]
    )

    assert payload["raw_count"] == 2
    assert payload["rows"] == [{"代码": "600000"}]
    assert payload["latest_trade_date"] == "2026-05-08"


def test_foreign_block_repository_owns_path_and_rejects_invalid_rows(monkeypatch):
    captured_paths = []
    monkeypatch.setattr(
        foreign_block_repository.json_cache_repository,
        "load_json_file",
        lambda path: captured_paths.append(path) or {"rows": "invalid"},
    )

    with pytest.raises(DataFormatError, match="rows invalid"):
        foreign_block_repository.load_foreign_block_cache_payload()

    assert len(captured_paths) == 1
    assert captured_paths[0].replace("\\", "/").endswith("/data/Cache/foreign_block_trade_latest.json")
