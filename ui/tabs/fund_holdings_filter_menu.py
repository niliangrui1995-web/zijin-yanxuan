# -*- coding: utf-8 -*-
"""Filter menu builders for the fund holdings tab."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QWidget


def build_change_filter_menu(
    menu: QMenu,
    parent: QWidget,
    *,
    all_key: str,
    options: Iterable[str],
    toggled_callback: Callable[[bool], None],
) -> dict[str, QAction]:
    menu.clear()
    actions: dict[str, QAction] = {}

    act_all = QAction("全部变化", parent)
    act_all.setCheckable(True)
    act_all.toggled.connect(toggled_callback)
    actions[all_key] = act_all
    menu.addAction(act_all)
    menu.addSeparator()

    for label in options:
        action = QAction(str(label), parent)
        action.setCheckable(True)
        action.toggled.connect(toggled_callback)
        actions[str(label)] = action
        menu.addAction(action)

    return actions


def build_quarter_filter_menu(
    menu: QMenu,
    parent: QWidget,
    *,
    latest_key: str,
    all_key: str,
    quarters: Iterable[str],
    toggled_callback: Callable[[bool], None],
) -> dict[str, QAction]:
    menu.clear()
    actions: dict[str, QAction] = {}

    for key, label in (
        (latest_key, "最新季度"),
        (all_key, "全部季度"),
    ):
        action = QAction(label, parent)
        action.setCheckable(True)
        action.toggled.connect(toggled_callback)
        actions[key] = action
        menu.addAction(action)

    quarter_list = [str(quarter or "").strip() for quarter in quarters if str(quarter or "").strip()]
    if quarter_list:
        menu.addSeparator()

    for quarter in quarter_list:
        action = QAction(quarter, parent)
        action.setCheckable(True)
        action.toggled.connect(toggled_callback)
        actions[quarter] = action
        menu.addAction(action)

    return actions
