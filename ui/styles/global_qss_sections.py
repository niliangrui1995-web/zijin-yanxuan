# -*- coding: utf-8 -*-
"""QSS section builders for the global application stylesheet."""

from __future__ import annotations


def build_button_qss(ctx: dict) -> str:
    t = ctx["t"]
    font = ctx["font"]
    radius = ctx["radius"]
    control = ctx["control"]
    border = ctx["border"]
    accent_border = ctx["accent_border"]
    text_on_danger = ctx["text_on_danger"]
    error_hover = ctx["error_hover"]
    primary_gradient_start = ctx["primary_gradient_start"]
    primary_gradient_end = ctx["primary_gradient_end"]
    primary_hover_start = ctx["primary_hover_start"]
    primary_hover_end = ctx["primary_hover_end"]
    primary_button_text = ctx["primary_button_text"]
    primary_button_border = ctx["primary_button_border"]
    primary_button_pressed_bg = ctx["primary_button_pressed_bg"]
    segment_active_bg = ctx["segment_active_bg"]
    segment_active_border = ctx["segment_active_border"]
    segment_active_text = ctx["segment_active_text"]

    return f"""
/* ═══════════════════════════════════════════
   QPushButton / QToolButton
   ═══════════════════════════════════════════ */
QPushButton {{
    background-color: {t["BG_BUTTON"]};
    color: {t["TEXT_PRIMARY"]};
    border: 1px solid {t["BORDER_STRONG"]};
    border-radius: {radius["md"]}px;
    padding: 0 {control["button_padding_x"]}px;
    font-size: {font["size_sm"]}px;
    font-weight: {font["weight_medium"]};
    min-height: {control["button_height"]}px;
    outline: none;
}}
QPushButton:hover {{
    background-color: {t["BG_BUTTON_HOVER"]};
    border: 1px solid {accent_border};
    color: {t["TEXT_PRIMARY"]};
}}
QPushButton:focus {{
    border: 1px solid {border["focus"]};
}}
QPushButton:pressed {{ background-color: {t["BG_HOVER"]}; }}
QPushButton:disabled {{
    background-color: {t["BG_BUTTON"]};
    color: {t["TEXT_DISABLED"]};
    border: 1px solid {t["BG_BUTTON"]};
}}

QPushButton[class="secondary"] {{
    background-color: {t["BG_BUTTON"]};
    color: {t["TEXT_SECONDARY"]};
    border: 1px solid {t["BORDER_DEFAULT"]};
}}
QPushButton[class="secondary"]:hover {{
    background-color: {t["BG_BUTTON_HOVER"]};
    color: {t["TEXT_PRIMARY"]};
    border: 1px solid {accent_border};
}}
QPushButton[class="ghost"] {{
    background: transparent;
    color: {t["TEXT_SECONDARY"]};
    border: 1px solid transparent;
}}
QPushButton[class="ghost"]:hover {{
    background: {t["BG_HOVER"]};
    color: {t["TEXT_PRIMARY"]};
    border: 1px solid {t["BORDER_SUBTLE"]};
}}
QPushButton[class="danger"] {{
    background-color: {t["COLOR_ERROR"]};
    color: {text_on_danger};
    border: none;
}}
QPushButton[class="danger"]:hover {{
    background-color: {error_hover};
    color: {text_on_danger};
}}

/* CTA 主按钮 */
QPushButton#primaryButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {primary_gradient_start}, stop:1 {primary_gradient_end});
    color: {primary_button_text}; border: 1px solid {primary_button_border}; font-weight: {font["weight_semibold"]}; min-height: {control["button_height"]}px;
}}
QPushButton#primaryButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {primary_hover_start}, stop:1 {primary_hover_end});
    border: 1px solid {primary_button_border};
}}
QPushButton#primaryButton:pressed {{ background: {primary_button_pressed_bg}; }}
QPushButton#primaryButton:disabled {{ background: {t["BORDER_STRONG"]}; color: {t["TEXT_MUTED"]}; }}

QPushButton[class="ctaSecondary"] {{
    min-height: {control["button_height"] - 2}px;
    padding: 0 {control["button_padding_x"]}px;
}}

QToolButton[class="toolbarGhost"] {{
    background-color: {t["BG_BUTTON"]};
    color: {t["TEXT_SECONDARY"]};
    border: 1px solid {t["BORDER_STRONG"]};
    border-radius: {radius["md"]}px;
    padding: 0 {control["button_padding_x"]}px;
    font-size: {font["size_sm"]}px;
    font-weight: {font["weight_medium"]};
    min-height: {control["button_height"]}px;
    outline: none;
}}
QToolButton[class="toolbarGhost"]:hover {{
    background-color: {t["BG_BUTTON_HOVER"]};
    color: {t["TEXT_PRIMARY"]};
    border: 1px solid {accent_border};
}}
QToolButton[class="toolbarGhost"]:focus {{
    border: 1px solid {border["focus"]};
}}
QToolButton[class="toolbarGhost"]:pressed {{
    background-color: {t["BG_HOVER"]};
}}
QToolButton[class="toolbarGhost"]:disabled {{
    background-color: {t["BG_BUTTON"]};
    color: {t["TEXT_DISABLED"]};
    border: 1px solid {t["BG_BUTTON"]};
}}

/* 时间段控制按钮 */
QPushButton[class="segmentControl"] {{
    background: {t["BG_BUTTON"]}; color: {t["TEXT_SECONDARY"]}; border: 1px solid {t["BORDER_STRONG"]};
    border-radius: {radius["sm"]}px; padding: 4px 12px; font-size: {font["size_xs"]}px;
    font-weight: {font["weight_medium"]}; min-width: 36px; min-height: {control["segment_height"]}px;
}}
QPushButton[class="segmentControl"]:hover {{ background: {t["BG_BUTTON_HOVER"]}; color: {t["TEXT_PRIMARY"]}; }}
QPushButton[class="segmentControl"][state="active"] {{
    background: {segment_active_bg}; color: {segment_active_text};
    border: 1px solid {segment_active_border}; font-weight: 600;
}}

/* 监控启动/停止按钮状态 */
QPushButton[monitoring="true"] {{
    background-color: {t["COLOR_ERROR"]}; color: {text_on_danger}; border: none;
}}
QPushButton[monitoring="true"]:hover {{
    background-color: {error_hover};
}}
QPushButton[monitoring_state="running"] {{
    background-color: {t["COLOR_ERROR"]};
    color: {text_on_danger};
    border: none;
}}
QPushButton[monitoring_state="running"]:hover {{
    background-color: {error_hover};
}}
QPushButton[monitoring_state="stopping"],
QPushButton[monitoring_state="stopping"]:disabled {{
    background-color: {t["COLOR_WARNING"]};
    color: #FFFFFF;
    border: none;
}}
"""


