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
    control = ui["control"]
    table = ui["table"]
    shell = ui["shell"]
    surface = ui["surface"]
    border = ui["border"]
    text = ui["text"]

    return f"""
/* ═══════════════════════════════════════════
   紫金研选量化终端 - 全局主题 QSS (动态生成)
   当前主题: {t.get('name', '未知')}
   ═══════════════════════════════════════════ */

/* --- 全局窗口基底 --- */
QMainWindow, QWidget {{
    background-color: {t['BG_CANVAS']};
    color: {text['primary']};
    font-family: {font['family']};
    font-size: {font['size_md']}px;
}}

/* --- 左侧面板 --- */
QWidget#leftPanel {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {t['BG_SIDEBAR']}, stop:1 {t['BG_INPUT']});
    border-right: 1px solid {t['BRAND_SUBTLE']};
}}

/* ═══════════════════════════════════════════
   QTabWidget - 现代标签页
   ═══════════════════════════════════════════ */
QTabWidget::pane {{
    background-color: {t['BG_CARD']};
    border: 1px solid {t['BORDER_DEFAULT']};
    border-radius: {radius['lg']}px;
    top: -1px;
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: {surface['soft']};
    color: {t['TEXT_MUTED']};
    padding: {control['tab_padding_y']}px {control['tab_padding_x']}px;
    margin-right: 4px;
    font-size: {font['size_md']}px;
    font-weight: {font['weight_medium']};
    border: 1px solid {border['default']};
    border-top: 2px solid transparent;
    border-top-left-radius: {radius['lg']}px;
    border-top-right-radius: {radius['lg']}px;
    min-width: 50px;
}}
QTabBar::tab:hover {{
    color: {t['TEXT_PRIMARY']};
    background: {t['TAB_HOVER_BG']};
    border-color: {border['strong']};
}}
QTabBar::tab:selected {{
    color: {t['TEXT_PRIMARY']};
    font-weight: {font['weight_semibold']};
    background: {t['BRAND_SUBTLE']};
    border-color: {border['accent']};
    border-top: 2px solid transparent;
}}

/* ═══════════════════════════════════════════
   QTableWidget - 数据表格
   ═══════════════════════════════════════════ */
QTableView {{
    background-color: {t['BG_TABLE_BASE']};
    alternate-background-color: {t['BG_TABLE_ALT_ROW']};
    color: {t['TEXT_PRIMARY']};
    gridline-color: {t['BORDER_SUBTLE']};
    border: none;
    font-family: "Microsoft YaHei", "微软雅黑";
    font-size: {font['size_md']}px;
    selection-background-color: {table['selected_bg']};
    selection-color: {t['TEXT_BRIGHT']};
    outline: none;
}}
QTableView::item {{
    padding: {table['cell_padding_y']}px {table['cell_padding_x']}px;
    border-bottom: 1px solid {t['BORDER_SUBTLE']};
}}
QTableView::item:hover {{
    background-color: {surface['hover']};
}}
QTableView::item:selected {{
    background-color: {table['selected_bg']};
    color: {t['TEXT_BRIGHT']};
    border-bottom: 1px solid {border['accent']};
}}
QTableView::item:selected:hover {{
    background-color: {table['selected_hover_bg']};
}}
QTableView:focus {{
    border: 1px solid {border['focus']};
    border-radius: {table['focus_radius']}px;
}}
QTableCornerButton::section {{
    background-color: {surface['elevated']};
    border: none;
    border-bottom: 1px solid {border['default']};
    border-right: 1px solid {border['subtle']};
}}

/* 表头 */
QHeaderView::section {{
    background-color: {surface['toolbar']};
    color: {text['header']};
    font-family: "Microsoft YaHei", "微软雅黑";
    font-size: {table['header_font_size']}px;
    font-weight: {font['weight_semibold']};
    letter-spacing: 0.2px;
    padding: {table['header_padding_y']}px {table['header_padding_x']}px;
    min-height: {table['header_min_height']}px;
    border: none;
    border-bottom: 1px solid {border['strong']};
    border-right: 1px solid {border['subtle']};
}}
QHeaderView::section:hover {{
    background-color: {surface['elevated']};
    color: {text['primary']};
}}
QHeaderView::section:pressed {{
    background-color: {table['sorted_header_bg']};
}}
QHeaderView::down-arrow {{ image: none; width: 0; }}
QHeaderView::up-arrow {{ image: none; width: 0; }}

/* ═══════════════════════════════════════════
   QPushButton - 按钮系统
   ═══════════════════════════════════════════ */
QPushButton {{
    background-color: {t['BG_BUTTON']};
    color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_STRONG']};
    border-radius: {radius['md']}px;
    padding: 0 {control['button_padding_x']}px;
    font-size: {font['size_sm']}px;
    font-weight: {font['weight_medium']};
    min-height: {control['button_height']}px;
}}
QPushButton:hover {{
    background-color: {t['BG_BUTTON_HOVER']};
    border: 1px solid {t['BORDER_BRAND']};
    color: {t['TEXT_PRIMARY']};
}}
QPushButton:pressed {{ background-color: {t['BG_HOVER']}; }}
QPushButton:disabled {{
    background-color: {t['BG_BUTTON']};
    color: {t['TEXT_DISABLED']};
    border: 1px solid {t['BG_BUTTON']};
}}

QPushButton[class="secondary"] {{
    background-color: {t['BG_BUTTON']};
    color: {t['TEXT_SECONDARY']};
    border: 1px solid {t['BORDER_DEFAULT']};
}}
QPushButton[class="secondary"]:hover {{
    background-color: {t['BG_BUTTON_HOVER']};
    color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_BRAND']};
}}
QPushButton[class="ghost"] {{
    background: transparent;
    color: {t['TEXT_SECONDARY']};
    border: 1px solid transparent;
}}
QPushButton[class="ghost"]:hover {{
    background: {t['BG_HOVER']};
    color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_SUBTLE']};
}}
QPushButton[class="danger"] {{
    background-color: {t['COLOR_ERROR']};
    color: {t['TEXT_ON_ACCENT']};
    border: none;
}}
QPushButton[class="danger"]:hover {{
    background-color: {t['BRAND_HOVER']};
    color: {t['TEXT_ON_ACCENT']};
}}

/* CTA 主按钮 */
QPushButton#primaryButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #EF4444, stop:1 #DC2626);
    color: {t['TEXT_ON_ACCENT']}; border: none; font-weight: {font['weight_semibold']}; min-height: {control['button_height']}px;
}}
QPushButton#primaryButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #F87171, stop:1 #EF4444);
}}
QPushButton#primaryButton:pressed {{ background: #B91C1C; }}
QPushButton#primaryButton:disabled {{ background: {t['BORDER_STRONG']}; color: {t['TEXT_MUTED']}; }}

QPushButton[class="ctaSecondary"] {{
    min-height: {control['button_height'] - 2}px;
    padding: 0 {control['button_padding_x']}px;
}}

QToolButton[class="toolbarGhost"] {{
    background-color: {t['BG_BUTTON']};
    color: {t['TEXT_SECONDARY']};
    border: 1px solid {t['BORDER_STRONG']};
    border-radius: {radius['md']}px;
    padding: 0 {control['button_padding_x']}px;
    font-size: {font['size_sm']}px;
    font-weight: {font['weight_medium']};
    min-height: {control['button_height']}px;
}}
QToolButton[class="toolbarGhost"]:hover {{
    background-color: {t['BG_BUTTON_HOVER']};
    color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_BRAND']};
}}
QToolButton[class="toolbarGhost"]:pressed {{
    background-color: {t['BG_HOVER']};
}}
QToolButton[class="toolbarGhost"]:disabled {{
    background-color: {t['BG_BUTTON']};
    color: {t['TEXT_DISABLED']};
    border: 1px solid {t['BG_BUTTON']};
}}



/* 时间段控制按钮 */
QPushButton[class="segmentControl"] {{
    background: {t['BG_BUTTON']}; color: {t['TEXT_SECONDARY']}; border: 1px solid {t['BORDER_STRONG']};
    border-radius: {radius['sm']}px; padding: 4px 12px; font-size: {font['size_xs']}px;
    font-weight: {font['weight_medium']}; min-width: 36px; min-height: {control['segment_height']}px;
}}
QPushButton[class="segmentControl"]:hover {{ background: {t['BG_BUTTON_HOVER']}; color: {t['TEXT_PRIMARY']}; }}
QPushButton[class="segmentControl"][state="active"] {{
    background: #EF4444; color: #FFFFFF;
    border: 1px solid #F87171; font-weight: 600;
}}

/* 监控启动/停止按钮状态 */
QPushButton[monitoring="true"] {{
    background-color: {t['COLOR_ERROR']}; color: #FFFFFF; border: none;
}}
QPushButton[monitoring="true"]:hover {{
    background-color: #F87171;
}}
QPushButton[monitoring_state="running"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #EF4444, stop:1 #DC2626);
    color: #FFFFFF;
    border: none;
}}
QPushButton[monitoring_state="running"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #F87171, stop:1 #EF4444);
}}
QPushButton[monitoring_state="stopping"],
QPushButton[monitoring_state="stopping"]:disabled {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #F59E0B, stop:1 #D97706);
    color: #FFFFFF;
    border: none;
}}

/* ═══════════════════════════════════════════
   QComboBox - 下拉框
   ═══════════════════════════════════════════ */
QComboBox {{
    background-color: {t['BG_INPUT']}; color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_STRONG']}; border-radius: {radius['sm']}px;
    padding: 0 30px 0 10px; font-size: {font['size_sm']}px; min-height: {control['input_height']}px;
}}
QComboBox:hover {{ border: 1px solid {t['BORDER_BRAND']}; }}
QComboBox:focus {{ border: 1px solid {t['BRAND_PRIMARY']}; outline: none; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-top: 5px solid {t['ARROW_COLOR']};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {t['BG_ELEVATED']}; color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_DEFAULT']}; selection-background-color: {t['SELECTION_BG']};
    selection-color: {t['TEXT_BRIGHT']}; outline: none;
}}

/* ═══════════════════════════════════════════
   QSpinBox / QDoubleSpinBox
   ═══════════════════════════════════════════ */
QSpinBox, QDoubleSpinBox {{
    background-color: {t['BG_INPUT']}; color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_STRONG']}; border-radius: {radius['sm']}px;
    padding: 0 8px; font-size: {font['size_sm']}px; min-height: {control['input_height']}px;
}}
QSpinBox:hover, QDoubleSpinBox:hover {{ border: 1px solid {t['BORDER_BRAND']}; }}
QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {t['BRAND_PRIMARY']}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background: transparent; border: none; width: 16px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    border-left: 3px solid transparent; border-right: 3px solid transparent;
    border-bottom: 4px solid {t['ARROW_COLOR']};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    border-left: 3px solid transparent; border-right: 3px solid transparent;
    border-top: 4px solid {t['ARROW_COLOR']};
}}



/* ═══════════════════════════════════════════
   QProgressBar
   ═══════════════════════════════════════════ */
QProgressBar {{
    background-color: {t['BG_BUTTON']}; border: none; border-radius: 3px;
    text-align: center; color: transparent; min-height: 4px; max-height: 4px;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #EF4444, stop:0.5 #F87171, stop:1 #B91C1C);
    border-radius: 3px;
}}

/* ═══════════════════════════════════════════
   QLabel / QLineEdit
   ═══════════════════════════════════════════ */
QWidget#tabToolbar {{
    background-color: {surface['toolbar']};
    border-bottom: 1px solid {border['default']};
    min-height: {shell['toolbar_min_height']}px;
}}
QWidget#tabToolbarHeader, QWidget#tabToolbarControls {{
    background: transparent;
}}
QFrame#tabToolbarTitleWrap {{
    background-color: {surface['toolbar_card']};
    border: 1px solid {border['default']};
    border-radius: {radius['xl']}px;
}}
QWidget#tabToolbarFilters, QWidget#tabToolbarActions {{
    background: transparent;
}}
QPushButton[inToolbar="true"],
QToolButton[inToolbar="true"],
QLineEdit[inToolbar="true"],
QComboBox[inToolbar="true"],
QSpinBox[inToolbar="true"],
QDoubleSpinBox[inToolbar="true"],
QCheckBox[inToolbar="true"] {{
    min-height: {control['toolbar_button_height']}px;
    max-height: {control['toolbar_button_height']}px;
}}
QPushButton[inToolbar="true"],
QToolButton[inToolbar="true"] {{
    padding: 0 {max(10, control['button_padding_x'] - 1)}px;
}}
QLineEdit[inToolbar="true"] {{
    padding: 0 10px;
}}
QComboBox[inToolbar="true"] {{
    padding: 0 26px 0 10px;
}}
QSpinBox[inToolbar="true"], QDoubleSpinBox[inToolbar="true"] {{
    padding: 0 8px;
}}
QCheckBox[inToolbar="true"] {{
    color: {text['secondary']};
    spacing: 6px;
    padding: 0 2px;
}}
QCheckBox[inToolbar="true"]::indicator {{
    width: 14px;
    height: 14px;
}}
QLabel#tabStatusLabel {{
    background-color: {surface['toolbar_chip']};
    color: {text['secondary']};
    border: 1px solid {border['default']};
    border-radius: {radius['pill']}px;
    padding: 0 {control['toolbar_chip_padding_x']}px;
    min-height: {control['toolbar_chip_height']}px;
    font-size: {font['size_sm']}px;
    font-weight: {font['weight_semibold']};
    font-family: {font['mono_family']};
}}
QLabel[toolbarRole="meta"] {{
    color: {text['muted']};
    font-size: {font['size_sm']}px;
    padding: 0 2px;
}}
QLabel[toolbarRole="status"] {{
    color: {text['secondary']};
}}

QLabel {{ color: {t['TEXT_SECONDARY']}; background: transparent; }}

QLabel#tabTitle {{
    font-size: {font['size_lg']}px; font-weight: {font['weight_bold']}; color: {t['TEXT_PRIMARY']};
}}
QLabel#tabSubtitle {{
    font-size: {font['size_sm']}px; color: {t['TEXT_MUTED']};
}}
QLabel#successStatus {{
    color: {t['COLOR_SUCCESS']}; font-weight: {font['weight_bold']}; font-size: {font['size_md']}px;
}}
QLabel#warningStatus {{
    color: #F59E0B; font-weight: {font['weight_bold']}; font-size: {font['size_xs']}px;
}}

QLineEdit {{
    background-color: {t['BG_INPUT']}; color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_STRONG']}; border-radius: {radius['sm']}px;
    padding: 0 12px; font-size: {font['size_sm']}px;
    selection-background-color: rgba(239, 68, 68, 0.3); min-height: {control['input_height']}px;
}}
QLineEdit:hover {{ border: 1px solid {t['BORDER_BRAND']}; }}
QLineEdit:focus {{ border: 1px solid {t['BRAND_PRIMARY']}; background-color: {t['BG_INPUT']}; }}
QLineEdit::placeholder {{ color: {t['TEXT_DISABLED']}; }}

QDialog#settingsDialog, QDialog#scanRangeDialog {{
    background-color: {t['BG_ELEVATED']};
    border: 1px solid {t['BORDER_DEFAULT']};
    border-radius: {radius['xl']}px;
}}
QDialog#settingsDialog QFrame#dialogSection,
QDialog#scanRangeDialog QFrame#dialogSection {{
    background-color: {t['BG_INPUT']};
    border: 1px solid {t['BORDER_DEFAULT']};
    border-radius: {radius['lg']}px;
}}
QLabel#dialogTitle {{
    color: {t['TEXT_PRIMARY']};
    font-size: {font['size_xl']}px;
    font-weight: {font['weight_bold']};
}}
QLabel#dialogFieldLabel {{
    color: {t['TEXT_PRIMARY']};
    font-size: {font['size_sm']}px;
    font-weight: {font['weight_semibold']};
}}
QLabel#dialogHint {{
    color: {t['TEXT_MUTED']};
    font-size: {font['size_sm']}px;
}}
QTextEdit#systemLogText {{
    background-color: {surface['panel']};
    color: {text['primary']};
    border: none;
    border-top: 1px solid {border['subtle']};
    padding: 10px 12px;
    selection-background-color: {table['selected_bg']};
    selection-color: {text['bright']};
    font-family: {font['mono_family']};
    font-size: {font['size_sm']}px;
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
    background: {t['BORDER_DEFAULT']};
    min-height: 30px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{ background: {t['TEXT_MUTED']}; }}
QScrollBar::handle:vertical:pressed {{ background: {t['BRAND_PRIMARY']}; }}
QScrollBar::sub-line:vertical, QScrollBar::add-line:vertical {{ height: 0px; background: none; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QScrollBar:horizontal {{
    border: none;
    background: transparent;
    height: 10px;
    margin: 0px;
}}
QScrollBar::handle:horizontal {{
    background: {t['BORDER_DEFAULT']};
    min-width: 30px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t['TEXT_MUTED']}; }}
QScrollBar::handle:horizontal:pressed {{ background: {t['BRAND_PRIMARY']}; }}
QScrollBar::sub-line:horizontal, QScrollBar::add-line:horizontal {{ width: 0px; background: none; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
"""


# 为了向后兼容，保持 GLOBAL_QSS 变量名可用
# 首次 import 时生成一份，主题切换时由 main_window 重新调用 generate_global_qss()
GLOBAL_QSS = generate_global_qss()
