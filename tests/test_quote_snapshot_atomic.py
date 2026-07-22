# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
from collections.abc import Mapping
from types import SimpleNamespace

import pytest

from core.global_store import GlobalStore
from core.observability import metric_history
from core.state.quote_snapshot import QuoteSnapshot, snapshot_to_mutable_dict
from ui.tabs.tab_quote_bridge import apply_quote_snapshot
from ui.tabs.watchlist_tab import _copy_quote_snapshot


def test_quote_snapshot_supports_empty_data() -> None:
    store = GlobalStore()

    snapshot = store.get_latest_quotes()

    assert isinstance(snapshot, QuoteSnapshot)
    assert isinstance(snapshot, Mapping)
    assert snapshot.version == 0
    assert snapshot.timestamp > 0
    assert snapshot.quotes == {}


def test_quote_snapshot_replace_keeps_old_snapshot_immutable() -> None:
    store = GlobalStore()
    old_snapshot = store.get_latest_quotes()

    new_snapshot = store.replace_quotes({"000001": {"close": 12.5}})

    assert store.get_latest_quotes() is new_snapshot
    assert new_snapshot.version == old_snapshot.version + 1
    assert old_snapshot == {}
    assert new_snapshot["000001"]["close"] == 12.5
    with pytest.raises(TypeError):
        new_snapshot.quotes["000002"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        new_snapshot["000001"]["close"] = 13.0  # type: ignore[index]


def test_quote_snapshot_remains_compatible_with_watchlist_copy_boundary() -> None:
    snapshot = QuoteSnapshot.create(version=1, timestamp=1, quotes={"000001": {"close": 12.5}})

    assert _copy_quote_snapshot(snapshot) == {"000001": {"close": 12.5}}


def test_quote_snapshot_crosses_mapping_consumer_without_mutable_conversion() -> None:
    snapshot = QuoteSnapshot.create(
        version=1,
        timestamp=1,
        quotes={
            "000001": {"close": 12.5},
            "600519": {"close": 1500.0},
        },
    )

    class _Model:
        row_data = [{"代码": "000001"}]

        def __init__(self) -> None:
            self.calls: list[Mapping[str, Mapping[str, object]]] = []

        def update_quotes(self, quotes):
            self.calls.append(quotes)
            return 1

    model = _Model()
    stats = apply_quote_snapshot(SimpleNamespace(model=model), snapshot)

    assert stats["payload_codes"] == 2
    assert stats["applied_codes"] == 1
    assert stats["changed_rows"] == 1
    assert tuple(model.calls[0]) == ("000001",)
    assert model.calls[0]["000001"] is snapshot["000001"]
    with pytest.raises(TypeError):
        model.calls[0]["000001"]["close"] = 13.0  # type: ignore[index]


def test_quote_snapshot_mutable_copy_is_filtered_and_detached() -> None:
    snapshot = QuoteSnapshot.create(
        version=1,
        timestamp=1,
        quotes={
            "000001": {"close": 12.5, "nested": {"items": [{"value": 1}]}},
            "600519": {"close": 1500.0},
        },
    )

    copied = snapshot_to_mutable_dict(snapshot, codes={"000001"})
    copied["000001"]["close"] = 13.0
    nested = copied["000001"]["nested"]
    assert isinstance(nested, dict)
    items = nested["items"]
    assert isinstance(items, tuple)
    assert isinstance(items[0], dict)
    items[0]["value"] = 2

    assert copied.keys() == {"000001"}
    assert snapshot["000001"]["close"] == 12.5
    assert snapshot["000001"]["nested"]["items"][0]["value"] == 1  # type: ignore[index]


def test_quote_snapshot_concurrent_readers_only_see_complete_replacements() -> None:
    store = GlobalStore()
    reader_count = 6
    writes = 300
    start = threading.Barrier(reader_count + 1)
    finished = threading.Event()
    all_readers_observed = threading.Event()
    errors: list[str] = []
    errors_lock = threading.Lock()
    observed: set[int] = set()

    def reader(reader_id: int) -> None:
        start.wait()
        while not finished.is_set():
            snapshot = store.get_latest_quotes()
            payload = snapshot.get("000001")
            if payload is None:
                continue
            if payload.get("version") != snapshot.version:
                with errors_lock:
                    errors.append(f"partial snapshot: {snapshot.version}/{payload!r}")
                return
            with errors_lock:
                observed.add(reader_id)
                if len(observed) == reader_count:
                    all_readers_observed.set()

    threads = [threading.Thread(target=reader, args=(reader_id,)) for reader_id in range(reader_count)]
    for thread in threads:
        thread.start()

    start.wait()
    snapshot = store.replace_quotes({"000001": {"version": 1}})
    assert snapshot.version == 1
    assert all_readers_observed.wait(2)
    for version in range(2, writes + 1):
        snapshot = store.replace_quotes({"000001": {"version": version}})
        assert snapshot.version == version
    finished.set()

    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert errors == []
    assert observed == set(range(reader_count))


def test_quote_snapshot_rejects_abnormal_data_without_replacing_current_snapshot() -> None:
    store = GlobalStore()
    original = store.replace_quotes({"000001": {"close": 10.0}})

    with pytest.raises(TypeError, match="must be a mapping"):
        store.merge_quotes({"000001": object()})

    assert store.get_latest_quotes() is original
    assert store.merge_quotes(None) is original

    with pytest.raises(ValueError, match="version"):
        QuoteSnapshot(version=-1, timestamp=0, quotes={})
    with pytest.raises(ValueError, match="timestamp"):
        QuoteSnapshot(version=1, timestamp=float("nan"), quotes={})


def test_quote_snapshot_detaches_mutable_binary_leaf_values() -> None:
    store = GlobalStore()
    raw = bytearray(b"quote")

    snapshot = store.replace_quotes({"000001": {"raw": raw}})
    raw[0] = ord("Q")

    assert snapshot["000001"]["raw"] == b"quote"


def test_quote_snapshot_freezes_nested_mutable_containers() -> None:
    nested = {"items": [{"value": 1}]}
    labels = {"live", "eastmoney"}
    raw = {
        "nested": nested,
        "labels": labels,
        "buffer": memoryview(bytearray(b"quote")),
    }

    snapshot = QuoteSnapshot.create(version=1, timestamp=1, quotes={"000001": raw})
    nested["items"][0]["value"] = 9
    labels.add("late")

    quote = snapshot["000001"]
    assert quote["nested"]["items"][0]["value"] == 1  # type: ignore[index]
    assert quote["labels"] == frozenset({"live", "eastmoney"})
    assert quote["buffer"] == b"quote"


def test_quote_snapshot_unknown_leaf_is_quarantined_by_default_and_rejected_in_strict_mode() -> None:
    class MutableLeaf:
        pass

    leaf = MutableLeaf()
    snapshot = QuoteSnapshot.create(
        version=1,
        timestamp=1,
        quotes={"000001": {"close": 12.5, "custom": leaf}},
    )

    assert snapshot["000001"] == {"close": 12.5}
    assert "custom" not in snapshot["000001"]
    assert snapshot.unknown_leaf_count == 1
    assert snapshot.unknown_leaf_types == (
        f"{MutableLeaf.__module__}.{MutableLeaf.__qualname__}",
    )
    with pytest.raises(TypeError, match="unsupported mutable quote leaf type"):
        QuoteSnapshot.create(
            version=2,
            timestamp=2,
            quotes={"000001": {"custom": leaf}},
            strict_values=True,
        )


def test_quote_snapshot_canonicalizes_scalar_subclasses_without_retaining_identity() -> None:
    class MutableInt(int):
        pass

    class MutableText(str):
        pass

    raw_number = MutableInt(12)
    raw_text = MutableText("eastmoney")

    snapshot = QuoteSnapshot.create(
        version=1,
        timestamp=1,
        quotes={"000001": {"volume": raw_number, "source": raw_text}},
    )

    assert snapshot["000001"]["volume"] == 12
    assert type(snapshot["000001"]["volume"]) is int
    assert snapshot["000001"]["volume"] is not raw_number
    assert snapshot["000001"]["source"] == "eastmoney"
    assert type(snapshot["000001"]["source"]) is str
    assert snapshot["000001"]["source"] is not raw_text
    assert snapshot.unknown_leaf_count == 0


def test_quote_snapshot_canonicalization_cannot_be_overridden_by_scalar_subclasses() -> None:
    class StickyText(str):
        def __str__(self):
            return self

    class StickyBytes(bytes):
        def __bytes__(self):
            return self

    class StickyByteArray(bytearray):
        def __init__(self, value, shared):
            super().__init__(value)
            self.shared = shared

        def __bytes__(self):
            return self.shared

    raw_text = StickyText("eastmoney")
    raw_bytes = StickyBytes(b"quote")
    raw_buffer = StickyByteArray(b"buffer", raw_bytes)

    for strict_values in (False, True):
        snapshot = QuoteSnapshot.create(
            version=1,
            timestamp=1,
            quotes={
                "000001": {
                    "source": raw_text,
                    "raw": raw_bytes,
                    "buffer": raw_buffer,
                }
            },
            strict_values=strict_values,
        )

        assert snapshot["000001"]["source"] == "eastmoney"
        assert type(snapshot["000001"]["source"]) is str
        assert snapshot["000001"]["source"] is not raw_text
        assert snapshot["000001"]["raw"] == b"quote"
        assert type(snapshot["000001"]["raw"]) is bytes
        assert snapshot["000001"]["raw"] is not raw_bytes
        assert snapshot["000001"]["buffer"] == b"buffer"
        assert type(snapshot["000001"]["buffer"]) is bytes
        assert snapshot["000001"]["buffer"] is not raw_bytes


def test_quote_snapshot_canonicalizes_outer_keys_and_version_to_exact_builtin_types() -> None:
    class StickyText(str):
        def __str__(self):
            return self

    class MutableVersion(int):
        pass

    raw_code = StickyText("000001")
    raw_field = StickyText("close")
    raw_version = MutableVersion(7)

    snapshot = QuoteSnapshot.create(
        version=raw_version,
        timestamp=1,
        quotes={raw_code: {raw_field: 12.5}},
    )

    code = next(iter(snapshot.quotes))
    field = next(iter(snapshot[code]))
    assert code == "000001"
    assert type(code) is str
    assert code is not raw_code
    assert field == "close"
    assert type(field) is str
    assert field is not raw_field
    assert snapshot.version == 7
    assert type(snapshot.version) is int
    assert snapshot.version is not raw_version


def test_quote_snapshot_quarantines_nested_mapping_with_unknown_key() -> None:
    class MutableHashableKey:
        pass

    custom_key = MutableHashableKey()
    snapshot = QuoteSnapshot.create(
        version=1,
        timestamp=1,
        quotes={
            "000001": {
                "close": 12.5,
                "extension": {custom_key: "unsafe", "safe": "value"},
            }
        },
    )

    assert snapshot["000001"] == {"close": 12.5}
    assert "extension" not in snapshot["000001"]
    assert snapshot.unknown_leaf_count == 1
    assert snapshot.unknown_leaf_types == (
        f"{MutableHashableKey.__module__}.{MutableHashableKey.__qualname__}",
    )

    with pytest.raises(TypeError, match="unsupported mutable quote leaf type"):
        QuoteSnapshot.create(
            version=2,
            timestamp=2,
            quotes={"000001": {"extension": {custom_key: "unsafe"}}},
            strict_values=True,
        )


def test_quote_snapshot_quarantines_set_item_that_cannot_remain_hashable() -> None:
    class HashableMapping(dict):
        __hash__ = object.__hash__  # type: ignore[assignment]

    mapping_item = HashableMapping({"source": "custom"})
    snapshot = QuoteSnapshot.create(
        version=1,
        timestamp=1,
        quotes={"000001": {"close": 12.5, "extension": {mapping_item}}},
    )

    assert snapshot["000001"] == {"close": 12.5}
    assert snapshot.unknown_leaf_count == 1
    assert "HashableMapping" in snapshot.unknown_leaf_types[0]
    with pytest.raises(TypeError, match="unsupported mutable quote leaf type"):
        QuoteSnapshot.create(
            version=2,
            timestamp=2,
            quotes={"000001": {"extension": {mapping_item}}},
            strict_values=True,
        )


def test_snapshot_to_mutable_dict_sanitizes_raw_mapping_input() -> None:
    class MutableLeaf:
        pass

    leaf = MutableLeaf()
    copied = snapshot_to_mutable_dict(
        {"000001": {"close": 12.5, "custom": leaf}},
    )

    assert copied == {"000001": {"close": 12.5}}
    assert all(value is not leaf for payload in copied.values() for value in payload.values())


def test_global_store_merge_quarantine_preserves_previous_safe_field() -> None:
    class MutableLeaf:
        pass

    store = GlobalStore()
    store.replace_quotes({"000001": {"close": 10.0, "source": "old"}})
    before = len(metric_history("quote_snapshot_mutable_leaf_detected"))

    snapshot = store.merge_quotes(
        {"000001": {"close": MutableLeaf(), "source": "new"}},
    )
    samples = metric_history("quote_snapshot_mutable_leaf_detected")

    assert snapshot["000001"] == {"close": 10.0, "source": "new"}
    assert snapshot.unknown_leaf_count == 0
    assert len(samples) == before + 1
    assert samples[-1].tags["operation"] == "merge"


def test_global_store_records_unknown_quote_leaf_quarantine_metric_after_install() -> None:
    class ObservableMutableLeaf:
        pass

    store = GlobalStore()
    before = len(metric_history("quote_snapshot_mutable_leaf_detected"))

    leaf = ObservableMutableLeaf()
    installed = store.replace_quotes({"000001": {"close": 12.5, "custom": leaf}})
    samples = metric_history("quote_snapshot_mutable_leaf_detected")

    assert store.get_latest_quotes() is installed
    assert installed["000001"] == {"close": 12.5}
    assert "custom" not in installed["000001"]
    assert len(samples) == before + 1
    assert samples[-1].value == 1
    assert samples[-1].tags["operation"] == "replace"
    assert "ObservableMutableLeaf" in samples[-1].tags["types"]
