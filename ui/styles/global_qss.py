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
    background-color: {t['BG_TABLE_HOVER']};
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
    padding: 4px 8px;
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
    padding: 0 14px;
    font-size: 12px;
    font-weight: 500;
    min-height: 30px;
}}
QPushButton:hover {{
    background-color: {t['BG_BUTTON_HOVER']};
    border: 1px solid rgba(239, 68, 68, 0.18);
    color: {t['TEXT_BRIGHT']};
}}
QPushButton:pressed {{ background-color: {t['BG_HOVER']}; }}
QPushButton:disabled {{
    background-color: {t['BG_BUTTON']};
    color: {t['TEXT_DISABLED']};
    border: 1px solid {t['BG_BUTTON']};
}}

/* CTA 主按钮 */
QPushButton#primaryButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #EF4444, stop:1 #DC2626);
    color: #FFFFFF; border: none; font-weight: 600; min-height: 32px;
}}
QPushButton#primaryButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #F87171, stop:1 #EF4444);
}}
QPushButton#primaryButton:pressed {{ background: #B91C1C; }}
QPushButton#primaryButton:disabled {{ background: {t['BORDER_STRONG']}; color: {t['TEXT_MUTED']}; }}

QPushButton[class="ctaSecondary"] {{
    min-height: 30px;
    padding: 0 14px;
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
    border: 1px solid {t['BORDER_STRONG']}; border-radius: 6px;
    padding: 0 30px 0 10px; font-size: 12px; min-height: 30px;
}}
QComboBox:hover {{ border: 1px solid rgba(239, 68, 68, 0.18); }}
QComboBox:focus {{ border: 1px solid #EF4444; outline: none; }}
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
    border: 1px solid {t['BORDER_STRONG']}; border-radius: 6px;
    padding: 0 8px; font-size: 12px; min-height: 30px;
}}
QSpinBox:hover, QDoubleSpinBox:hover {{ border: 1px solid rgba(239, 68, 68, 0.18); }}
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

QLabel#tabTitle {{
    font-size: 15px; font-weight: bold; color: {t['TEXT_PRIMARY']};
}}
QLabel#tabSubtitle {{
    font-size: 11px; color: {t['TEXT_MUTED']};
}}
QLabel#successStatus {{
    color: {t['COLOR_SUCCESS']}; font-weight: bold; font-size: 13px;
}}
QLabel#warningStatus {{
    color: #F59E0B; font-weight: bold; font-size: 11px;
}}

QLineEdit {{
    background-color: {t['BG_INPUT']}; color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_STRONG']}; border-radius: 6px;
    padding: 0 12px; font-size: 12px;
    selection-background-color: rgba(239, 68, 68, 0.3); min-height: 30px;
}}
QLineEdit:hover {{ border: 1px solid rgba(239, 68, 68, 0.18); }}
QLineEdit:focus {{ border: 1px solid #EF4444; background-color: {t['BG_INPUT']}; }}
QLineEdit::placeholder {{ color: {t['TEXT_DISABLED']}; }}

QDialog#settingsDialog, QDialog#scanRangeDialog {{
    background-color: {t['BG_ELEVATED']};
    border: 1px solid {t['BORDER_DEFAULT']};
    border-radius: 14px;
}}
QDialog#settingsDialog QFrame#dialogSection,
QDialog#scanRangeDialog QFrame#dialogSection {{
    background-color: {t['BG_INPUT']};
    border: 1px solid {t['BORDER_DEFAULT']};
    border-radius: 12px;
}}
QLabel#dialogTitle {{
    color: {t['TEXT_PRIMARY']};
    font-size: 16px;
    font-weight: 700;
}}
QLabel#dialogFieldLabel {{
    color: {t['TEXT_PRIMARY']};
    font-size: 12px;
    font-weight: 600;
}}
QLabel#dialogHint {{
    color: {t['TEXT_MUTED']};
    font-size: 12px;
}}

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
    background-color: {t['BG_ELEVATED']};
    color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_DEFAULT']};
    border-radius: 6px;
    padding: 6px 10px;
    margin: 0px;
}}

