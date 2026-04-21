# -*- coding: utf-8 -*-
"""Shared helper functions for table models and delegates."""

from __future__ import annotations

import re
import textwrap
from functools import lru_cache

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from domains.quotes.snapshot import resolve_quote_metrics
from ui.theme import theme_manager
from ui.theme_tokens import build_ui_tokens

SERIAL_HEADER = "序号"

def _current_table_density():
    try:
        from core.app_config import app_config

        return getattr(app_config, "table_density", None)
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None


# 运行时动态获取当前主题颜色（不再用 from import 快照，否则切换主题后颜色不更新）
@lru_cache(maxsize=128)
def _theme_token_cached(theme_name: str, token: str) -> str:
    theme = theme_manager.THEMES.get(theme_name, theme_manager.current_theme)
    return theme.get(token, "")


def _c(token: str) -> str:
    return _theme_token_cached(theme_manager.current_theme_name, token)


@lru_cache(maxsize=8)
def _theme_table_tokens_cached(theme_name: str, density: str | None) -> dict:
    theme = theme_manager.THEMES.get(theme_name, theme_manager.current_theme)
    return build_ui_tokens(theme=theme, density=density)["table"]


def _theme_table_tokens() -> dict:
    return _theme_table_tokens_cached(theme_manager.current_theme_name, _current_table_density())


@lru_cache(maxsize=512)
def _qcolor_from_text_cached(text: str) -> QColor:
    rgba_match = re.fullmatch(
        r"rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*([0-9]*\.?[0-9]+)\s*\)",
        text,
        re.IGNORECASE,
    )
    if rgba_match:
        r, g, b = (int(rgba_match.group(i)) for i in range(1, 4))
        alpha_raw = float(rgba_match.group(4))
        alpha = int(round(alpha_raw * 255)) if alpha_raw <= 1 else int(round(alpha_raw))
        return QColor(r, g, b, max(0, min(255, alpha)))

    rgb_match = re.fullmatch(
        r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)",
        text,
        re.IGNORECASE,
    )
    if rgb_match:
        return QColor(*(int(rgb_match.group(i)) for i in range(1, 4)))

    return QColor(text)


def _apply_quote_metrics_to_row(item_dict: dict, quote: dict) -> tuple[bool, dict]:
    metrics = resolve_quote_metrics(item_dict, quote)
    row_changed = False

    zongguben = float(metrics.get("zongguben", 0) or 0)
    if zongguben > 0 and float(item_dict.get("_zongguben", 0) or 0) != zongguben:
        item_dict["_zongguben"] = zongguben
        row_changed = True

    price_text = metrics.get("price_text")
    if price_text is not None:
        for price_key in ("现价", "市价"):
            if price_key in item_dict and item_dict.get(price_key) != price_text:
                item_dict[price_key] = price_text
                row_changed = True

    pct = metrics.get("pct")
    if pct is not None and item_dict.get("涨幅%") != pct:
        item_dict["涨幅%"] = pct
        row_changed = True

    cap_text = metrics.get("market_cap_text")
    if cap_text and item_dict.get("市值") != cap_text:
        item_dict["市值"] = cap_text
        row_changed = True

    return row_changed, metrics


def _qcolor_from_token(color) -> QColor:
    if isinstance(color, QColor):
        return QColor(color)

    if color is None:
        return QColor()

    text = str(color).strip()
    if not text:
        return QColor()
    return QColor(_qcolor_from_text_cached(text))


def _parse_numeric_value(raw_val):
    if raw_val is None:
        return None
    text = str(raw_val).strip().replace(",", "")
    if not text or text in {"--", "-"}:
        return None
    match = re.search(r"[-+]?\d*\.?\d+", text)
    if not match:
        return None
    try:
        value = float(match.group(0))
    except (TypeError, ValueError):
        return None

    if "万" in text:
        return value * 10000
    if "亿" in text:
        return value * 100000000
    return value


def _is_status_header(header: str) -> bool:
    return header in {
        SERIAL_HEADER,
        "突破状态",
        "状态",
        "买点",
        "关注",
        "市场",
    }


def _is_date_like_header(header: str) -> bool:
    if header in {
        "揭晓日",
        "报告期",
        "最近上榜",
        "上榜日",
        "公告日",
    }:
        return True
    return any(keyword in header for keyword in ("日期", "时间"))


def _is_numeric_header(header: str) -> bool:
    if header == SERIAL_HEADER or _is_status_header(header):
        return False
    if header in {"PE"}:
        return True
    keywords = (
        "%",
        "价",
        "额",
        "量",
        "值",
        "分",
        "幅",
        "RPS",
        "评分",
        "强度",
        "净买",
        "振幅",
        "换手",
        "流通",
        "成交",
    )
    return any(keyword in header for keyword in keywords)