def build_form_progress_qss(ctx: dict) -> str:
    t = ctx["t"]
    font = ctx["font"]
    radius = ctx["radius"]
    control = ctx["control"]
    border = ctx["border"]
    accent_border = ctx["accent_border"]
    input_selection_bg = ctx["input_selection_bg"]
    progress_gradient_start = ctx["progress_gradient_start"]
    progress_gradient_mid = ctx["progress_gradient_mid"]
    progress_gradient_end = ctx["progress_gradient_end"]

    return f"""
/* ═══════════════════════════════════════════
   Form Inputs / Progress
   ═══════════════════════════════════════════ */
QComboBox {{
    background-color: {t["BG_INPUT"]}; color: {t["TEXT_PRIMARY"]};
    border: 1px solid {t["BORDER_STRONG"]}; border-radius: {radius["sm"]}px;
    padding: 0 30px 0 10px; font-size: {font["size_sm"]}px; min-height: {control["input_height"]}px;
}}
QComboBox:hover {{ border: 1px solid {accent_border}; }}
QComboBox:focus {{ border: 1px solid {border["focus"]}; outline: none; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-top: 5px solid {t["ARROW_COLOR"]};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {t["BG_ELEVATED"]}; color: {t["TEXT_PRIMARY"]};
    border: 1px solid {t["BORDER_DEFAULT"]}; selection-background-color: {t["SELECTION_BG"]};
    selection-color: {t["TEXT_BRIGHT"]}; outline: none;
}}

QSpinBox, QDoubleSpinBox {{
    background-color: {t["BG_INPUT"]}; color: {t["TEXT_PRIMARY"]};
    border: 1px solid {t["BORDER_STRONG"]}; border-radius: {radius["sm"]}px;
    padding: 0 8px; font-size: {font["size_sm"]}px; min-height: {control["input_height"]}px;
}}
QSpinBox:hover, QDoubleSpinBox:hover {{ border: 1px solid {accent_border}; }}
QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {border["focus"]}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background: transparent; border: none; width: 16px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    border-left: 3px solid transparent; border-right: 3px solid transparent;
    border-bottom: 4px solid {t["ARROW_COLOR"]};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    border-left: 3px solid transparent; border-right: 3px solid transparent;
    border-top: 4px solid {t["ARROW_COLOR"]};
}}

/* 日期输入 / 区间选择 */
QDateEdit {{
    background-color: {t["BG_INPUT"]};
    color: {t["TEXT_PRIMARY"]};
    border: 1px solid {t["BORDER_STRONG"]};
    border-radius: {radius["sm"]}px;
    padding: 0 30px 0 10px;
    font-size: {font["size_sm"]}px;
    min-height: {control["input_height"]}px;
    selection-background-color: {input_selection_bg};
}}
QDateEdit:hover {{ border: 1px solid {accent_border}; }}
QDateEdit:focus {{ border: 1px solid {border["focus"]}; }}
QDateEdit::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border: none;
    background: transparent;
}}
QDateEdit::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {t["ARROW_COLOR"]};
    margin-right: 6px;
}}
QDateEdit QLineEdit {{
    background: transparent;
    color: {t["TEXT_PRIMARY"]};
    border: none;
    padding: 0;
}}

QProgressBar {{
    background-color: {t["BG_BUTTON"]}; border: none; border-radius: 3px;
    text-align: center; color: transparent; min-height: 4px; max-height: 4px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {progress_gradient_start}, stop:0.5 {progress_gradient_mid}, stop:1 {progress_gradient_end});
    border-radius: 3px;
}}
"""


