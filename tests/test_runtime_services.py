from __future__ import annotations

from app.services import runtime_services


def test_create_data_provider_prefers_cached_code_names(monkeypatch):
    class _Provider:
        def __init__(self, *, offline=True):
            self.offline = offline
            self.code2name = {}
            self.ensure_calls = 0

        def load_cached_code_name_map(self):
            return {"000001": "Ping An"}

        def ensure_code_name_map(self):
            self.ensure_calls += 1
            return {"600519": "Moutai"}

    created = []
    monkeypatch.setattr(
        runtime_services, "TdxDataProvider", lambda **kwargs: created.append(_Provider(**kwargs)) or created[-1]
    )

    provider = runtime_services.create_data_provider(offline=True)

    assert provider.code2name == {"000001": "Ping An"}
    assert provider.ensure_calls == 0


def test_create_data_provider_falls_back_to_full_name_map_when_cache_missing(monkeypatch):
    class _Provider:
        def __init__(self, *, offline=True):
            self.offline = offline
            self.code2name = {}
            self.ensure_calls = 0

        def load_cached_code_name_map(self):
            return {}

        def ensure_code_name_map(self):
            self.ensure_calls += 1
            return {"600519": "Moutai"}

    created = []
    monkeypatch.setattr(
        runtime_services, "TdxDataProvider", lambda **kwargs: created.append(_Provider(**kwargs)) or created[-1]
    )

    provider = runtime_services.create_data_provider(offline=True)

    assert provider.code2name == {"600519": "Moutai"}
    assert provider.ensure_calls == 1
