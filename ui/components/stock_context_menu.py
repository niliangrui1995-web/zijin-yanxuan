# -*- coding: utf-8 -*-
"""
ui/components/stock_context_menu.py
股票右键菜单工厂 — 统一四处重复的右键菜单逻辑 (#2)

为什么要统一？
原先 MainWindow / ScanTab / WatchlistTab 各写了一份
几乎一模一样的右键菜单，新增菜单项要改 4 处。
现在用工厂模式，各 Tab 只需 3 行代码调用。
"""

import os
import re
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QCursor, QDesktopServices
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox

from app.services.ui_event_service import ui_signals as event_bus
from app.services.ui_navigation_service import open_codex_desktop_thread as launch_codex_desktop_thread
from app.services.ui_watchlist_service import watchlist_vm
from core.logger import get_logger
from core.observability import emit_structured_log, record_metric
from ui.components.motion import install_menu_fade
from ui.styles.context_menu_qss import generate_context_menu_qss

CODEX_INDUSTRY_RESEARCH_PROJECT = Path(r"D:\vcp_hunter\产业链投研")
CODEX_NEW_THREAD_ROUTE = "codex://new"
CODEX_PROMPT_MAX_LENGTH = 800
CODEX_STOCK_FIELD_MAX_LENGTH = 80
CODEX_STOCK_PROMPT_INTRO = "深度研究"
CODEX_CURRENT_STOCK_PROMPT = "深度研究 当前股票"
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")
_WHITESPACE_RE = re.compile(r"\s+")
_STOCK_CODE_RE = re.compile(r"(?:[A-Za-z]{2})?(\d{6})(?:\.[A-Za-z]{2})?")
_CODEX_URL_OPEN_ERRORS = (OSError, RuntimeError, webbrowser.Error)
_log = get_logger(__name__)


def _normalize_text(value, *, max_length: int | None = None) -> str:
    text = _CONTROL_CHAR_RE.sub(" ", str(value or ""))
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if max_length is not None and len(text) > max_length:
        text = text[:max_length].rstrip()
    return text


def _clean_stock_code(code: str) -> str:
    text = _normalize_text(code, max_length=CODEX_STOCK_FIELD_MAX_LENGTH)
    match = _STOCK_CODE_RE.search(text)
    if match:
        return match.group(1)
    return "".join(ch for ch in text if ch.isalnum() or ch in ".-_+")[:CODEX_STOCK_FIELD_MAX_LENGTH]


def _clean_stock_name(name: str, *, max_length: int | None = None) -> str:
    text = str(name or "").replace("\r\n", "\n").replace("\r", "\n").split("\n", 1)[0]
    text = _normalize_text(text, max_length=max_length)
    while text.startswith(("⭐", "★")):
        text = text[1:].strip()
    return text