def build_toolbar_status_qss(ctx: dict) -> str:
    t = ctx["t"]
    font = ctx["font"]
    radius = ctx["radius"]
    space = ctx["space"]
    control = ctx["control"]
    shell = ctx["shell"]
    surface = ctx["surface"]
    border = ctx["border"]
    text = ctx["text"]
    toolbar_background = ctx["toolbar_background"]
    depth_line = ctx["depth_line"]
    accent_border = ctx["accent_border"]
    input_selection_bg = ctx["input_selection_bg"]

    return f"""
/* ═══════════════════════════════════════════
   Toolbar / Status / Text Inputs
   ═══════════════════════════════════════════ */
QWidget#tabToolbar {{
    background: {toolbar_background};
    border-bottom: 1px solid {depth_line};
    min-height: {shell["toolbar_min_height"]}px;
}}
QFrame#tabToolbarTitleWrap {{
    background-color: {surface["toolbar_card"]};
    border: 1px solid {depth_line};
    border-radius: {radius["xl"]}px;
    min-height: {control["toolbar_button_height"] + 1}px;
}}
QWidget#tabToolbarFilters, QWidget#tabToolbarActions {{
    background: transparent;
    min-height: {control["toolbar_button_height"]}px;
}}
QPushButton[inToolbar="true"],
QToolButton[inToolbar="true"],
QLineEdit[inToolbar="true"],
QComboBox[inToolbar="true"],
QSpinBox[inToolbar="true"],
QDoubleSpinBox[inToolbar="true"],
QCheckBox[inToolbar="true"] {{
    min-height: {control["toolbar_button_height"]}px;
}}
QPushButton[inToolbar="true"],
QToolButton[inToolbar="true"] {{
    padding: 0 {max(8, control["button_padding_x"] - 2)}px;
}}
QLineEdit[inToolbar="true"] {{
    padding: 0 8px;
}}
QComboBox[inToolbar="true"] {{
    padding: 0 24px 0 8px;
}}
QSpinBox[inToolbar="true"], QDoubleSpinBox[inToolbar="true"] {{
    padding: 0 6px;
}}
QCheckBox[inToolbar="true"] {{
    color: {text["secondary"]};
    spacing: 6px;
    padding: 0 12px 0 10px;
    background-color: {surface["toolbar_chip"]};
    border: 1px solid {border["subtle"]};
    border-radius: {radius["pill"]}px;
}}
QCheckBox[inToolbar="true"]:hover {{
    background-color: {t["BG_BUTTON_HOVER"]};
    color: {text["primary"]};
    border: 1px solid {accent_border};
}}
QCheckBox[inToolbar="true"]:focus {{
    border: 1px solid {border["focus"]};
}}
QCheckBox[inToolbar="true"]:checked {{
    background-color: {surface["toolbar_chip"]};
    color: {text["primary"]};
    border: 1px solid {accent_border};
}}
QCheckBox[inToolbar="true"]::indicator {{
    width: 14px;
    height: 14px;
}}
QLabel#tabStatusLabel {{
    background-color: {surface["toolbar_chip"]};
    color: {text["secondary"]};
    border: 1px solid {border["subtle"]};
    border-radius: {radius["pill"]}px;
    padding: 0 {control["toolbar_chip_padding_x"]}px;
    min-height: {control["toolbar_chip_height"]}px;
    font-size: {font["size_sm"]}px;
    font-weight: {font["weight_semibold"]};
    font-family: {font["mono_family"]};
}}
QWidget#tabStatusChipBar {{
    background: transparent;
}}
QLabel#tabStatusPrimaryChip {{
    background-color: {t["INFO_BADGE_BG"]};
    color: {t["INFO_BADGE_FG"]};
    border: 1px solid {t["INFO_BADGE_BORDER"]};
    border-radius: {radius["pill"]}px;
    padding: 0 {control["toolbar_chip_padding_x"]}px;
    min-height: {control["toolbar_chip_height"]}px;
    font-size: {font["size_sm"]}px;
    font-weight: {font["weight_semibold"]};
}}
QLabel#tabStatusChip {{
    background-color: {surface["toolbar_chip"]};
    color: {text["secondary"]};
    border: 1px solid {border["subtle"]};
    border-radius: {radius["pill"]}px;
    padding: 0 {max(6, control["toolbar_chip_padding_x"] - 2)}px;
    min-height: {control["toolbar_chip_height"]}px;
    font-size: {font["size_xs"]}px;
    font-weight: {font["weight_semibold"]};
    font-family: {font["mono_family"]};
}}
QLabel[toolbarRole="meta"] {{
    color: {text["muted"]};
    font-size: {font["size_sm"]}px;
    padding: 0 2px;
}}
QLabel[toolbarRole="status"] {{
    color: {text["secondary"]};
}}

QLabel {{ color: {t["TEXT_SECONDARY"]}; background: transparent; }}

QLabel#tabTitle {{
    font-size: {font["size_lg"]}px; font-weight: {font["weight_bold"]}; color: {t["TEXT_PRIMARY"]};
}}
QLabel#tabSubtitle {{
    font-size: {font["size_sm"]}px; color: {t["TEXT_MUTED"]};
}}
QLabel#successStatus {{
    color: {t["COLOR_SUCCESS"]}; font-weight: {font["weight_bold"]}; font-size: {font["size_md"]}px;
}}
QLabel#warningStatus {{
    color: {t["COLOR_WARNING"]}; font-weight: {font["weight_bold"]}; font-size: {font["size_xs"]}px;
}}

QFrame#taskStatusPanel {{
    background-color: {surface["elevated"]};
    border: 1px solid {depth_line};
    border-radius: {radius["lg"]}px;
}}
QLabel#taskStatusPanelTitle {{
    color: {text["primary"]};
    font-size: {font["size_md"]}px;
    font-weight: {font["weight_semibold"]};
}}
QLabel#taskStatusPanelSummary {{
    color: {text["secondary"]};
    font-size: {font["size_sm"]}px;
}}
QPlainTextEdit#taskStatusPanelDetails {{
    background-color: {surface["input"]};
    color: {text["secondary"]};
    border: 1px solid {border["subtle"]};
    border-radius: {radius["md"]}px;
    padding: {space["sm"]}px {space["md"]}px;
    font-family: {font["mono_family"]};
    font-size: {font["size_xs"]}px;
    selection-background-color: {input_selection_bg};
}}

QLineEdit {{
    background-color: {t["BG_INPUT"]}; color: {t["TEXT_PRIMARY"]};
    border: 1px solid {t["BORDER_STRONG"]}; border-radius: {radius["sm"]}px;
    padding: 0 12px; font-size: {font["size_sm"]}px;
    selection-background-color: {input_selection_bg}; min-height: {control["input_height"]}px;
}}
QLineEdit:hover {{ border: 1px solid {accent_border}; }}
QLineEdit:focus {{ border: 1px solid {border["focus"]}; background-color: {t["BG_INPUT"]}; }}
QLineEdit::placeholder {{ color: {t["TEXT_DISABLED"]}; }}
"""


