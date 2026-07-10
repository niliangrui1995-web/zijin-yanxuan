from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.services.runtime_health_service as runtime_health_service
import app.services.sector_runtime_service as sector_runtime_service
import domains
import domains.quotes as quotes


def test_runtime_health_service_exports_diagnostic_entrypoints():
    assert runtime_health_service.__all__ == ["collect_runtime_health", "export_runtime_health_report"]
    assert callable(runtime_health_service.collect_runtime_health)
    assert callable(runtime_health_service.export_runtime_health_report)


def test_sector_runtime_service_returns_sector_manager(monkeypatch):
    calls = []

    monkeypatch.setattr(
        sector_runtime_service.SectorManager,
        "get_instance",
        classmethod(lambda cls, root=None: calls.append(root) or {"root": root}),
    )

    assert sector_runtime_service.get_sector_manager("C:/zd_huatai") == {"root": "C:/zd_huatai"}
    assert calls == ["C:/zd_huatai"]


def test_domains_lazy_exports_resolve_and_cache(monkeypatch):
    domains.__dict__.pop("TaskCategory", None)
    fake_module = SimpleNamespace(TaskCategory="task-category")
    calls = []

    monkeypatch.setattr(domains, "import_module", lambda module_name: calls.append(module_name) or fake_module)

    assert domains.__getattr__("TaskCategory") == "task-category"
    assert domains.TaskCategory == "task-category"
    assert calls == ["domains.runtime"]


def test_domains_lazy_exports_reject_unknown_name():
    with pytest.raises(AttributeError, match="MissingName"):
        domains.__getattr__("MissingName")


def test_quotes_lazy_exports_resolve_and_reject_unknown(monkeypatch):
    quotes.__dict__.pop("has_valid_quote", None)
    fake_module = SimpleNamespace(has_valid_quote=lambda quote: True)
    calls = []

    monkeypatch.setattr(quotes, "import_module", lambda module_name: calls.append(module_name) or fake_module)

    assert quotes.__getattr__("has_valid_quote")({"close": 10}) is True
    assert calls == ["domains.quotes.dispatcher"]
    with pytest.raises(AttributeError, match="MissingQuoteExport"):
        quotes.__getattr__("MissingQuoteExport")
