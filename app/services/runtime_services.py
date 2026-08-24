# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import threading
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.bootstrap.startup_orchestrator import StartupHostPort, StartupOrchestrator
    from infra.market_data.tdx_data_provider import TdxDataProvider as TdxDataProviderType

# Public test seams remain patchable without importing the heavy provider at
# module import time.  ``None`` means "resolve the production implementation".
TdxDataProvider = None

_native_dataframe_runtime_lock = threading.Lock()
_native_dataframe_runtime_ready = False
# Pandas imports the compatible PyArrow runtime itself.  Importing PyArrow
# separately here repeats the same cold-start work before the first window.
_NATIVE_DATAFRAME_MODULES = ("pandas", "polars")
_search_filter_runtime_lock = threading.Lock()
_search_filter_runtime_ready = False


def is_native_dataframe_runtime_ready() -> bool:
    return _native_dataframe_runtime_ready


def initialize_native_dataframe_runtime() -> None:
    """Load native dataframe libraries once, and never first-load them in a worker."""

    global _native_dataframe_runtime_ready
    if _native_dataframe_runtime_ready:
        return
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("native dataframe runtime must be initialized on the main thread")
    with _native_dataframe_runtime_lock:
        if _native_dataframe_runtime_ready:
            return
        for module_name in _NATIVE_DATAFRAME_MODULES:
            importlib.import_module(module_name)
        _native_dataframe_runtime_ready = True


def is_search_filter_runtime_ready() -> bool:
    return _search_filter_runtime_ready


def initialize_search_filter_runtime() -> None:
    """Load the GIL-heavy pinyin dictionary before the UI becomes interactive."""

    global _search_filter_runtime_ready
    if _search_filter_runtime_ready:
        return
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("search filter runtime must be initialized on the main thread")
    with _search_filter_runtime_lock:
        if _search_filter_runtime_ready:
            return
        importlib.import_module("pypinyin")
        _search_filter_runtime_ready = True


def _load_snapshot(codes: Iterable[str], tdx_vipdoc: str | None) -> dict[str, dict]:
    from infra.market_data.vcp_scan_adapter import load_local_tdx_capital_snapshot

    return load_local_tdx_capital_snapshot(codes, tdx_vipdoc)


def create_data_provider(*, offline: bool = True) -> TdxDataProviderType:
    provider_factory = TdxDataProvider
    if provider_factory is None:
        initialize_native_dataframe_runtime()
        from infra.market_data.tdx_data_provider import TdxDataProvider as provider_factory

    provider = provider_factory(offline=offline)
    cached_code_names: dict = {}
    load_cached_code_name_map = getattr(provider, "load_cached_code_name_map", None)
    if callable(load_cached_code_name_map):
        loaded = load_cached_code_name_map()
        cached_code_names = loaded if isinstance(loaded, dict) else {}
    if cached_code_names:
        provider.code2name = cached_code_names
    else:
        provider_code_names = provider.ensure_code_name_map()
        provider.code2name = provider_code_names if isinstance(provider_code_names, dict) else {}
    return provider


def create_startup_orchestrator(main_window: StartupHostPort, job_runner: Any = None) -> StartupOrchestrator:
    from app.bootstrap.startup_orchestrator import StartupHostAdapter, StartupOrchestrator

    return StartupOrchestrator(job_runner=job_runner, host=StartupHostAdapter(main_window))


def load_local_tdx_capital_snapshot(codes: Iterable[str], tdx_vipdoc: str | None) -> dict[str, dict]:
    return _load_snapshot(codes, tdx_vipdoc)
