# -*- coding: utf-8 -*-
"""紫金研选 — 全局 QSS 样式表（动态化主题版）

根据当前激活的主题 token 字典动态生成 QSS 字符串。
为什么动态生成而不是硬编码？因为用户可以切换主题，
QSS 必须跟着变——就像换一套衣服，每件都要配套。
"""

from ui.theme import theme_manager
from ui.theme_tokens import build_ui_tokens


def generate_global_qss(theme: dict = None, density: str | None = None) -> str:
    """根据主题 token 字典生成完整的全局 QSS 字符串"""
    if theme is None:
        theme = theme_manager.current_theme
    t = theme  # 简写
    ui = build_ui_tokens(theme, density)
    font = ui["font"]
    radius = ui["radius"]
    space = ui["space"]
    control = ui["control"]
    table = ui["table"]
    shell = ui["shell"]
    surface = ui["surface"]
    border = ui["border"]
    text = ui["text"]
    tab_active_bg = t.get("TAB_ACTIVE_BG", t["BRAND_SUBTLE"])
    tab_active_border = t.get("TAB_ACTIVE_BORDER", t["BORDER_BRAND"])
    tab_active_text = t.get("TAB_ACTIVE_TEXT", t["TEXT_PRIMARY"])
    tab_active_top = t.get("TAB_ACTIVE_TOP", "transparent")
    tab_active_indicator = t.get("TAB_ACTIVE_INDICATOR", tab_active_top)
    if t.get("TAB_ACTIVE_INDICATOR_SIDE") == "bottom":
        tab_active_indicator_rule = (
            f"border-top: 1px solid {tab_active_border};\n"
            f"    border-bottom: 2px solid {tab_active_indicator};"
        )
    else:
        tab_active_indicator_rule = f"border-top: 2px solid {tab_active_top};"
    primary_gradient_start = t.get("PRIMARY_GRADIENT_START", t["BRAND_PRIMARY"])
    primary_gradient_end = t.get("PRIMARY_GRADIENT_END", t.get("BRAND_PRESSED", t["BRAND_DEEP"]))
    primary_hover_start = t.get("PRIMARY_HOVER_GRADIENT_START", t["BRAND_HOVER"])
    primary_hover_end = t.get("PRIMARY_HOVER_GRADIENT_END", t["BRAND_PRIMARY"])
    primary_button_text = t.get("PRIMARY_BUTTON_TEXT", t["TEXT_ON_ACCENT"])
    primary_button_border = t.get("PRIMARY_BUTTON_BORDER", "transparent")
    primary_button_pressed_bg = t.get("PRIMARY_BUTTON_PRESSED_BG", t.get("BRAND_PRESSED", t["BRAND_DEEP"]))
    segment_active_bg = t.get("SEGMENT_ACTIVE_BG", t["BRAND_PRIMARY"])
    segment_active_border = t.get("SEGMENT_ACTIVE_BORDER", t["BRAND_HOVER"])
    segment_active_text = t.get("SEGMENT_ACTIVE_TEXT", t["TEXT_ON_ACCENT"])
    progress_gradient_start = t.get("PROGRESS_GRADIENT_START", primary_gradient_start)
    progress_gradient_mid = t.get("PROGRESS_GRADIENT_MID", primary_hover_start)
    progress_gradient_end = t.get("PROGRESS_GRADIENT_END", primary_gradient_end)
    error_hover = t.get("COLOR_ERROR_HOVER", t["BRAND_HOVER"])
    text_on_danger = t.get("TEXT_ON_DANGER", t["TEXT_ON_ACCENT"])
    input_selection_bg = t.get("INPUT_SELECTION_BG", t["SELECTION_BG"])
    info_badge_text = t.get("INFO_BADGE_FG", t["TEXT_PRIMARY"])
    scrollbar_pressed = t.get("SCROLLBAR_HANDLE_PRESSED", t["BRAND_PRIMARY"])

    return f"""
/* ═══════════════════════════════════════════
   紫金研选量化终端 - 全局主题 QSS (动态生成)
   当前主题: {t.get("name", "未知")}
   ═══════════════════════════════════════════ */

/* --- 全局窗口基底 --- */
QMainWindow, QWidget {{
    background-color: {t["BG_CANVAS"]};
    color: {text["primary"]};
    font-family: {font["family"]};
    font-size: {font["size_md"]}px;
}}

QToolTip {{
    background-color: {surface["elevated"]};
    color: {text["primary"]};
    border: 1px solid {border["default"]};
    border-radius: 0px;
    padding: 6px 10px;
    font-family: {font["family"]};
    font-size: {font["size_md"]}px;
}}

/* --- 左侧面板 --- */
QWidget#leftPanel {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {t["BG_SIDEBAR"]}, stop:1 {t["BG_INPUT"]});
    border-right: none;
}}

/* ═══════════════════════════════════════════
   QTabWidget - 现代标签页
   ═══════════════════════════════════════════ */
QTabWidget::pane {{
    background-color: {t["BG_CARD"]};
    border: 1px solid {t["BORDER_SUBTLE"]};
    border-radius: {radius["lg"]}px;
    top: -1px;
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: {surface["toolbar_chip"]};
    color: {t["TEXT_MUTED"]};
    padding: {control["tab_padding_y"]}px {control["tab_padding_x"]}px;
    margin-right: {space["xs"]}px;
    font-size: {font["size_md"]}px;
    font-weight: {font["weight_medium"]};
    border: 1px solid {border["subtle"]};
    border-top: 2px solid transparent;
    border-top-left-radius: {radius["md"]}px;
    border-top-right-radius: {radius["md"]}px;
    min-width: 50px;
}}
QTabBar::tab:hover {{
    color: {t["TEXT_PRIMARY"]};
    background: {t["TAB_HOVER_BG"]};
    border-color: {border["strong"]};
}}
QTabBar::tab:selected {{
    color: {tab_active_text};
    font-weight: {font["weight_semibold"]};
    background: {tab_active_bg};
    border-color: {tab_active_border};
    {tab_active_indicator_rule}
}}

/* ═══════════════════════════════════════════
   QTableWidget - 数据表格
   ═══════════════════════════════════════════ */
QTableView {{
    background-color: {t["BG_TABLE_BASE"]};
    alternate-background-color: {t["BG_TABLE_ALT_ROW"]};
    color: {t["TEXT_PRIMARY"]};
    gridline-color: transparent;
    border: none;
    font-family: "Microsoft YaHei", "微软雅黑";
    font-size: {font["size_md"]}px;
    selection-background-color: {table["selected_bg"]};
    selection-color: {t["TEXT_BRIGHT"]};
    outline: none;
}}
QTableView::item {{
    padding: {table["cell_padding_y"]}px {table["cell_padding_x"]}px;
    border-bottom: 1px solid {t["BORDER_SUBTLE"]};
    border-right: 1px solid transparent;
}}
QTableView::item:hover {{
    background-color: {t["BG_TABLE_HOVER"]};
}}
QTableView::item:selected {{
    background-color: {table["selected_bg"]};
    color: {t["TEXT_BRIGHT"]};
    border-bottom: 1px solid {t["BORDER_SUBTLE"]};
    border-right: 1px solid transparent;
}}
QTableView::item:selected:hover {{
    background-color: {table["selected_hover_bg"]};
}}
QTableView:focus {{
    border: 1px solid {border["focus"]};
    border-radius: {table["focus_radius"]}px;
}}
QTableCornerButton::section {{
    background-color: {surface["elevated"]};
    border: none;
    border-bottom: 1px solid {border["default"]};
    border-right: 1px solid {border["subtle"]};
}}

/* 表头 */
QHeaderView::section {{
    background-color: {surface["toolbar"]};
    color: {text["header"]};
    font-family: "Microsoft YaHei", "微软雅黑";
    font-size: {table["header_font_size"]}px;
    font-weight: {font["weight_semibold"]};
    letter-spacing: 0px;
    padding: {table["header_padding_y"]}px {table["header_padding_x"]}px;
    min-height: {table["header_min_height"]}px;
    border: none;
    border-bottom: 1px solid {border["strong"]};
    border-right: 1px solid transparent;
}}
QHeaderView::section:hover {{
    background-color: {surface["elevated"]};
    color: {text["primary"]};
}}
QHeaderView::section:pressed {{
    background-color: {table["sorted_header_bg"]};
}}
QHeaderView::down-arrow {{ image: none; width: 0; }}
QHeaderView::up-arrow {{ image: none; width: 0; }}

/* ═══════════════════════════════════════════
   QPushButton - 按钮系统
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
}}
QPushButton:hover {{
    background-color: {t["BG_BUTTON_HOVER"]};
    border: 1px solid {t["BORDER_BRAND"]};
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
    border: 1px solid {t["BORDER_BRAND"]};
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
}}
QToolButton[class="toolbarGhost"]:hover {{
    background-color: {t["BG_BUTTON_HOVER"]};
    color: {t["TEXT_PRIMARY"]};
    border: 1px solid {t["BORDER_BRAND"]};
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

/* ═══════════════════════════════════════════
   QComboBox - 下拉框
   ═══════════════════════════════════════════ */
QComboBox {{
    background-color: {t["BG_INPUT"]}; color: {t["TEXT_PRIMARY"]};
    border: 1px solid {t["BORDER_STRONG"]}; border-radius: {radius["sm"]}px;
    padding: 0 30px 0 10px; font-size: {font["size_sm"]}px; min-height: {control["input_height"]}px;
}}
QComboBox:hover {{ border: 1px solid {t["BORDER_BRAND"]}; }}
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

/* ═══════════════════════════════════════════
   QSpinBox / QDoubleSpinBox
   ═══════════════════════════════════════════ */
QSpinBox, QDoubleSpinBox {{
    background-color: {t["BG_INPUT"]}; color: {t["TEXT_PRIMARY"]};
    border: 1px solid {t["BORDER_STRONG"]}; border-radius: {radius["sm"]}px;
    padding: 0 8px; font-size: {font["size_sm"]}px; min-height: {control["input_height"]}px;
}}
QSpinBox:hover, QDoubleSpinBox:hover {{ border: 1px solid {t["BORDER_BRAND"]}; }}
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
QDateEdit:hover {{ border: 1px solid {t["BORDER_BRAND"]}; }}
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



/* ═══════════════════════════════════════════
   QProgressBar
   ═══════════════════════════════════════════ */
QProgressBar {{
    background-color: {t["BG_BUTTON"]}; border: none; border-radius: 3px;
    text-align: center; color: transparent; min-height: 4px; max-height: 4px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {progress_gradient_start}, stop:0.5 {progress_gradient_mid}, stop:1 {progress_gradient_end});
    border-radius: 3px;
}}

/* ═══════════════════════════════════════════
   QLabel / QLineEdit
   ═══════════════════════════════════════════ */
QWidget#tabToolbar {{
    background-color: {surface["toolbar"]};
    border-bottom: 1px solid {border["default"]};
    min-height: {shell["toolbar_min_height"]}px;
}}
QFrame#tabToolbarTitleWrap {{
    background-color: {surface["toolbar_card"]};
    border: 1px solid {border["strong"]};
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
    border: 1px solid {t["BORDER_BRAND"]};
}}
QCheckBox[inToolbar="true"]:focus {{
    border: 1px solid {border["focus"]};
}}
QCheckBox[inToolbar="true"]:checked {{
    background-color: {surface["toolbar_chip"]};
    color: {text["primary"]};
    border: 1px solid {t["BORDER_BRAND"]};
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
    border: 1px solid {border["default"]};
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
QLineEdit:hover {{ border: 1px solid {t["BORDER_BRAND"]}; }}
QLineEdit:focus {{ border: 1px solid {border["focus"]}; background-color: {t["BG_INPUT"]}; }}
QLineEdit::placeholder {{ color: {t["TEXT_DISABLED"]}; }}

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
    background: {t["BORDER_DEFAULT"]};
    min-height: 30px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{ background: {t["TEXT_MUTED"]}; }}
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
    background: {t["BORDER_DEFAULT"]};
    min-width: 30px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t["TEXT_MUTED"]}; }}
QScrollBar::handle:horizontal:pressed {{ background: {scrollbar_pressed}; }}
QScrollBar::sub-line:horizontal, QScrollBar::add-line:horizontal {{ width: 0px; background: none; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
"""


# 为了向后兼容，保持 GLOBAL_QSS 变量名可用
# 首次 import 时生成一份，主题切换时由 main_window 重新调用 generate_global_qss()
GLOBAL_QSS = generate_global_qss()
