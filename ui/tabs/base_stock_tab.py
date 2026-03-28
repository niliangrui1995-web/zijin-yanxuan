# -*- coding: utf-8 -*-
"""BaseStockTab — 所有股票列表 Tab 的公共基类

提取各 Tab 中重复的通用逻辑：
- 涨跌着色
- 历史缓存回填
- 右键菜单构建
- 通达信跳转
- 代码复制
"""

import os
import subprocess
import time

from PyQt6.QtWidgets import (
    QWidget, QMenu, QTableWidget, QApplication,
)
from PyQt6.QtGui import QColor, QCursor
from PyQt6.QtCore import Qt

from core.event_bus import event_bus
from ui.theme import (
    COLOR_RISE, COLOR_RISE_STRONG,
    COLOR_FALL, COLOR_FALL_STRONG,
    COLOR_FLAT,
    STATUS_BREAKOUT, STATUS_APPROACHING, STATUS_VCP, STATUS_INACTIVE,
    COLOR_WARNING,
)
from ui.components import NumericTableWidgetItem


class BaseStockTab(QWidget):
    """股票列表 Tab 基类 — 提供通用方法"""

    def __init__(self, data_provider=None, parent=None):
        super().__init__(parent)
        self.data_provider = data_provider

    # ================================================================
    # 通用工具方法
    # ================================================================

    @staticmethod
    def apply_pct_color(cell, pct_val: float):
        """涨跌幅着色（红涨绿跌）"""
        if pct_val > 5:
            cell.setForeground(QColor(COLOR_RISE_STRONG))
        elif pct_val > 0:
            cell.setForeground(QColor(COLOR_RISE))
        elif pct_val < -5:
            cell.setForeground(QColor(COLOR_FALL_STRONG))
        elif pct_val < 0:
            cell.setForeground(QColor(COLOR_FALL))
        else:
            cell.setForeground(QColor(COLOR_FLAT))

    @staticmethod
    def apply_status_color(cell, status_text: str):
        """突破状态着色"""
        if "突破" in status_text:
            cell.setText(f"🚀 {status_text}")
            cell.setForeground(QColor(STATUS_BREAKOUT))
            f = cell.font()
            f.setBold(True)
            cell.setFont(f)
        elif "临近" in status_text:
            cell.setText(f"⚠️ {status_text}")
            cell.setForeground(QColor(COLOR_WARNING))
            f = cell.font()
            f.setBold(True)
            cell.setFont(f)
        elif "蓄力" in status_text:
            cell.setText(f"⏳ {status_text}")
            cell.setForeground(QColor(STATUS_APPROACHING))
        elif "潜伏" in status_text:
            cell.setForeground(QColor(STATUS_VCP))

    def backfill_price_from_cache(self, table: QTableWidget,
                                  code_col: int, price_col: int, pct_col: int):
        """从 cache_data 历史数据回填现价/涨幅"""
        if not self.data_provider:
            return
        for r in range(table.rowCount()):
            code_item = table.item(r, code_col)
            if not code_item:
                continue
            code = code_item.text()
            df = self.data_provider.get_data(code)
            if df is None or len(df) < 2:
                continue
            try:
                last_close = float(df.iloc[-1]['close'])
                prev_close = float(df.iloc[-2]['close'])
                if last_close <= 0 or prev_close <= 0:
                    continue
                pct = ((last_close / prev_close) - 1) * 100

                for col_idx, text in [(price_col, f"{last_close:.2f}"),
                                      (pct_col, f"{pct:+.2f}%")]:
                    existing = table.item(r, col_idx)
                    if existing and existing.text() not in ('--', ''):
                        continue
                    if existing:
                        existing.setText(text)
                    else:
                        item = NumericTableWidgetItem(text)
                        item.setForeground(QColor(COLOR_FLAT))
                        item.setTextAlignment(
                            Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                        )
                        table.setItem(r, col_idx, item)

                    if col_idx == pct_col:
                        cell = table.item(r, col_idx)
                        if cell:
                            self.apply_pct_color(cell, pct)
            except Exception:
                continue

    def build_stock_context_menu(self, table: QTableWidget, pos,
                                 code_col: int = 0, name_col: int = 2):
        """构建通用右键菜单（查看K线 / 加入关注池 / 跳转通达信 / 复制代码）"""
        row = table.rowAt(pos.y())
        if row < 0:
            return
        code_item = table.item(row, code_col)
        if not code_item:
            return
        code = code_item.text().strip()
        name_item = table.item(row, name_col)
        name = name_item.text() if name_item else code

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1A1F2E;
                color: #E2E8F0;
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 8px 24px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: rgba(139,92,246,0.15);
            }
        """)

        # 查看K线
        act_kline = menu.addAction("📈 查看K线图")
        act_kline.triggered.connect(
            lambda: event_bus.sig_action_open_kline.emit(code, name)
        )

        menu.addSeparator()

        # 加入/移出关注池
        act_watch = menu.addAction("⭐ 加入关注池")
        act_watch.triggered.connect(
            lambda: event_bus.sig_watchlist_changed.emit("add", code)
        )

        # AI 诊断
        act_ai = menu.addAction("🤖 AI 智能诊股")
        act_ai.triggered.connect(
            lambda: event_bus.sig_open_ai_diag.emit(code, "ai")
        )

        menu.addSeparator()

        # 跳转通达信
        act_tdx = menu.addAction("🖥️ 跳转通达信")
        act_tdx.triggered.connect(lambda: self._launch_tdx(code))

        # 复制代码
        act_copy = menu.addAction("📋 复制代码")
        act_copy.triggered.connect(
            lambda: QApplication.clipboard().setText(code)
        )

        menu.exec(QCursor.pos())

    def _launch_tdx(self, code: str):
        """跳转通达信并输入股票代码"""
        try:
            import ctypes
            tdx_vipdoc = getattr(self.data_provider, 'tdx_vipdoc', '')
            tdx_path = tdx_vipdoc.replace("vipdoc", "tdxw.exe") if tdx_vipdoc else ""
            if not tdx_path or not os.path.exists(tdx_path):
                event_bus.sig_system_log.emit("warn", f"[TDX] 未找到通达信: {tdx_path}")
                return

            # 查找华泰高级版窗口
            target_title = "华泰网上交易(高级版)"
            hwnd = ctypes.windll.user32.FindWindowW(None, target_title)
            if not hwnd:
                subprocess.Popen([tdx_path])
                time.sleep(2)
                hwnd = ctypes.windll.user32.FindWindowW(None, target_title)

            if hwnd:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                time.sleep(0.3)
                # 输入股票代码
                bare = code.replace('sh', '').replace('sz', '')
                for ch in bare:
                    vk = ord(ch)
                    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
                    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
                    time.sleep(0.05)
                # 按回车
                ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0)
                ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)
        except Exception as e:
            event_bus.sig_system_log.emit("error", f"[TDX] 跳转失败: {e}")
