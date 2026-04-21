# -*- coding: utf-8 -*-
from __future__ import annotations

import html
from datetime import timedelta

from PyQt6.QtCore import Qt

from core.logger import get_logger
from app.services.ui_runtime_service import MarketCalendar
from app.services.ui_runtime_service import watchlist_vm
from ui.kline_chart_payload import (
    build_kline_summary_cards,
    build_kline_window_palette,
    format_kline_market_badge,
    resolve_kline_vcp_context,
)
from ui.status_registry import resolve_kline_runtime_badges
from ui.theme import theme_manager
from ui.theme_tokens import build_ui_tokens, get_state_tone

log = get_logger(__name__)


def _elide_summary_value(label, key_text: str, value_text: str) -> str:
    clean_value = str(value_text or "").strip()
    if not clean_value or clean_value == "--":
        return clean_value or "--"

    available_width = label.width()
    if available_width <= 0:
        return clean_value

    metrics = label.fontMetrics()
    key_width = metrics.horizontalAdvance(key_text) + 24
    value_width = max(available_width - key_width, 36)
    return metrics.elidedText(clean_value, Qt.TextElideMode.ElideRight, value_width)


def set_header_badge(window, label, text: str, tone_name: str) -> None:
    tokens = build_ui_tokens(theme_manager.current_theme)
    tone = get_state_tone(tone_name, theme_manager.current_theme)
    label.setText(text)
    label.setStyleSheet(
        f"background-color: {tone['bg']}; color: {tone['fg']}; border: 1px solid {tone['border']};"
        f" border-radius: {tokens['radius']['pill']}px; padding: 1px 9px;"
        f" min-height: {tokens['shell']['status_pill_min_height']}px;"
        f" font-size: {tokens['font']['size_xs']}px; font-weight: {tokens['font']['weight_semibold']};"
    )


def apply_header_badges(window) -> None:
    market = window._get_market()
    is_offline = bool(getattr(window.data_provider, "_offline", False))
    info_tone = getattr(window, "_info_tone", "info")
    badges = resolve_kline_runtime_badges(
        info_tone=info_tone,
        is_offline=is_offline,
        market=market,
    )
    set_header_badge(
        window,
        window.session_badge_lbl,
        badges["session_text"],
        badges["session_tone"],
    )
    set_header_badge(
        window,
        window.feed_badge_lbl,
        badges["feed_text"],
        badges["feed_tone"],
    )


def refresh_header_context(window) -> None:
    market_badge = format_kline_market_badge(window.code)
    if hasattr(window, "identity_lbl"):
        window.identity_lbl.setText(f"{window.name}  {window.code}")
    if hasattr(window, "market_badge_lbl"):
        window.market_badge_lbl.setText(market_badge)
    if hasattr(window, "nav_index_lbl"):
        total = len(window.code_list)
        window.nav_index_lbl.setText(
            f"{window.current_idx + 1} / {total}" if total else "单票"
        )
    if hasattr(window, "feed_badge_lbl"):
        apply_header_badges(window)
    if hasattr(window, "summary_cards"):
        summary_cards = build_kline_summary_cards(
            window.vcp_data,
            getattr(window, "is_fav", False),
        )
        for card_index, card_widgets in enumerate(window.summary_cards):
            card_data = summary_cards[card_index] if card_index < len(summary_cards) else {
                "title": "--",
                "rows": (),
            }
            card_widgets["title"].setText(str(card_data.get("title", "--")))
            rows = card_data.get("rows", ()) or ()
            for row_index, label in enumerate(card_widgets["labels"]):
                row_data = rows[row_index] if row_index < len(rows) else {}
                key_text = str(row_data.get("label", "--"))
                raw_value = str(row_data.get("raw_value", "--"))
                base_value = str(row_data.get("value", raw_value or "--"))
                display_value = _elide_summary_value(label, key_text, base_value)
                value_color = (
                    window._summary_highlight_color
                    if row_data.get("highlight")
                    else window._summary_value_color
                )
                label.setText(
                    f"<span style='color:{window._summary_key_color};'>{html.escape(key_text)}</span>"
                    f"&nbsp;&nbsp;<span style='color:{value_color}; font-weight:600;'>"
                    f"{html.escape(display_value)}</span>"
                )
                tooltip = f"{key_text}: {raw_value}" if raw_value and raw_value != "--" else ""
                label.setToolTip(tooltip)


