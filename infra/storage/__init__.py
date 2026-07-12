# -*- coding: utf-8 -*-
"""Lazy storage exports; importing a repository must not open SQLite."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infra.storage.data_store import DataStore, data_store, resolve_data_store_path

__all__ = ["DataStore", "data_store", "resolve_data_store_path"]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)
    value = getattr(import_module("infra.storage.data_store"), name)
    globals()[name] = value
    return value
