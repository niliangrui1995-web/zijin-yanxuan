# -*- coding: utf-8 -*-
"""Shared helper functions for table models and delegates."""

from __future__ import annotations

import re
import time
from functools import lru_cache

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont

from app.services.ui_quote_service import resolve_quote_metrics
from ui.theme import theme_manager
from ui.theme_tokens import build_ui_tokens

SERIAL_HEADER = "序号"
FLASH_DURATION_SECONDS = 0.5
_TABLE_DENSITY_CACHE_LOADED = False
_TABLE_DENSITY_CACHE: str | None = None


def invalidate_table_token_cache(density: str | None = None) -> None:
    global _TABLE_DENSITY_CACHE_LOADED, _TABLE_DENSITY_CACHE

    if density is None:
        _TABLE_DENSITY_CACHE_LOADED = False
        _TABLE_DENSITY_CACHE = None
    else:
        _TABLE_DENSITY_CACHE = str(density or "").strip() or None
        _TABLE_DENSITY_CACHE_LOADED = True
    _theme_token_cached.cache_clear()
    _theme_table_tokens_cached.cache_clear()


def _current_table_density():
    global _TABLE_DENSITY_CACHE_LOADED, _TABLE_DENSITY_CACHE

    if _TABLE_DENSITY_CACHE_LOADED:
        return _TABLE_DENSITY_CACHE

    try:
        from app.services.ui_config_service import app_config

        density = getattr(app_config, "table_density", None)
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        density = None
    _TABLE_DENSITY_CACHE = str(density or "").strip() or None
    _TABLE_DENSITY_CACHE_LOADED = True
    return _TABLE_DENSITY_CACHE


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