def resolve_vcp_context(window, code: str, name: str, item_data: dict | None = None) -> dict:
    try:
        watchlist_entry = watchlist_vm.get_watchlist_data().get(code, {})
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.debug(f"[KLine] read watchlist context failed: {exc}")
        watchlist_entry = {}

    workspace = getattr(getattr(window, "main_window", None), "_workspace", None)
    if workspace is not None:
        try:
            scan_results = workspace.get_scan_results()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.debug(f"[KLine] read scan context failed: {exc}")
            scan_results = []
    else:
        scan_results = []

    return resolve_kline_vcp_context(
        code=code,
        name=name,
        item_data=item_data,
        watchlist_entry=watchlist_entry,
        scan_results=scan_results,
    )


def apply_qt_theme(window) -> None:
    theme = theme_manager.current_theme
    tokens = build_ui_tokens(theme)
    is_dark = tokens["is_dark"]
    palette = build_kline_window_palette(theme, is_dark)
    widget_bg = palette["widget_bg"]
    widget_text = palette["widget_text"]
    toolbar_bg = palette["toolbar_bg"]
    toolbar_border = palette["toolbar_border"]
    summary_bg = palette["summary_bg"]
    info_color = palette["info_color"]
    btn_border = palette["btn_border"]
    btn_hover_bg = palette["btn_hover_bg"]
    btn_hover_text = palette["btn_hover_text"]
    btn_disabled_text = palette["btn_disabled_text"]
    btn_disabled_border = palette["btn_disabled_border"]
    chart_bg = palette["chart_bg"]
    nav_bg = palette["nav_bg"]
    badge_bg = palette["badge_bg"]
    badge_fg = palette["badge_fg"]
    summary_border = palette["summary_border"]
    radius = tokens["radius"]
    font = tokens["font"]
    control = tokens["control"]
    neutral_tone = tokens["state"]["neutral"]
    action_height = control["button_height"]

    window.setStyleSheet(
        f"""
            QWidget {{ background-color: {widget_bg}; color: {widget_text}; }}
            QLabel {{ font-family: {font['family']}; }}
        """
    )

    window.container.setStyleSheet(
        f"""
            QFrame#klineContainer {{
                background-color: {widget_bg};
                border: 1px solid {toolbar_border};
                border-radius: {radius['md']}px;
            }}
        """
    )

    window.title_bar.setStyleSheet(
        f"""
            QWidget {{
                background-color: {toolbar_bg};
                border-top-left-radius: {radius['md']}px;
                border-top-right-radius: {radius['md']}px;
                border-bottom: none;
            }}
        """
    )
    window.title_lbl.setStyleSheet(
        f"color: {widget_text}; font-weight: {font['weight_bold']}; font-size: {font['size_md']}px;"
    )

    window.btn_close.setStyleSheet(
        f"""
            QToolButton {{
                background: transparent;
                border: none;
                color: {info_color};
            }}
            QToolButton:hover {{
                background-color: #E81123;
                color: white;
                border-radius: {radius['xs']}px;
            }}
        """
    )

    window.header_widget.setStyleSheet(
        f"background-color: {toolbar_bg}; border-bottom: 1px solid {toolbar_border};"
    )
    window.identity_lbl.setStyleSheet(
        f"color: {widget_text}; font-weight: {font['weight_bold']}; font-size: {font['size_lg']}px;"
    )
    window.market_badge_lbl.setStyleSheet(
        f"background-color: {badge_bg}; color: {badge_fg}; border: 1px solid {badge_bg};"
        f"border-radius: {radius['pill']}px; padding: 1px 9px;"
        f" font-size: {font['size_xs']}px; font-weight: {font['weight_semibold']};"
    )

    window.btn_prev.setFixedHeight(action_height)
    window.btn_next.setFixedHeight(action_height)
    window.btn_fav.setFixedHeight(action_height)
    window.nav_index_lbl.setFixedHeight(action_height)
    window.nav_index_lbl.setMinimumWidth(72)
    window.nav_index_lbl.setStyleSheet(
        f"background-color: {neutral_tone['bg']}; color: {info_color}; border: 1px solid {btn_border};"
        f"border-radius: {radius['pill']}px; padding: 0 10px; font-size: {font['size_xs']}px;"
        f" font-weight: {font['weight_semibold']}; font-family: {font['mono_family']};"
    )

    nav_style = f"""
        QPushButton {{
            background-color: {neutral_tone['bg']};
            color: {info_color};
            border: 1px solid {btn_border};
            border-radius: {radius['md']}px;
            padding: 0 12px;
            font-weight: {font['weight_semibold']};
            font-size: {font['size_sm']}px;
        }}
        QPushButton:hover {{ background-color: {btn_hover_bg}; color: {btn_hover_text}; }}
        QPushButton:disabled {{ color: {btn_disabled_text}; border-color: {btn_disabled_border}; }}
    """
    window.btn_prev.setStyleSheet(nav_style)
    window.btn_next.setStyleSheet(nav_style)

    vcp_star = theme.get("KLINE_VCP_STAR", "#FFD60A")
    fav_hover = "rgba(255, 214, 10, 0.1)" if is_dark else "rgba(217, 119, 6, 0.1)"
    fav_active_bg = "#FACC15" if not is_dark else "#FFD60A"
    fav_active_text = "#2B1900" if not is_dark else "#201300"
    fav_active_hover = "#FDE047" if not is_dark else "#FFE083"
    window.btn_fav.setProperty("watching", bool(getattr(window, "is_fav", False)))
    window.btn_fav.setStyleSheet(
        f"""
            QPushButton[watching="false"] {{
                background-color: {neutral_tone['bg']};
                color: {vcp_star};
                border: 1px solid {vcp_star};
                border-radius: {radius['md']}px;
                padding: 0 12px;
                font-weight: {font['weight_semibold']};
                font-size: {font['size_sm']}px;
            }}
            QPushButton[watching="false"]:hover {{ background-color: {fav_hover}; }}
            QPushButton[watching="true"] {{
                background-color: {fav_active_bg};
                color: {fav_active_text};
                border: 1px solid {fav_active_bg};
                border-radius: {radius['md']}px;
                padding: 0 12px;
                font-weight: {font['weight_semibold']};
                font-size: {font['size_sm']}px;
            }}
            QPushButton[watching="true"]:hover {{
                background-color: {fav_active_hover};
                border: 1px solid {fav_active_hover};
            }}
        """
    )
    apply_header_badges(window)

    window.summary_widget.setStyleSheet(
        f"background-color: {summary_bg}; border-bottom: 1px solid {summary_border};"
    )
    window._summary_key_color = theme["TEXT_MUTED"]
    window._summary_value_color = widget_text
    window._summary_highlight_color = vcp_star
    for card in window.summary_cards:
        card["frame"].setStyleSheet(
            f"""
                QFrame#klineSummaryCard {{
                    background-color: {nav_bg};
                    border: 1px solid {summary_border};
                    border-radius: {radius['lg']}px;
                }}
            """
        )
        card["title"].setStyleSheet(
            f"color: {theme['TEXT_MUTED']}; font-size: {font['size_xs']}px;"
            f" font-weight: {font['weight_medium']}; letter-spacing: 0.2px;"
        )
        for label in card["labels"]:
            label.setStyleSheet(
                f"font-size: {font['size_sm']}px; font-weight: {font['weight_medium']};"
            )

    apply_info_styles(window, widget_text=widget_text, info_color=info_color, is_dark=is_dark)
    window.browser.setStyleSheet(f"background-color: {chart_bg};")


