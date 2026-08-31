# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from core import f5_resource_guard as guard


def test_commit_headroom_guard_rejects_low_capacity_and_fails_open_when_unavailable(monkeypatch):
    gib = 1024**3
    monkeypatch.setattr(guard, "_read_system_commit_bytes", lambda: (9 * gib, 10 * gib))

    with pytest.raises(guard.F5MemoryPressureError) as captured:
        guard.ensure_f5_commit_headroom(2 * gib, stage="F5 启动")

    assert captured.value.error_code == "insufficient_memory_headroom"
    assert "F5 启动" in str(captured.value)
    assert "1024 MB" in str(captured.value)

    monkeypatch.setattr(guard, "_read_system_commit_bytes", lambda: None)
    guard.ensure_f5_commit_headroom(99 * gib, stage="F5 启动")


def test_commit_headroom_snapshot_uses_commit_limit_minus_total(monkeypatch):
    gib = 1024**3
    monkeypatch.setattr(guard, "_read_system_commit_bytes", lambda: (6 * gib, 10 * gib))

    snapshot = guard.read_system_commit_headroom()

    assert snapshot is not None
    assert snapshot.commit_total_bytes == 6 * gib
    assert snapshot.commit_limit_bytes == 10 * gib
    assert snapshot.headroom_bytes == 4 * gib
