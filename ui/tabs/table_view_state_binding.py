# -*- coding: utf-8 -*-
from __future__ import annotations

import logging

from app.services.ui_config_service import TableViewStateStore


def bind_table_view_state(
    owner, table, settings, header_state_savers: list, settings_key: str = "header_state"
) -> bool:
    """绑定表格列宽、列顺序和排序状态的自动恢复与防抖保存。"""
    try:
        store = TableViewStateStore(settings, settings_key=settings_key)
        return store.bind(owner, table, header_state_savers)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        logging.getLogger(__name__).warning(f"绑定表格状态失败 {settings_key}: {exc}")
        return False
