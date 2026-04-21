# -*- coding: utf-8 -*-
from __future__ import annotations

from vcp.sector import SectorManager


def get_sector_manager(tdx_root: str | None = None) -> SectorManager:
    return SectorManager.get_instance(tdx_root)


__all__ = ["get_sector_manager"]
