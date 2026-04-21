from __future__ import annotations

from infra.market_data import as_market_data_ports


class _DummyProvider:
    code2name = {}
    cache_data = {}

    def fetch_realtime_quotes_batch(self, codes):
        return {code: {} for code in codes}

    def test_network(self, timeout=3):
        return True

    def set_online_mode(self, enabled):
        self.enabled = enabled

    def load_cache_from_disk(self):
        return ""

    def ensure_code_name_map(self, refresh_missing=True):
        return {}


class _DummyEngine:
    def full_scan(self, *args, **kwargs):
        return None

    def incremental_scan(self, *args, **kwargs):
        return None


def test_as_market_data_ports_returns_provider_and_engine_pair():
    provider = _DummyProvider()
    engine = _DummyEngine()

    ports = as_market_data_ports(provider, engine)

    assert ports.provider is provider
    assert ports.engine is engine