def _numeric_heat_color(header: str, raw_val):
    value = _parse_numeric_value(raw_val)
    if value is None:
        return None

    tokens = _theme_table_tokens()
    alpha = 0
    base = None

    if "%" in header or header in {"涨跌", "现价", "市价", "收盘", "最新价"}:
        if abs(value) < 0.01:
            return None
        base = _c("COLOR_RISE") if value > 0 else _c("COLOR_FALL")
        alpha = min(tokens["numeric_heat_max_alpha"], max(8, int(abs(value) * 2.6)))
    elif header == "评分":
        if value >= 90:
            base = _c("SCORE_EXCELLENT")
            alpha = 28
        elif value >= 80:
            base = _c("SCORE_GOOD")
            alpha = 22
        elif value >= 60:
            base = _c("SCORE_NORMAL")
            alpha = 16
    elif "RPS" in header:
        if value >= 95:
            base = _c("COLOR_INFO")
            alpha = 24
        elif value >= 85:
            base = _c("COLOR_INFO")
            alpha = 16

    if not base or alpha <= 0:
        return None

    color = QColor(base)
    color.setAlpha(alpha)
    return color


@lru_cache(maxsize=2048)
def _build_cell_tooltip_cached(text: str):
    if not text:
        return None

    wrapped_lines = []
    for line in text.splitlines() or [text]:
        wrapped_lines.append(textwrap.fill(line, width=50) if len(line) > 40 else line)
    return "\n".join(wrapped_lines)


def _build_cell_tooltip(raw_val):
    """统一表格悬浮提示文本，交给 QToolTip 自身样式渲染。"""
    text = str(raw_val).strip()
    return _build_cell_tooltip_cached(text)


_DYNAMIC_ELIDE_HEADERS = {
    "\u5916\u8d44\u51c0\u4e70\u5165",
    "\u4e70\u65b9\u8425\u4e1a\u90e8",
    "\u5356\u65b9\u8425\u4e1a\u90e8",
    "\u4ea4\u6613\u8be6\u60c5",
    "\u89d2\u8272\u5b9a\u4f4d",
    "\u4ea7\u4e1a\u94fe\u5730\u4f4d",
}


def _summarize_long_text(header: str, raw_val):
    text = str(raw_val or "").strip()
    if not text:
        return text
    if str(header) not in _DYNAMIC_ELIDE_HEADERS:
        return text
    return " | ".join(part.strip() for part in text.splitlines() if part.strip()) or text


def _status_badge_color(text: str, header: str | None = None):
    from ui.status_registry import resolve_status_tone_name

    st = str(text or "").strip()
    if not st or st in ("--", "-"):
        return None
    tone_name = resolve_status_tone_name(st, header)
    tone_map = {
        "success": "COLOR_SUCCESS",
        "warning": "COLOR_WARNING",
        "info": "COLOR_INFO",
        "error": "COLOR_ERROR",
        "rise": "COLOR_RISE",
        "rise_strong": "COLOR_RISE_STRONG",
    }
    color_key = tone_map.get(tone_name)
    return _c(color_key) if color_key else None


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]", text or ""))


def _normalized_alignment_text(header: str, raw_val) -> str:
    text = str(raw_val or "").strip()
    if not text:
        return ""

    if "日" in header or "期" in header or "时间" in header:
        normalized = text.split(" ")[0].replace("-", "").replace("/", "")
        if normalized.isdigit():
            return normalized

    return text


def _is_numeric_like_text(text: str) -> bool:
    if not text:
        return False
    if _contains_cjk(text):
        return False

    normalized = re.sub(r"[\s,\+\-\.:%/]", "", text)
    return bool(normalized) and normalized.isdigit()


def _alignment_for_cell(header: str, raw_val):
    if header == SERIAL_HEADER:
        return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

    text = _normalized_alignment_text(header, raw_val)
    if not text or text in {"--", "-"}:
        if _is_numeric_header(header) or _is_date_like_header(header):
            return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    if _is_numeric_like_text(text):
        return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

    return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


def _with_serial_header(headers):
    header_list = list(headers or [])
    if not header_list or header_list[0] != SERIAL_HEADER:
        header_list.insert(0, SERIAL_HEADER)
    return header_list


def _sync_serial_values(rows):
    for idx, item in enumerate(rows or [], 1):
        if isinstance(item, dict):
            item[SERIAL_HEADER] = idx


def _emit_model_row_ranges(model, changed_rows, start_col: int, end_col: int, roles):
    if not changed_rows:
        return

    start_row = prev_row = changed_rows[0]
    for row in changed_rows[1:]:
        if row == prev_row + 1:
            prev_row = row
            continue
        model.dataChanged.emit(model.index(start_row, start_col), model.index(prev_row, end_col), roles)
        start_row = prev_row = row

    model.dataChanged.emit(model.index(start_row, start_col), model.index(prev_row, end_col), roles)

