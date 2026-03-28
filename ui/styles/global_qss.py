# -*- coding: utf-8 -*-
"""紫金研选 — 全局 QSS 样式表（从 main_window_qt.py 抽离）

所有组件的统一暗色主题样式集中管理。
配色方案：紫金暗夜 (Purple-Gold Dark)
主色: #8B5CF6  辅色: #6D28D9  背景: #0F1117  卡片: #1A1F2E
"""


GLOBAL_QSS = """
/* ═══════════════════════════════════════════
   紫金研选量化终端 - 全局深色主题 QSS v3
   ═══════════════════════════════════════════ */

/* --- 全局窗口基底 --- */
QMainWindow, QWidget {
    background-color: #0F1117;
    color: #E2E8F0;
    font-family: "Microsoft YaHei UI", "Inter", "Segoe UI", sans-serif;
    font-size: 13px;
}

/* --- 左侧面板 --- */
QWidget#leftPanel {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #161B26, stop:1 #0D1117);
    border-right: 1px solid rgba(139, 92, 246, 0.12);
}

/* --- AnimatedCard 模块卡片 --- */
QFrame#moduleCard {
    background-color: #1A1F2E;
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 12px;
}
QFrame#moduleCard:hover {
    border: 1px solid rgba(139, 92, 246, 0.2);
}

/* --- GlassPanel 毛玻璃面板 --- */
QFrame#glassPanel {
    background-color: rgba(18, 20, 26, 0.92);
    border-radius: 10px;
    border: 1px solid rgba(255, 255, 255, 0.04);
}

/* ═══════════════════════════════════════════
   QTabWidget - 现代标签页
   ═══════════════════════════════════════════ */
QTabWidget::pane {
    background-color: #1A1F2E;
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 0 0 10px 10px;
    top: -1px;
}
QTabBar {
    background: transparent;
}
QTabBar::tab {
    background: transparent;
    color: #718096;
    padding: 10px 20px;
    margin-right: 2px;
    font-size: 13px;
    font-weight: 500;
    border: none;
    border-bottom: 2px solid transparent;
    min-width: 60px;
}
QTabBar::tab:hover {
    color: #A78BFA;
    background: rgba(139, 92, 246, 0.06);
}
QTabBar::tab:selected {
    color: #F9FAFB;
    font-weight: 600;
    background: #1A1F2E;
    border-bottom: 2px solid #8B5CF6;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
}

/* ═══════════════════════════════════════════
   QTableWidget - 数据表格
   ═══════════════════════════════════════════ */
QTableWidget {
    background-color: #12141A;
    alternate-background-color: #161B26;
    color: #E2E8F0;
    gridline-color: rgba(255, 255, 255, 0.03);
    border: none;
    font-size: 12px;
    selection-background-color: rgba(139, 92, 246, 0.18);
    selection-color: #F9FAFB;
    outline: none;
}
QTableWidget::item {
    padding: 4px 8px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.02);
}
QTableWidget::item:hover {
    background-color: rgba(139, 92, 246, 0.08);
}
QTableWidget::item:selected {
    background-color: rgba(139, 92, 246, 0.18);
    color: #F9FAFB;
}
QTableWidget:focus {
    border: 1px solid rgba(139, 92, 246, 0.3);
    border-radius: 4px;
}

/* 表头 */
QHeaderView::section {
    background-color: #161B26;
    color: #718096;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    padding: 7px 8px;
    border: none;
    border-bottom: 1px solid rgba(139, 92, 246, 0.15);
    border-right: 1px solid rgba(255, 255, 255, 0.03);
}
QHeaderView::section:hover {
    background-color: #1A1F2E;
    color: #A78BFA;
}
QHeaderView::section:pressed {
    background-color: rgba(139, 92, 246, 0.12);
}
QHeaderView::down-arrow { image: none; width: 0; }
QHeaderView::up-arrow { image: none; width: 0; }

/* ═══════════════════════════════════════════
   QPushButton - 按钮系统
   ═══════════════════════════════════════════ */
QPushButton {
    background-color: #1F2937;
    color: #E2E8F0;
    border: 1px solid #374151;
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 500;
    min-height: 32px;
}
QPushButton:hover {
    background-color: #374151;
    border: 1px solid #4B5563;
    color: #F9FAFB;
}
QPushButton:pressed { background-color: #4B5563; }
QPushButton:disabled {
    background-color: #1F2937;
    color: #4B5563;
    border: 1px solid #1F2937;
}

/* CTA 主按钮 */
QPushButton#ctaButton, QPushButton[class="primary"] {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #8B5CF6, stop:1 #7C3AED);
    color: #FFFFFF; border: none; font-weight: 600; min-height: 36px;
}
QPushButton#ctaButton:hover, QPushButton[class="primary"]:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #A78BFA, stop:1 #8B5CF6);
}
QPushButton#ctaButton:pressed, QPushButton[class="primary"]:pressed { background: #6D28D9; }
QPushButton#ctaButton:disabled, QPushButton[class="primary"]:disabled { background: #374151; color: #6B7280; }

/* 危险按钮 */
QPushButton[class="dangerGhost"] {
    background: transparent; color: #F87171;
    border: 1px solid rgba(248, 113, 113, 0.3); font-weight: 500;
}
QPushButton[class="dangerGhost"]:hover {
    background: rgba(248, 113, 113, 0.1);
    border: 1px solid rgba(248, 113, 113, 0.5); color: #FCA5A5;
}

/* 时间段控制按钮 */
QPushButton[class="segmentControl"] {
    background: #1F2937; color: #9CA3AF; border: 1px solid #374151;
    border-radius: 6px; padding: 4px 12px; font-size: 11px;
    font-weight: 500; min-width: 36px; min-height: 28px;
}
QPushButton[class="segmentControl"]:hover { background: #374151; color: #E2E8F0; }
QPushButton[class="segmentControl"][state="active"] {
    background: #8B5CF6; color: #FFFFFF;
    border: 1px solid #A78BFA; font-weight: 600;
}

/* ═══════════════════════════════════════════
   QComboBox - 下拉框
   ═══════════════════════════════════════════ */
QComboBox {
    background-color: #0D1117; color: #E2E8F0;
    border: 1px solid #374151; border-radius: 6px;
    padding: 4px 10px; font-size: 12px; min-height: 28px;
}
QComboBox:hover { border: 1px solid #8B5CF6; }
QComboBox:focus { border: 1px solid #8B5CF6; outline: none; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow {
    image: none; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-top: 5px solid #718096;
    margin-right: 6px;
}
QComboBox QAbstractItemView {
    background-color: #1A1F2E; color: #E2E8F0;
    border: 1px solid #374151; selection-background-color: rgba(139, 92, 246, 0.2);
    selection-color: #F9FAFB; outline: none;
}

/* ═══════════════════════════════════════════
   QSpinBox / QDoubleSpinBox
   ═══════════════════════════════════════════ */
QSpinBox, QDoubleSpinBox {
    background-color: #0D1117; color: #E2E8F0;
    border: 1px solid #374151; border-radius: 6px;
    padding: 4px 8px; font-size: 12px; min-height: 28px;
}
QSpinBox:hover, QDoubleSpinBox:hover { border: 1px solid #8B5CF6; }
QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #8B5CF6; }
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    background: transparent; border: none; width: 16px;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    border-left: 3px solid transparent; border-right: 3px solid transparent;
    border-bottom: 4px solid #718096;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    border-left: 3px solid transparent; border-right: 3px solid transparent;
    border-top: 4px solid #718096;
}

/* ═══════════════════════════════════════════
   QDateEdit
   ═══════════════════════════════════════════ */
QDateEdit {
    background-color: #0D1117; color: #E2E8F0;
    border: 1px solid #374151; border-radius: 6px;
    padding: 4px 10px; font-size: 12px; min-height: 28px;
}
QDateEdit:hover { border: 1px solid #8B5CF6; }
QDateEdit::drop-down { border: none; width: 20px; }

/* ═══════════════════════════════════════════
   QProgressBar
   ═══════════════════════════════════════════ */
QProgressBar {
    background-color: #1F2937; border: none; border-radius: 3px;
    text-align: center; color: transparent; min-height: 4px; max-height: 4px;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #8B5CF6, stop:0.5 #A78BFA, stop:1 #6D28D9);
    border-radius: 3px;
}

/* ═══════════════════════════════════════════
   QLabel / QLineEdit
   ═══════════════════════════════════════════ */
QLabel { color: #A0AEC0; background: transparent; }
QLineEdit {
    background-color: #0D1117; color: #E2E8F0;
    border: 1px solid #374151; border-radius: 6px;
    padding: 6px 12px; font-size: 12px;
    selection-background-color: rgba(139, 92, 246, 0.3); min-height: 28px;
}
QLineEdit:hover { border: 1px solid #4B5563; }
QLineEdit:focus { border: 1px solid #8B5CF6; background-color: #12141A; }
QLineEdit::placeholder { color: #4B5563; }

/* ═══════════════════════════════════════════
   QScrollBar
   ═══════════════════════════════════════════ */
QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }
QScrollBar::handle:vertical {
    background: rgba(139, 92, 246, 0.25); border-radius: 3px; min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: rgba(139, 92, 246, 0.45); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 6px; margin: 0; }
QScrollBar::handle:horizontal {
    background: rgba(139, 92, 246, 0.25); border-radius: 3px; min-width: 30px;
}
QScrollBar::handle:horizontal:hover { background: rgba(139, 92, 246, 0.45); }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::corner { background: transparent; }
QAbstractScrollArea::corner { background: transparent; }

/* ═══════════════════════════════════════════
   QSplitter / QToolTip / QCheckBox / QTextBrowser / QGroupBox / QMenu / QStatusBar
   ═══════════════════════════════════════════ */
QSplitter::handle { background: rgba(139, 92, 246, 0.08); width: 3px; }
QSplitter::handle:hover { background: rgba(139, 92, 246, 0.25); }

QToolTip {
    background-color: #1A1F2E; color: #E2E8F0;
    border: 1px solid rgba(139, 92, 246, 0.2); border-radius: 6px;
    padding: 8px 12px; font-size: 12px;
}

QCheckBox { color: #E2E8F0; spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px; border: 1px solid #374151;
    border-radius: 4px; background: #0D1117;
}
QCheckBox::indicator:hover { border: 1px solid #8B5CF6; }
QCheckBox::indicator:checked { background: #8B5CF6; border: 1px solid #8B5CF6; }

QTextBrowser, QTextEdit {
    background-color: #12141A; color: #E2E8F0;
    border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 8px;
    font-size: 13px; selection-background-color: rgba(139, 92, 246, 0.25);
}

QGroupBox {
    background-color: transparent; border: 1px solid #1F2937;
    border-radius: 8px; margin-top: 12px; padding-top: 16px;
    font-size: 12px; font-weight: 600; color: #718096;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    padding: 2px 12px; color: #8B5CF6;
}

QMenu {
    background-color: #151820; color: #E2E8F0;
    border: 1px solid #252A36; border-radius: 8px; padding: 4px;
}
QMenu::item { padding: 7px 24px; border-radius: 4px; }
QMenu::item:selected { background-color: rgba(139, 92, 246, 0.15); color: white; }
QMenu::separator { height: 1px; background: #252A36; margin: 4px 8px; }

QStatusBar {
    background-color: #0D1117; color: #718096;
    border-top: 1px solid rgba(139, 92, 246, 0.1); font-size: 11px;
}
"""
