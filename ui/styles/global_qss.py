# -*- coding: utf-8 -*-
"""紫金研选 — 全局 QSS 样式表（动态化主题版）

根据当前激活的主题 token 字典动态生成 QSS 字符串。
为什么动态生成而不是硬编码？因为用户可以切换主题，
QSS 必须跟着变——就像换一套衣服，每件都要配套。
"""
from ui.theme import theme_manager


def generate_global_qss(theme: dict = None) -> str:
    """根据主题 token 字典生成完整的全局 QSS 字符串"""
    if theme is None:
        theme = theme_manager.current_theme
    t = theme  # 简写

    return f"""
/* ═══════════════════════════════════════════
   紫金研选量化终端 - 全局主题 QSS (动态生成)
   当前主题: {t.get('name', '未知')}
   ═══════════════════════════════════════════ */

/* --- 全局窗口基底 --- */
QMainWindow, QWidget {{
    background-color: {t['BG_CANVAS']};
    color: {t['TEXT_PRIMARY']};
    font-family: "Microsoft YaHei UI", "Inter", "Segoe UI", sans-serif;
    font-size: 13px;
}}

/* --- 左侧面板 --- */
QWidget#leftPanel {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {t['BG_SIDEBAR']}, stop:1 {t['BG_INPUT']});
    border-right: 1px solid {t['BRAND_SUBTLE']};
}}

/* --- AnimatedCard 模块卡片 --- */
QFrame#moduleCard {{
    background-color: {t['BG_MODULE_CARD']};
    border: 1px solid {t['BORDER_DEFAULT']};
    border-radius: 12px;
}}
QFrame#moduleCard:hover {{
    border: 1px solid rgba(239, 68, 68, 0.2);
}}

/* --- GlassPanel 毛玻璃面板 --- */
QFrame#glassPanel {{
    background-color: {t['BG_GLASS']};
    border-radius: 10px;
    border: 1px solid {t['BORDER_SUBTLE']};
}}

/* ═══════════════════════════════════════════
   QTabWidget - 现代标签页
   ═══════════════════════════════════════════ */
QTabWidget::pane {{
    background-color: {t['BG_CARD']};
    border: 1px solid {t['BORDER_DEFAULT']};
    border-radius: 0;
    top: -1px;
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {t['TEXT_MUTED']};
    padding: 8px 18px;
    margin-right: 2px;
    font-size: 13px;
    font-weight: 500;
    border: none;
    border-bottom: 2px solid transparent;
    min-width: 50px;
}}
QTabBar::tab:hover {{
    color: {t['BRAND_HOVER']};
    background: {t['SELECTION_HOVER_BG']};
}}
QTabBar::tab:selected {{
    color: {t['TEXT_BRIGHT']};
    font-weight: 600;
    background: {t['SELECTION_HOVER_BG']};
    border-bottom: 2px solid {t['BRAND_PRIMARY']};
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
    font-family: "SimSun", "宋体";
    font-size: 13px;
    selection-background-color: {t['SELECTION_BG']};
    selection-color: {t['TEXT_BRIGHT']};
    outline: none;
}}
QTableView::item {{
    padding: 5px 8px;
    border-bottom: 1px solid {t['BORDER_SUBTLE']};
}}
QTableView::item:hover {{
    background-color: {t['SELECTION_HOVER_BG']};
}}
QTableView::item:selected {{
    background-color: {t['SELECTION_BG']};
    color: {t['TEXT_BRIGHT']};
}}
QTableView:focus {{
    border: 1px solid rgba(239, 68, 68, 0.3);
    border-radius: 4px;
}}

/* 表头 */
QHeaderView::section {{
    background-color: {t['BG_SIDEBAR']};
    color: {t['TEXT_HEADER']};
    font-family: "SimSun", "宋体";
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.5px;
    padding: 8px 8px;
    border: none;
    border-bottom: 1px solid {t['BORDER_BRAND']};
    border-right: 1px solid {t['BORDER_SUBTLE']};
}}
QHeaderView::section:hover {{
    background-color: {t['BG_CARD']};
    color: {t['BRAND_HOVER']};
}}
QHeaderView::section:pressed {{
    background-color: {t['BRAND_SUBTLE']};
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
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 500;
    min-height: 32px;
}}
QPushButton:hover {{
    background-color: {t['BG_BUTTON_HOVER']};
    border: 1px solid {t['TEXT_DISABLED']};
    color: {t['TEXT_BRIGHT']};
}}
QPushButton:pressed {{ background-color: {t['TEXT_DISABLED']}; }}
QPushButton:disabled {{
    background-color: {t['BG_BUTTON']};
    color: {t['TEXT_DISABLED']};
    border: 1px solid {t['BG_BUTTON']};
}}

/* CTA 主按钮 */
QPushButton#ctaButton, QPushButton[class="primary"] {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #EF4444, stop:1 #DC2626);
    color: #FFFFFF; border: none; font-weight: 600; min-height: 36px;
}}
QPushButton#ctaButton:hover, QPushButton[class="primary"]:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #F87171, stop:1 #EF4444);
}}
QPushButton#ctaButton:pressed, QPushButton[class="primary"]:pressed {{ background: #B91C1C; }}
QPushButton#ctaButton:disabled, QPushButton[class="primary"]:disabled {{ background: {t['BORDER_STRONG']}; color: {t['TEXT_MUTED']}; }}

/* 危险按钮 */
QPushButton[class="dangerGhost"] {{
    background: transparent; color: #F87171;
    border: 1px solid rgba(248, 113, 113, 0.3); font-weight: 500;
}}
QPushButton[class="dangerGhost"]:hover {{
    background: rgba(248, 113, 113, 0.1);
    border: 1px solid rgba(248, 113, 113, 0.5); color: #FCA5A5;
}}

/* 时间段控制按钮 */
QPushButton[class="segmentControl"] {{
    background: {t['BG_BUTTON']}; color: {t['TEXT_SECONDARY']}; border: 1px solid {t['BORDER_STRONG']};
    border-radius: 6px; padding: 4px 12px; font-size: 11px;
    font-weight: 500; min-width: 36px; min-height: 28px;
}}
QPushButton[class="segmentControl"]:hover {{ background: {t['BG_BUTTON_HOVER']}; color: {t['TEXT_PRIMARY']}; }}
QPushButton[class="segmentControl"][state="active"] {{
    background: #EF4444; color: #FFFFFF;
    border: 1px solid #F87171; font-weight: 600;
}}

/* ═══════════════════════════════════════════
   QComboBox - 下拉框
   ═══════════════════════════════════════════ */
QComboBox {{
    background-color: {t['BG_INPUT']}; color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_STRONG']}; border-radius: 6px;
    padding: 4px 10px; font-size: 12px; min-height: 28px;
}}
QComboBox:hover {{ border: 1px solid #EF4444; }}
QComboBox:focus {{ border: 1px solid #EF4444; outline: none; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-top: 5px solid {t['ARROW_COLOR']};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {t['BG_CARD']}; color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_STRONG']}; selection-background-color: {t['SELECTION_BG']};
    selection-color: {t['TEXT_BRIGHT']}; outline: none;
}}

/* ═══════════════════════════════════════════
   QSpinBox / QDoubleSpinBox
   ═══════════════════════════════════════════ */
QSpinBox, QDoubleSpinBox {{
    background-color: {t['BG_INPUT']}; color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_STRONG']}; border-radius: 6px;
    padding: 4px 8px; font-size: 12px; min-height: 28px;
}}
QSpinBox:hover, QDoubleSpinBox:hover {{ border: 1px solid #EF4444; }}
QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid #EF4444; }}
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
   QDateEdit
   ═══════════════════════════════════════════ */
QDateEdit {{
    background-color: {t['BG_INPUT']}; color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_STRONG']}; border-radius: 6px;
    padding: 4px 10px; font-size: 12px; min-height: 28px;
}}
QDateEdit:hover {{ border: 1px solid #EF4444; }}
QDateEdit::drop-down {{ border: none; width: 20px; }}

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
QLabel {{ color: {t['TEXT_SECONDARY']}; background: transparent; }}
QLineEdit {{
    background-color: {t['BG_INPUT']}; color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_STRONG']}; border-radius: 6px;
    padding: 6px 12px; font-size: 12px;
    selection-background-color: rgba(239, 68, 68, 0.3); min-height: 28px;
}}
QLineEdit:hover {{ border: 1px solid {t['TEXT_DISABLED']}; }}
QLineEdit:focus {{ border: 1px solid #EF4444; background-color: {t['BG_TABLE_BASE']}; }}
QLineEdit::placeholder {{ color: {t['TEXT_DISABLED']}; }}

/* ═══════════════════════════════════════════
   QScrollBar
   ═══════════════════════════════════════════ */
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {t['SCROLLBAR_HANDLE']}; border-radius: 3px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {t['SCROLLBAR_HANDLE_HOVER']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 6px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {t['SCROLLBAR_HANDLE']}; border-radius: 3px; min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t['SCROLLBAR_HANDLE_HOVER']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::corner {{ background: transparent; }}
QAbstractScrollArea::corner {{ background: transparent; }}

/* ═══════════════════════════════════════════
   QSplitter / QToolTip / QCheckBox / QTextBrowser / QGroupBox / QMenu / QStatusBar
   ═══════════════════════════════════════════ */
QSplitter::handle {{ background: {t['SPLITTER_BG']}; width: 3px; }}
QSplitter::handle:hover {{ background: {t['SPLITTER_HOVER']}; }}

QToolTip {{
    background-color: {t['BG_CARD']}; color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_BRAND']}; border-radius: 6px;
    padding: 8px 12px; font-size: 12px;
}}

QCheckBox {{ color: {t['TEXT_PRIMARY']}; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {t['BORDER_STRONG']};
    border-radius: 4px; background: {t['BG_INPUT']};
}}
QCheckBox::indicator:hover {{ border: 1px solid #EF4444; }}
QCheckBox::indicator:checked {{ background: #EF4444; border: 1px solid #EF4444; }}

QTextBrowser, QTextEdit {{
    background-color: {t['BG_TABLE_BASE']}; color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_DEFAULT']}; border-radius: 8px;
    font-size: 13px; selection-background-color: rgba(239, 68, 68, 0.25);
}}

QGroupBox {{
    background-color: transparent; border: 1px solid {t['BG_BUTTON']};
    border-radius: 8px; margin-top: 12px; padding-top: 16px;
    font-size: 12px; font-weight: 600; color: {t['TEXT_MUTED']};
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left;
    padding: 2px 12px; color: #EF4444;
}}

QMenu {{
    background-color: {t['BG_MENU']}; color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_MENU']}; border-radius: 8px; padding: 4px;
}}
QMenu::item {{ padding: 7px 24px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {t['SELECTION_BG']}; color: {t['TEXT_BRIGHT']}; }}
QMenu::separator {{ height: 1px; background: {t['BORDER_MENU']}; margin: 4px 8px; }}

QStatusBar {{
    background-color: {t['BG_INPUT']}; color: {t['TEXT_MUTED']};
    border-top: 1px solid {t['BORDER_BRAND']}; font-size: 11px;
}}
"""


# 为了向后兼容，保持 GLOBAL_QSS 变量名可用
# 首次 import 时生成一份，主题切换时由 main_window 重新调用 generate_global_qss()
GLOBAL_QSS = generate_global_qss()