QCheckBox {{ color: {t['TEXT_PRIMARY']}; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {t['BORDER_STRONG']};
    border-radius: 4px; background: {t['BG_INPUT']};
}}
QCheckBox::indicator:hover {{ border: 1px solid #EF4444; }}
QCheckBox::indicator:checked {{ background: #EF4444; border: 1px solid #EF4444; }}

QTextBrowser, QTextEdit {{
    background-color: {t['BG_INPUT']}; color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_DEFAULT']}; border-radius: 8px;
    font-size: 13px; selection-background-color: rgba(239, 68, 68, 0.25);
}}

/* ═══════════════════════════════════════════
   QDateEdit - 日期输入框（含日历弹出箭头）
   ═══════════════════════════════════════════ */
QDateEdit {{
    background-color: {t['BG_INPUT']};
    color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_STRONG']};
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 13px;
    min-height: 22px;
    max-height: 22px;
}}
QDateEdit::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid {t['BORDER_STRONG']};
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    background: {t['BG_BUTTON']};
}}
QDateEdit::down-arrow {{
    width: 10px; height: 10px;
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {t['TEXT_MUTED']};
}}
QDateEdit::down-arrow:hover {{
    border-top: 5px solid {t['BRAND_PRIMARY']};
}}

/* ═══════════════════════════════════════════
   QCalendarWidget - 日历弹窗模块
   ═══════════════════════════════════════════ */
QCalendarWidget {{
    background-color: {t['BG_CARD']};
    border: 1px solid {t['BORDER_STRONG']};
}}
QCalendarWidget QWidget#qt_calendar_navigationbar {{
    background-color: {t['BG_SIDEBAR']};
    min-height: 32px;
}}
QCalendarWidget QToolButton {{
    color: {t['TEXT_PRIMARY']};
    background: transparent;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 13px;
    font-weight: 600;
}}
QCalendarWidget QToolButton:hover {{
    background-color: {t['BG_HOVER']};
    color: {t['BRAND_HOVER']};
}}
QCalendarWidget QToolButton#qt_calendar_prevmonth,
QCalendarWidget QToolButton#qt_calendar_nextmonth {{
    qproperty-icon: none;
    min-width: 24px;
    font-weight: 700;
}}
QCalendarWidget QMenu {{
    background-color: {t['BG_MENU']};
    color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_MENU']};
}}
QCalendarWidget QMenu::item:selected {{
    background-color: {t['SELECTION_BG']};
    color: {t['TEXT_BRIGHT']};
}}
QCalendarWidget QSpinBox {{
    background: {t['BG_INPUT']};
    color: {t['TEXT_PRIMARY']};
    border: 1px solid {t['BORDER_DEFAULT']};
    border-radius: 4px;
    selection-background-color: {t['SELECTION_BG']};
}}
/* 日历日期网格 — 统一所有日期（含周末）的文字颜色 */
QCalendarWidget QAbstractItemView {{
    background-color: {t['BG_TABLE_BASE']};
    alternate-background-color: {t['BG_TABLE_ALT_ROW']};
    color: {t['TEXT_PRIMARY']};
    selection-background-color: {t['SELECTION_BG']};
    selection-color: {t['TEXT_BRIGHT']};
    border: none;
    outline: none;
    font-size: 13px;
}}
QCalendarWidget QAbstractItemView:enabled {{
    color: {t['TEXT_PRIMARY']};
}}
QCalendarWidget QAbstractItemView:disabled {{
    color: {t['TEXT_DISABLED']};
}}
/* 星期几表头行 */
QCalendarWidget QHeaderView::section {{
    background-color: {t['BG_SIDEBAR']};
    color: {t['TEXT_HEADER']};
    border: none;
    padding: 4px;
    font-size: 11px;
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
    border: 1px solid {t['BORDER_MENU']}; padding: 4px;
}}
QMenu::item {{ padding: 7px 24px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {t['SELECTION_BG']}; color: {t['TEXT_BRIGHT']}; }}
QMenu::separator {{ height: 1px; background: {t['BORDER_MENU']}; margin: 4px 8px; }}

QToolButton#btnSysMenu {{
    border: none; padding: 4px 10px; background: transparent;
    border-radius: 6px; color: {t['TEXT_MUTED']}; font-size: 16px;
}}
QToolButton#btnSysMenu:hover {{ background: {t['BG_HOVER']}; color: {t['TEXT_PRIMARY']}; }}
QToolButton#btnSysMenu::menu-indicator {{ image: none; }}

QTextEdit#systemLogText {{
    background-color: transparent; color: {t['TEXT_MUTED']};
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 12px; border: none; padding: 12px;
    border-top: 1px solid {t['BORDER_DEFAULT']};
    border-radius: 0px;
}}

QStatusBar {{
    background-color: {t['BG_STATUSBAR']}; color: {t['TEXT_MUTED']};
    border-top: 1px solid {t['BORDER_BRAND']}; font-size: 11px;
}}

QWidget#statusBarWidget {{
    background-color: {t['BG_STATUSBAR']};
    border-top: 1px solid {t['STATUSBAR_BORDER']};
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
