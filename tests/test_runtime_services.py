from __future__ import annotations

import threading

from app.services import runtime_services


def test_native_dataframe_runtime_initializes_once_in_safe_order(monkeypatch):
    imported = []
    monkeypatch.setattr(runtime_services, "_native_dataframe_runtime_ready", False)
    monkeypatch.setattr(
        runtime_services.importlib,
        "import_module",
        lambda name: imported.append(name) or object(),
    )

    runtime_services.initialize_native_dataframe_runtime()
    runtime_services.initialize_native_dataframe_runtime()

    assert imported == ["pandas", "polars"]
    assert runtime_services.is_native_dataframe_runtime_ready() is True


def test_native_dataframe_runtime_rejects_first_import_from_worker_thread(monkeypatch):
    imported = []
    errors = []
    monkeypatch.setattr(runtime_services, "_native_dataframe_runtime_ready", False)
    monkeypatch.setattr(
        runtime_services.importlib,
        "import_module",
        lambda name: imported.append(name) or object(),
    )

    thread = threading.Thread(
        target=lambda: _capture_runtime_error(
            runtime_services.initialize_native_dataframe_runtime,
            errors,
        )
    )
    thread.start()
    thread.join(timeout=5)

    assert thread.is_alive() is False
    assert imported == []
    assert len(errors) == 1
    assert "main thread" in str(errors[0])
    assert runtime_services.is_native_dataframe_runtime_ready() is False


def test_search_filter_runtime_initializes_once_on_main_thread(monkeypatch):
    imported = []
    monkeypatch.setattr(runtime_services, "_search_filter_runtime_ready", False)
    monkeypatch.setattr(
        runtime_services.importlib,
        "import_module",
        lambda name: imported.append(name) or object(),
    )

    runtime_services.initialize_search_filter_runtime()
    runtime_services.initialize_search_filter_runtime()

    assert imported == ["pypinyin"]
    assert runtime_services.is_search_filter_runtime_ready() is True


def test_search_filter_runtime_rejects_first_import_from_worker_thread(monkeypatch):
    imported = []
    errors = []
    monkeypatch.setattr(runtime_services, "_search_filter_runtime_ready", False)
    monkeypatch.setattr(
        runtime_services.importlib,
        "import_module",
        lambda name: imported.append(name) or object(),
    )

    thread = threading.Thread(
        target=lambda: _capture_runtime_error(
            runtime_services.initialize_search_filter_runtime,
            errors,
        )
    )
    thread.start()
    thread.join(timeout=5)

    assert thread.is_alive() is False
    assert imported == []
    assert len(errors) == 1
    assert "main thread" in str(errors[0])
    assert runtime_services.is_search_filter_runtime_ready() is False


def _capture_runtime_error(callback, errors):
    try:
        callback()
    except RuntimeError as exc:
        errors.append(exc)


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