def _clean_codex_prompt(prompt: str) -> str:
    text = str(prompt or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHAR_RE.sub(" ", text)
    lines = [_normalize_text(line) for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)
    if len(text) > CODEX_PROMPT_MAX_LENGTH:
        text = text[:CODEX_PROMPT_MAX_LENGTH].rstrip()
    return text


def build_codex_stock_prompt(code: str, name: str) -> str:
    code = _clean_stock_code(code)
    name = _clean_stock_name(name, max_length=CODEX_STOCK_FIELD_MAX_LENGTH)
    target = name or code
    if target:
        suffix = "" if target.endswith("股票") else "股票"
        return f"{CODEX_STOCK_PROMPT_INTRO} {target}{suffix}"
    return CODEX_CURRENT_STOCK_PROMPT


def build_codex_project_thread_url(
    project_path: str | Path = CODEX_INDUSTRY_RESEARCH_PROJECT,
    *,
    prompt: str | None = None,
) -> str:
    params = {"path": str(project_path)}
    if prompt:
        clean_prompt = _clean_codex_prompt(prompt)
        if clean_prompt:
            params["prompt"] = clean_prompt
    return f"{CODEX_NEW_THREAD_ROUTE}?{urlencode(params)}"


def _warn_codex_open_failed(parent, message: str) -> None:
    QMessageBox.warning(parent, "无法打开 Codex", message)


def _is_windows_os() -> bool:
    return os.name == "nt"


def _is_codex_scheme_registered() -> bool:
    if not _is_windows_os():
        return True

    try:
        import winreg
    except ImportError:
        return True

    for root, path in (
        (winreg.HKEY_CURRENT_USER, r"Software\Classes\codex"),
        (winreg.HKEY_CLASSES_ROOT, "codex"),
    ):
        try:
            with winreg.OpenKey(root, path) as key:
                winreg.QueryValueEx(key, "URL Protocol")
                return True
        except OSError:
            continue
    return False


def _open_codex_url(url: str) -> bool:
    if QDesktopServices.openUrl(QUrl(url)):
        return True

    try:
        return bool(webbrowser.open_new_tab(url))
    except _CODEX_URL_OPEN_ERRORS as exc:
        _log.warning("[股票右键菜单] Codex 深链接回退失败: %s", exc, exc_info=True)
        emit_structured_log(
            "stock_context_menu.codex_open_failed",
            logger=_log,
            level="warning",
            scheme=QUrl(url).scheme(),
            error_type=type(exc).__name__,
        )
        record_metric(
            "stock_context_menu_codex_open_failures",
            1,
            unit="count",
            tags={"error_type": type(exc).__name__},
            logger=_log,
        )
        return False


def _open_codex_desktop_thread(thread_url: str) -> bool:
    return launch_codex_desktop_thread(thread_url)


def open_codex_project_thread(
    parent=None,
    project_path: str | Path = CODEX_INDUSTRY_RESEARCH_PROJECT,
    *,
    prompt: str | None = None,
) -> bool:
    project_path = Path(project_path)
    if not project_path.exists():
        _warn_codex_open_failed(parent, f"产业链投研项目路径不存在：{project_path}")
        return False
    if not _is_windows_os() and not _is_codex_scheme_registered():
        _warn_codex_open_failed(parent, "当前系统没有注册 codex:// 深链接，请先安装或修复 Codex 桌面端。")
        return False

    launch_prompt = prompt if prompt is not None else CODEX_CURRENT_STOCK_PROMPT
    url = build_codex_project_thread_url(project_path, prompt=launch_prompt)
    if _is_windows_os():
        opened = _open_codex_desktop_thread(url)
    else:
        opened = _open_codex_url(url)

    if not opened:
        _warn_codex_open_failed(parent, "系统没有接受 Codex 打开请求。")
        return False

    return True


def _resolve_workspace(parent):
    window = parent.window() if hasattr(parent, "window") else None
    workspace = getattr(window, "_workspace", None)
    if workspace is not None:
        return workspace

    cursor = parent
    visited = set()
    while cursor is not None and id(cursor) not in visited:
        visited.add(id(cursor))
        workspace = getattr(cursor, "_workspace", None)
        if workspace is not None:
            return workspace
        cursor = cursor.parent() if hasattr(cursor, "parent") else None
    return None


def build_stock_context_menu(
    parent,
    code: str,
    name: str,
    *,
    show_watchlist_toggle: bool = True,
    show_export: bool = False,
    export_callback=None,
    extra_actions=None,
    vcp_data: dict = None,
):
    """构建标准化的股票右键菜单并执行

    参数:
        parent: 菜单的父 widget
        code: 股票代码
        name: 股票名称
        show_watchlist_toggle: 是否显示关注池切换选项
        show_export: 是否显示导出选项
        export_callback: 导出回调(仅 show_export=True 时有效)
        extra_actions: 调用方追加的菜单动作 [(文本, 回调)]
        vcp_data: VCP 策略数据(加入关注池时附带)

    返回:
        None — 菜单在内部 exec 并处理所有动作
    """
    menu = QMenu(parent)
    install_menu_fade(menu)
    # 每次创建菜单时动态获取当前主题的 QSS（而非模块加载时的快照）
    menu.setStyleSheet(generate_context_menu_qss())

    # --- 查看操作 ---
    act_chart = menu.addAction("查看 K 线图")
    workspace = _resolve_workspace(parent)
    open_security_detail = getattr(workspace, "open_security_detail", None)
    act_detail = menu.addAction("查看股票全景") if callable(open_security_detail) else None
    act_copy = menu.addAction("复制代码")
    extra_action_pairs = []
    for label, callback in extra_actions or []:
        label = str(label or "").strip()
        if label and callable(callback):
            extra_action_pairs.append((menu.addAction(label), callback))
    menu.addSeparator()

    # --- 关注池操作 ---
    act_watchlist = None
    act_pin_top = None
    act_move_bottom = None
    if show_watchlist_toggle:
        is_fav = watchlist_vm.is_in_watchlist(code)
        act_watchlist = menu.addAction("移出关注池" if is_fav else "加入关注池")
        if is_fav:
            act_pin_top = menu.addAction("置顶")
            act_move_bottom = menu.addAction("置底")
        menu.addSeparator()

    # --- 跳转操作 ---
    act_tdx = menu.addAction("跳转通达信")
    act_em = menu.addAction("跳转东方财富")
    act_codex = menu.addAction("打开 Codex")

    # --- 导出 ---
    act_export = None
    if show_export:
        menu.addSeparator()
        act_export = menu.addAction("导出当前表")

    # === 执行菜单 ===
    action = menu.exec(QCursor.pos())
    if action is None:
        return

    # === 分发动作 ===
    if action == act_chart:
        if isinstance(vcp_data, dict) and vcp_data:
            kline_item = dict(vcp_data)
            kline_item.setdefault("代码", code)
            kline_item.setdefault("名称", name)
            event_bus.sig_show_kline_with_list.emit(code, [kline_item], 0)
        else:
            event_bus.sig_show_kline.emit(code)

    elif action == act_detail and act_detail is not None:
        open_security_detail(
            code,
            {
                "name": name,
                "vcp_data": vcp_data if isinstance(vcp_data, dict) else {},
            },
        )

    elif action == act_copy:
        QApplication.clipboard().setText(code)

    elif extra_action_pairs and any(action == extra_action for extra_action, _callback in extra_action_pairs):
        for extra_action, callback in extra_action_pairs:
            if action == extra_action:
                callback()
                break

    elif action == act_watchlist and show_watchlist_toggle:
        # 清理名称中的星标前缀
        clean_name = _clean_stock_name(name)
        watchlist_vm.toggle_stock(code, clean_name, vcp_data)

    elif action == act_pin_top and act_pin_top is not None:
        watchlist_vm.pin_to_top(code)

    elif action == act_move_bottom and act_move_bottom is not None:
        watchlist_vm.move_to_bottom(code)

    elif action == act_tdx:
        if hasattr(parent, "launch_tdx"):
            parent.launch_tdx(code)

    elif action == act_em:
        if hasattr(parent, "launch_eastmoney"):
            parent.launch_eastmoney(code)

    elif action == act_codex:
        open_codex_project_thread(parent, prompt=build_codex_stock_prompt(code, name))

    elif action == act_export and show_export and export_callback:
        export_callback()