def apply_info_styles(
    window,
    *,
    widget_text: str | None = None,
    info_color: str | None = None,
    is_dark: bool | None = None,
) -> None:
    theme = theme_manager.current_theme
    if widget_text is None or info_color is None or is_dark is None:
        widget_text = theme.get("KLINE_WIDGET_TEXT", "#F5F7FA" if theme_manager.is_dark() else theme["TEXT_PRIMARY"])
        info_color = theme.get("KLINE_INFO_COLOR", "#8B98A8" if theme_manager.is_dark() else theme["TEXT_MUTED"])
        is_dark = theme_manager.is_dark()

    tone = getattr(window, "_info_tone", "info")
    tokens = build_ui_tokens(theme)
    state_tone = get_state_tone("info" if tone == "realtime" else tone, theme)
    border_color = (
        state_tone["border"]
        if tone != "info"
        else theme.get("INFO_BADGE_BORDER", "rgba(148, 163, 184, 0.10)" if is_dark else theme["BORDER_SUBTLE"])
    )
    fg_color = widget_text if tone == "info" else state_tone["fg"]
    if tone == "info":
        fg_color = theme.get("INFO_BADGE_FG", fg_color)
        bg_color = theme.get("INFO_BADGE_BG", theme["BRAND_SUBTLE"])
    else:
        bg_color = state_tone["bg"]
    window.info_lbl.setStyleSheet(
        f"background-color: {bg_color}; color: {fg_color}; border: 1px solid {border_color};"
        f"border-radius: {tokens['radius']['pill']}px; padding: 5px 10px;"
        f" font-size: {tokens['font']['size_sm']}px; font-weight: {tokens['font']['weight_semibold']};"
    )


def get_cn_target_trade_date():
    now_cn = MarketCalendar._get_market_now("CN")
    today = now_cn.date()
    latest = MarketCalendar.get_latest_trade_date("CN", ref_date=today)
    if latest is None:
        return None

    if not MarketCalendar.is_trade_day(today, market="CN"):
        return latest

    hhmm = now_cn.hour * 100 + now_cn.minute
    if hhmm < 915:
        return MarketCalendar.get_latest_trade_date(
            "CN",
            ref_date=today - timedelta(days=1),
        )

    return latest

