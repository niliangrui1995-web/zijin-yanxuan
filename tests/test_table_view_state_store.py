# -*- coding: utf-8 -*-
from __future__ import annotations

from PyQt6.QtCore import Qt

from infra.settings import table_view_state_store as store_module


class _Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class _FakeTimer:
    single_shots = []

    def __init__(self, owner):
        self.owner = owner
        self.timeout = _Signal()
        self.starts = 0

    def setSingleShot(self, value):
        self.single_shot = value

    def setInterval(self, value):
        self.interval = value

    def start(self):
        self.starts += 1

    @staticmethod
    def singleShot(delay, callback):
        _FakeTimer.single_shots.append(delay)
        callback()


class _Settings:
    def __init__(self, values):
        self.values = dict(values)
        self.written = {}

    def contains(self, key):
        return key in self.values

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.written[key] = value

    def sync(self):
        self.synced = True


class _Header:
    def __init__(self, *, restore_error=None):
        self.restore_error = restore_error
        self.sectionResized = _Signal()
        self.sectionMoved = _Signal()
        self.sortIndicatorChanged = _Signal()

    def setDefaultAlignment(self, alignment):
        self.alignment = alignment

    def restoreState(self, _state):
        if self.restore_error is not None:
            raise self.restore_error

    def sortIndicatorOrder(self):
        return Qt.SortOrder.DescendingOrder

    def saveState(self):
        return b"state"


class _Table:
    def __init__(self, header):
        self._header = header

    def horizontalHeader(self):
        return self._header

    def sorted_column(self):
        return 2

    def sortByColumn(self, column, order):
        self.restored_sort = (column, order)


class _Owner:
    pass


def test_table_view_state_store_ignores_invalid_saved_header_and_sort(monkeypatch):
    monkeypatch.setattr(store_module, "QTimer", _FakeTimer)
    settings = _Settings(
        {
            "grid": b"bad-state",
            "grid/sort_column": "3",
            "grid/sort_order": "not-an-order",
        }
    )
    header = _Header(restore_error=RuntimeError("restore failed"))
    table = _Table(header)
    savers = []

    restored = store_module.TableViewStateStore(settings, settings_key="grid").bind(_Owner(), table, savers)

    assert restored is False
    assert len(savers) == 1


def test_table_view_state_store_saves_state_and_restores_sort(monkeypatch):
    _FakeTimer.single_shots = []
    monkeypatch.setattr(store_module, "QTimer", _FakeTimer)
    settings = _Settings(
        {
            "grid": b"state",
            "grid/sort_column": "4",
            "grid/sort_order": str(Qt.SortOrder.DescendingOrder.value),
        }
    )
    owner = _Owner()
    header = _Header()
    table = _Table(header)
    savers = []

    restored = store_module.TableViewStateStore(settings, settings_key="grid").bind(owner, table, savers)
    savers[0]()

    assert restored is True
    assert table.restored_sort == (4, Qt.SortOrder.DescendingOrder)
    assert settings.written["grid"] == b"state"
    assert settings.written["grid/sort_column"] == 2
    assert settings.written["grid/sort_order"] == Qt.SortOrder.DescendingOrder.value
    assert _FakeTimer.single_shots == [0]
    assert owner._header_save_timers


def test_table_view_state_store_ignores_save_and_restore_sort_errors(monkeypatch):
    _FakeTimer.single_shots = []
    monkeypatch.setattr(store_module, "QTimer", _FakeTimer)
    settings = _Settings(
        {
            "grid": b"state",
            "grid/sort_column": "1",
            "grid/sort_order": str(Qt.SortOrder.AscendingOrder.value),
        }
    )

    class BadTable(_Table):
        def sorted_column(self):
            raise RuntimeError("save failed")

        def sortByColumn(self, _column, _order):
            raise RuntimeError("sort failed")

    savers = []
    restored = store_module.TableViewStateStore(settings, settings_key="grid").bind(
        _Owner(),
        BadTable(_Header()),
        savers,
    )

    savers[0]()

    assert restored is True
    assert settings.written == {}
