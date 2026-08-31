# -*- coding: utf-8 -*-
"""Windows commit-headroom admission control for memory-intensive F5 work."""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass

MEBIBYTE = 1024**2
GIBIBYTE = 1024**3
F5_WORKER_START_MIN_COMMIT_HEADROOM_BYTES = 2 * GIBIBYTE
F5_FULL_REREAD_MIN_COMMIT_HEADROOM_BYTES = 3 * GIBIBYTE
# Parent activation temporarily holds both the active cache and the decoded
# generation, so it needs a larger admission margin than the worker stages.
F5_ACTIVATION_LOAD_MIN_COMMIT_HEADROOM_BYTES = 4 * GIBIBYTE


class _PerformanceInformation(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("CommitTotal", ctypes.c_size_t),
        ("CommitLimit", ctypes.c_size_t),
        ("CommitPeak", ctypes.c_size_t),
        ("PhysicalTotal", ctypes.c_size_t),
        ("PhysicalAvailable", ctypes.c_size_t),
        ("SystemCache", ctypes.c_size_t),
        ("KernelTotal", ctypes.c_size_t),
        ("KernelPaged", ctypes.c_size_t),
        ("KernelNonpaged", ctypes.c_size_t),
        ("PageSize", ctypes.c_size_t),
        ("HandleCount", ctypes.c_uint32),
        ("ProcessCount", ctypes.c_uint32),
        ("ThreadCount", ctypes.c_uint32),
    ]


@dataclass(frozen=True)
class CommitHeadroom:
    commit_total_bytes: int
    commit_limit_bytes: int
    headroom_bytes: int


class F5MemoryPressureError(RuntimeError):
    """Raised before a F5 stage that would exceed the system commit budget."""

    error_code = "insufficient_memory_headroom"

    def __init__(self, *, stage: str, headroom_bytes: int, minimum_bytes: int) -> None:
        self.stage = str(stage or "F5")
        self.headroom_bytes = max(0, int(headroom_bytes or 0))
        self.minimum_bytes = max(0, int(minimum_bytes or 0))
        super().__init__(
            f"{self.stage}未执行：系统提交内存余量仅 {self.headroom_bytes // MEBIBYTE} MB，"
            f"低于安全阈值 {self.minimum_bytes // MEBIBYTE} MB。"
            "请关闭占用内存的程序或增加 Windows 页面文件后重试。"
        )


def _read_system_commit_bytes() -> tuple[int, int] | None:
    """Return ``(commit_total_bytes, commit_limit_bytes)`` on Windows.

    ``CommitLimit - CommitTotal`` is the system-wide commit headroom.  It is
    deliberately used instead of physical-RAM availability because page-file
    exhaustion can terminate allocations while RAM still appears available.
    """

    if os.name != "nt":
        return None
    try:
        info = _PerformanceInformation()
        info.cb = ctypes.sizeof(info)
        get_performance_info = ctypes.WinDLL("psapi", use_last_error=True).GetPerformanceInfo
        get_performance_info.argtypes = (ctypes.POINTER(_PerformanceInformation), ctypes.c_uint32)
        get_performance_info.restype = ctypes.c_int
        if not get_performance_info(ctypes.byref(info), ctypes.sizeof(info)):
            return None
        page_size = int(info.PageSize or 0)
        total_pages = int(info.CommitTotal or 0)
        limit_pages = int(info.CommitLimit or 0)
        if page_size <= 0 or total_pages < 0 or limit_pages <= 0:
            return None
        return total_pages * page_size, limit_pages * page_size
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def read_system_commit_headroom() -> CommitHeadroom | None:
    values = _read_system_commit_bytes()
    if values is None:
        return None
    commit_total_bytes, commit_limit_bytes = values
    return CommitHeadroom(
        commit_total_bytes=max(0, int(commit_total_bytes)),
        commit_limit_bytes=max(0, int(commit_limit_bytes)),
        headroom_bytes=max(0, int(commit_limit_bytes) - int(commit_total_bytes)),
    )


def ensure_f5_commit_headroom(minimum_bytes: int, *, stage: str) -> CommitHeadroom | None:
    """Fail closed only when Windows can prove the F5 budget is insufficient."""

    snapshot = read_system_commit_headroom()
    threshold = max(0, int(minimum_bytes or 0))
    if snapshot is not None and snapshot.headroom_bytes < threshold:
        raise F5MemoryPressureError(
            stage=stage,
            headroom_bytes=snapshot.headroom_bytes,
            minimum_bytes=threshold,
        )
    return snapshot


__all__ = [
    "CommitHeadroom",
    "F5_ACTIVATION_LOAD_MIN_COMMIT_HEADROOM_BYTES",
    "F5MemoryPressureError",
    "F5_FULL_REREAD_MIN_COMMIT_HEADROOM_BYTES",
    "F5_WORKER_START_MIN_COMMIT_HEADROOM_BYTES",
    "ensure_f5_commit_headroom",
    "read_system_commit_headroom",
]
