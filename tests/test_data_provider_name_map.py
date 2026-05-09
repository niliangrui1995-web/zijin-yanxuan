from __future__ import annotations

from vcp.data_provider_history_mixin import TdxDataProviderHistoryMixin


class _Store:
    def __init__(self):
        self.saved_payloads = []

    def load_json(self, *_args, **_kwargs):
        return {
            "000001": "Ping An",
            "000002": "000002",
            "600519": "600519",
        }

    def save_json(self, _key, payload):
        self.saved_payloads.append(dict(payload))


class _Provider(TdxDataProviderHistoryMixin):
    def __init__(self):
        self.code2name = {"300750": "CATL"}
        self._offline = False
        self.quote_calls = []

    def _get_codes_from_vipdoc(self):
        raise AssertionError("targeted code-name refresh should not scan all vipdoc files")

    def _load_local_tdx_name_map_for_codes(self, codes):
        return {"600519": "Moutai"}

    def fetch_realtime_quotes_batch(self, codes):
        self.quote_calls.append(list(codes))
        return {"000002": {"name": "Vanke"}}


def test_ensure_code_name_map_with_targets_avoids_full_vipdoc_scan(monkeypatch):
    store = _Store()
    monkeypatch.setattr("core.data_store.DataStore", lambda: store)
    provider = _Provider()

    result = provider.ensure_code_name_map(
        ["000001", "600519", "000002", "00700", "not-a-code"],
        refresh_missing=True,
    )

    assert result["000001"] == "Ping An"
    assert result["600519"] == "Moutai"
    assert result["000002"] == "Vanke"
    assert result["300750"] == "CATL"
    assert "00700" not in result
    assert provider.quote_calls == [["000002"]]
    assert store.saved_payloads[-1]["000002"] == "Vanke"


def test_load_cached_code_name_map_avoids_vipdoc_and_local_master_scan(monkeypatch):
    store = _Store()
    monkeypatch.setattr("core.data_store.DataStore", lambda: store)
    provider = _Provider()

    result = provider.load_cached_code_name_map()

    assert result["000001"] == "Ping An"
    assert result["000002"] == "000002"
    assert result["600519"] == "600519"
    assert result["603196"] == "璞源材料"
    assert provider.code2name == result