def build_dialog_log_scrollbar_qss(ctx: dict) -> str:
    t = ctx["t"]
    font = ctx["font"]
    radius = ctx["radius"]
    space = ctx["space"]
    surface = ctx["surface"]
    border = ctx["border"]
    text = ctx["text"]
    table = ctx["table"]
    info_badge_text = ctx["info_badge_text"]
    error_hover = ctx["error_hover"]
    text_on_danger = ctx["text_on_danger"]
    scrollbar_handle = ctx["scrollbar_handle"]
    scrollbar_hover = ctx["scrollbar_hover"]
    scrollbar_pressed = ctx["scrollbar_pressed"]

    return f"""
/* ═══════════════════════════════════════════
   Dialogs / System Log
   ═══════════════════════════════════════════ */
QDialog#settingsDialog, QDialog#scanRangeDialog, QDialog#tradeCalendarDialog, QDialog#tradeDateRangeDialog, QDialog#confirmDialog {{
    background: transparent;
    border: none;
}}
QDialog#settingsDialog QFrame#dialogContainer,
QDialog#scanRangeDialog QFrame#dialogContainer,
QDialog#tradeCalendarDialog QFrame#dialogContainer,
QDialog#tradeDateRangeDialog QFrame#dialogContainer,
QDialog#confirmDialog QFrame#dialogContainer {{
    background-color: {t["BG_ELEVATED"]};
    border: 1px solid {t["BORDER_DEFAULT"]};
    border-radius: {radius["xl"]}px;
}}
QDialog#settingsDialog QWidget#dialogTitleBar,
QDialog#scanRangeDialog QWidget#dialogTitleBar,
QDialog#tradeCalendarDialog QWidget#dialogTitleBar,
QDialog#tradeDateRangeDialog QWidget#dialogTitleBar,
QDialog#confirmDialog QWidget#dialogTitleBar {{
    background-color: {t["BG_TITLEBAR"]};
    border-top-left-radius: {radius["xl"]}px;
    border-top-right-radius: {radius["xl"]}px;
    border-bottom: 1px solid {t["TITLEBAR_BORDER"]};
}}
QDialog#tradeCalendarDialog QFrame#dialogContainer,
QDialog#tradeDateRangeDialog QFrame#dialogContainer {{
    background-color: {surface["overlay"]};
    border: 1px solid {border["default"]};
    border-radius: {radius["xl"] + 2}px;
}}
QDialog#tradeCalendarDialog QWidget#dialogTitleBar,
QDialog#tradeDateRangeDialog QWidget#dialogTitleBar {{
    background: transparent;
    border: none;
}}
QDialog#settingsDialog QFrame#dialogSection,
QDialog#scanRangeDialog QFrame#dialogSection {{
    background-color: {t["BG_INPUT"]};
    border: 1px solid {t["BORDER_DEFAULT"]};
    border-radius: {radius["lg"]}px;
}}
QLabel#dialogTitle {{
    color: {t["TEXT_PRIMARY"]};
    font-size: {font["size_xl"]}px;
    font-weight: {font["weight_bold"]};
}}
QLabel#dialogFieldLabel {{
    color: {t["TEXT_PRIMARY"]};
    font-size: {font["size_sm"]}px;
    font-weight: {font["weight_semibold"]};
}}
QLabel#dialogHint {{
    color: {t["TEXT_MUTED"]};
    font-size: {font["size_sm"]}px;
}}
QLabel#dialogWindowTitle {{
    color: {t["TEXT_PRIMARY"]};
    font-size: {font["size_md"]}px;
    font-weight: {font["weight_semibold"]};
}}
QLabel#confirmDialogIcon {{
    background-color: {t["COLOR_INFO"]};
    color: {info_badge_text};
    border-radius: 19px;
    font-size: {font["size_xl"] + 2}px;
    font-weight: {font["weight_bold"]};
}}
QLabel#confirmDialogMessage {{
    background: transparent;
    color: {text["primary"]};
    font-size: {font["size_md"]}px;
    line-height: 1.65em;
    padding-top: {space["xs"]}px;
}}
QDialog#tradeCalendarDialog QLabel#dialogWindowTitle,
QDialog#tradeDateRangeDialog QLabel#dialogWindowTitle {{
    font-size: {font["size_lg"]}px;
    font-weight: {font["weight_bold"]};
}}
QToolButton#dialogCloseButton {{
    background: transparent;
    border: none;
    color: {t["TEXT_MUTED"]};
    font-size: {font["size_md"]}px;
    font-weight: {font["weight_bold"]};
}}
QDialog#tradeCalendarDialog QToolButton#dialogCloseButton,
QDialog#tradeDateRangeDialog QToolButton#dialogCloseButton {{
    min-width: 30px;
    min-height: 30px;
    border-radius: {radius["md"]}px;
}}
QToolButton#dialogCloseButton:hover {{
    background-color: {error_hover};
    color: {text_on_danger};
    border-radius: {radius["sm"]}px;
}}
QFrame#tradeCalendarBody {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {surface["panel"]}, stop:1 {surface["input"]});
    border: 1px solid {border["subtle"]};
    border-radius: {radius["xl"]}px;
}}
QLabel#calendarSectionTitle {{
    color: {t["TEXT_PRIMARY"]};
    font-size: {font["size_md"]}px;
    font-weight: {font["weight_semibold"]};
}}
QTextEdit#systemLogText {{
    background-color: {surface["panel"]};
    color: {text["primary"]};
    border: none;
    border-top: 1px solid {border["subtle"]};
    padding: 10px 12px;
    selection-background-color: {table["selected_bg"]};
    selection-color: {text["bright"]};
    font-family: {font["mono_family"]};
    font-size: {font["size_sm"]}px;
}}

/* ═══════════════════════════════════════════
   QScrollBar - 现代极简滚动条
   ═══════════════════════════════════════════ */
QScrollBar:vertical {{
    border: none;
    background: transparent;
    width: 10px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {scrollbar_handle};
    min-height: 30px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{ background: {scrollbar_hover}; }}
QScrollBar::handle:vertical:pressed {{ background: {scrollbar_pressed}; }}
QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {{ height: 0px; background: none; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QScrollBar:horizontal {{
    border: none;
    background: transparent;
    height: 10px;
    margin: 0px;
}}
QScrollBar::handle:horizontal {{
    background: {scrollbar_handle};
    min-width: 30px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal:hover {{ background: {scrollbar_hover}; }}
QScrollBar::handle:horizontal:pressed {{ background: {scrollbar_pressed}; }}
QScrollBar::sub-line:horizontal, QScrollBar::add-line:horizontal {{ width: 0px; background: none; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
"""