def _build_qfont(families: list[str], point_size: float, *, bold: bool = False, mono: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(families)
    if float(point_size).is_integer():
        font.setPointSize(int(point_size))
    else:
        font.setPointSizeF(float(point_size))
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    if mono:
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
    if bold:
        font.setBold(True)
    return font


def _build_table_model_fonts() -> dict[str, QFont]:
    font_tokens = build_ui_tokens()["font"]
    point_size = 12
    family_names = list(font_tokens.get("family_names") or ["Microsoft YaHei UI", "Segoe UI"])
    mono_family_names = list(
        font_tokens.get("mono_family_names")
        or ["JetBrains Mono", "Cascadia Mono", "Consolas", "Segoe UI Historic", "Microsoft YaHei UI"]
    )

    return {
        "base": _build_qfont(family_names, point_size),
        "small": _build_qfont(family_names, 11.5),
        "mono": _build_qfont(mono_family_names, point_size, mono=True),
        "small_mono": _build_qfont(mono_family_names, 11.5, mono=True),
        "bold": _build_qfont(family_names, point_size, bold=True),
        "bold_mono": _build_qfont(mono_family_names, point_size, bold=True, mono=True),
    }


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
    if pct is not None:
        for pct_key in ("涨幅%", "涨幅"):
            if pct_key in item_dict and item_dict.get(pct_key) != pct:
                item_dict[pct_key] = pct
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


_MARKET_CAP_HEADERS = {"\u5e02\u503c", "\u603b\u5e02\u503c"}


def _is_market_cap_header(header: str) -> bool:
    return str(header or "").strip() in _MARKET_CAP_HEADERS


def _format_market_cap_display(header: str, raw_val):
    if not _is_market_cap_header(header):
        return None

    text = str(raw_val or "").strip()
    if not text or text in {"--", "-"}:
        return text

    def _replace(match: re.Match) -> str:
        sign, integer_part, decimal_part = match.groups()
        digits = integer_part.replace(",", "")
        if len(digits) <= 3:
            return match.group(0)
        try:
            grouped = f"{int(digits):,}"
        except ValueError:
            return match.group(0)
        return f"{sign or ''}{grouped}{decimal_part or ''}"

    return re.sub(r"(?<![\d,.])([+-]?)(\d[\d,]*)(\.\d+)?", _replace, text)


_FLASH_EXACT_HEADERS = {
    "现价",
    "市价",
    "最新价",
    "最新",
    "收盘",
    "涨幅%",
    "涨幅",
    "涨跌%",
    "涨跌",
    "市值",
    "总市值",
    "买点",
    "状态",
    "市场",
    "突破状态",
    "时间",
    "最近时间",
    "日报时间",
    "更新时间",
}


def _should_flash_header(header: str) -> bool:
    header_text = str(header or "").strip()
    if not header_text or header_text == SERIAL_HEADER:
        return False
    if header_text in _FLASH_EXACT_HEADERS:
        return True
    return any(keyword in header_text for keyword in ("时间", "状态"))


def _build_flash_record(header: str, old_value, new_value, *, now: float | None = None):
    if not _should_flash_header(header):
        return None

    old_text = "" if old_value is None else str(old_value).strip()
    new_text = "" if new_value is None else str(new_value).strip()
    if old_text == new_text:
        return None

    old_num = _parse_numeric_value(old_value)
    new_num = _parse_numeric_value(new_value)
    diff = 0.0
    if old_num is not None and new_num is not None:
        numeric_diff = new_num - old_num
        if abs(numeric_diff) > 0.0001:
            diff = numeric_diff

    return {"time": time.time() if now is None else now, "diff": diff}


def _flash_decay_alpha(elapsed: float, duration: float = FLASH_DURATION_SECONDS) -> float:
    duration = max(0.001, float(duration or FLASH_DURATION_SECONDS))
    progress = max(0.0, min(1.0, float(elapsed or 0.0) / duration))
    eased = progress * progress * (3.0 - 2.0 * progress)
    return max(0.0, 1.0 - eased)


def _prune_flash_records(flash_records: dict, *, now: float | None = None) -> None:
    """Drop expired flash metadata so long-running quote refreshes do not retain stale cells."""
    if not flash_records:
        return

    current = time.time() if now is None else float(now)
    expired_rows = []
    for row, cells in list(flash_records.items()):
        if not isinstance(cells, dict):
            expired_rows.append(row)
            continue
        expired_cols = [
            col
            for col, record in list(cells.items())
            if current - float((record or {}).get("time", 0) or 0) > FLASH_DURATION_SECONDS
        ]
        for col in expired_cols:
            cells.pop(col, None)
        if not cells:
            expired_rows.append(row)

    for row in expired_rows:
        flash_records.pop(row, None)


def _active_flash_record(flash_records: dict, row: int, col: int):
    flash_record = flash_records.get(row, {}).get(col)
    if not flash_record:
        return None
    if time.time() - float(flash_record.get("time", 0) or 0) > FLASH_DURATION_SECONDS:
        return None
    return flash_record


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
    if header in {"细分板块", "细分环节"}:
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


def _is_pct_like_header(header: str) -> bool:
    return "%" in header or "涨幅" in header or header == "涨跌"


def _strong_market_pct_from_row(row_data: dict | None):
    if not isinstance(row_data, dict):
        return None
    for pct_key in ("涨幅%", "涨幅", "涨跌%", "涨跌"):
        value = _parse_numeric_value(row_data.get(pct_key))
        if value is not None:
            return value
    return None


def _is_strong_market_move(header: str, raw_val, row_data: dict | None = None) -> bool:
    header_text = str(header or "").strip()
    pct_value = None
    if _is_pct_like_header(header_text) and "换手" not in header_text:
        pct_value = _parse_numeric_value(raw_val)
    elif header_text in {"现价", "市价", "最新价", "收盘"}:
        pct_value = _strong_market_pct_from_row(row_data)
    return pct_value is not None and abs(float(pct_value)) >= 9.0


def _numeric_heat_color(header: str, raw_val):
    value = _parse_numeric_value(raw_val)
    if value is None:
        return None

    if header in {"5日涨跌%", "10日涨跌%", "20日涨跌%"}:
        if abs(value) < 0.01:
            return None
        if value > 0:
            color = QColor("#FF455A")
            color.setAlpha(min(24, int(6 + (abs(value) / 15.0) * 12)))
            return color
        color = QColor("#05C492")
        color.setAlpha(min(20, int(5 + (abs(value) / 15.0) * 10)))
        return color

    tokens = _theme_table_tokens()
    alpha = 0
    base = None

    if _is_pct_like_header(header) or header in {"现价", "市价", "收盘", "最新价"}:
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
    return "\n".join(text.splitlines())


def _build_cell_tooltip(raw_val):
    """统一表格悬浮提示文本，交给自绘悬浮窗渲染。"""
    text = str(raw_val).strip()
    return _build_cell_tooltip_cached(text)


def _tooltip_for_cell(key: str, raw_val, item_dict: dict):
    if key == SERIAL_HEADER:
        return None
    if key == "外资净买入":
        custom_tip = item_dict.get("_外资净买入_tooltip")
        if custom_tip:
            return custom_tip
    return _build_cell_tooltip(raw_val)


_DYNAMIC_ELIDE_HEADERS = {
    "\u5916\u8d44\u51c0\u4e70\u5165",
    "\u4e70\u65b9\u8425\u4e1a\u90e8",
    "\u5356\u65b9\u8425\u4e1a\u90e8",
    "\u4ea4\u6613\u8be6\u60c5",
    "\u89d2\u8272\u5b9a\u4f4d",
    "\u4ea7\u4e1a\u94fe\u5730\u4f4d",
    "\u50ac\u5316\u5242",
    "\u7ec6\u5206\u677f\u5757",
    "\u70ed\u70b9\u677f\u5757",
    "AI\u7ec6\u5206\u677f\u5757/\u5907\u6ce8",
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


def _accent_rail_color_for_row_style(row_style: str):
    style = str(row_style or "").strip()
    style_map = {
        "breakout": "COLOR_RISE_STRONG",
        "fake_breakout": "COLOR_WARNING",
        "approaching": "STATUS_APPROACHING",
        "warning": "COLOR_ERROR",
        "vcp": "STATUS_VCP",
    }
    color_key = style_map.get(style)
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


def _emit_model_row_ranges(model, changed_rows, start_col: int, end_col: int, roles, *, coalesce: bool = False):
    if not changed_rows:
        return

    changed_rows = sorted(set(changed_rows))
    if coalesce:
        model.dataChanged.emit(model.index(changed_rows[0], start_col), model.index(changed_rows[-1], end_col), roles)
        return

    start_row = prev_row = changed_rows[0]
    for row in changed_rows[1:]:
        if row == prev_row + 1:
            prev_row = row
            continue
        model.dataChanged.emit(model.index(start_row, start_col), model.index(prev_row, end_col), roles)
        start_row = prev_row = row

    model.dataChanged.emit(model.index(start_row, start_col), model.index(prev_row, end_col), roles)
