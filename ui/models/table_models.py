"""
================================================================================
【VCP Hunter UI 核心表格底层引擎维护规则】
(重构后核心规范 - 严格遵守)

【重构前后的区别】
1. 重构前 (QTableWidget)：
   - 极度耦合：每个单元格都需要手动 new QTableWidgetItem()。
   - 代码臃肿：给每个格子去算颜色、对齐方式、还要手动拼 "+" 或 "%" 的符号。
   - 性能极差：1000行数据要创建数万个对象，UI极易卡顿、无响应。
   
2. 重构后 (MVC体系：QTableView + StockTableModel + StockItemDelegate)：
   - 数据降维：业务层只需要去组装简单的字典数组（例如 row_dict={"代码":"000001", "现价":12.5, "涨幅%":2.5} ），一把通过 self.model.update_data(row_data) 送进去。
   - 规则托管：表格不再操作具体的单元格。底层自动发现名为“涨幅%”的列就会自动加上 "2.50%" 和对应红绿色。

【维护与开发硬性规则】
1. 👉 不要手动改UI展现：千万不要在各个 Tab（业务层）里面去计算字体的颜色、靠左靠右对齐、或者是手动加上 %。业务层只管塞干净清晰的“纯数字”或原始字符串。
2. 👉 表头列标准（前四列护城河）：任何看盘 Tab 前四列一定是 `["代码", "名称", "现价", "涨幅%"]`，不准变动位置，不准改名。
3. 👉 涨跌幅识别：任何表示百分比的列，必须在表头包含 `%`（例如 `"换手率%"`、`"折/溢价率(%)"`），底层才能自动捕获并规范成 `+2.40%`。
4. 👉 截断显示：业务层如果传入了超50字的文本，什么都不用管，系统会自动缩略并在鼠标悬停时展示完整的 ToolTip。
5. 👉 安全操作：永远不要再用 `.setRowCount(0)` 来清空表格，唯一合法清空手段是 `self.model.update_data([])`。
================================================================================
"""
import sys
import logging
from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel, QRect
from PyQt6.QtGui import QColor, QFont

_log = logging.getLogger(__name__)
from ui.theme import (
    COLOR_RISE, COLOR_RISE_STRONG, COLOR_FALL, COLOR_FALL_STRONG, COLOR_FLAT,
    COLOR_WARNING, STATUS_APPROACHING, STATUS_INACTIVE, STATUS_VCP,
    STATUS_BREAKOUT, SCORE_EXCELLENT, SCORE_GOOD, SCORE_NORMAL, SCORE_LOW
)

class RtTableModel(QAbstractTableModel):
    def __init__(self, data=None):
        super().__init__()
        self._data = data or []
        self._headers = ["代码", "名称", "现价", "涨幅%", "时间", "评分", "RPS强度", "突破状态", "市值", "区间振幅", "热点板块"]
        # Monospace font for numerical columns
        self.mono_font = QFont()
        self.mono_font.setFamilies(["Consolas", "Microsoft YaHei UI", "monospace"])
        self.mono_font.setPointSize(10)
        self.mono_font.setStyleHint(QFont.StyleHint.Monospace)
        
        self.bold_font = QFont()
        self.bold_font.setBold(True)

    @property
    def row_data(self):
        return self._data

    @property
    def headers(self):
        return self._headers

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return len(self._headers)

    def update_data(self, new_data):
        self.beginResetModel()
        self._data = new_data
        self.endResetModel()

    def get_row_data(self, row):
        if 0 <= row < len(self._data):
            return self._data[row]
        return {}

    def update_quotes(self, quotes: dict):
        if not quotes or not self._data: return
        try:
            col_price_idx = self._headers.index("现价")
            col_pct_idx = self._headers.index("涨幅%")
        except ValueError:
            return
            
        for row, item_dict in enumerate(self._data):
            code = item_dict.get("代码")
            if not code or code not in quotes: continue
            
            q = quotes[code]
            rt_close = float(q.get('close', 0) or 0)
            last_close = float(q.get('last_close', 0) or 0)
            
            if rt_close <= 0 and last_close > 0:
                rt_close = last_close
                
            if last_close > 0 and rt_close > 0:
                pct = ((rt_close / last_close) - 1) * 100
            else:
                pct = 0
                
            item_dict["现价"] = f"{rt_close:.2f}" if rt_close > 0 else "--"
            item_dict["涨幅%"] = pct
            
            idx_start = self.index(row, col_price_idx)
            idx_end = self.index(row, col_pct_idx)
            self.dataChanged.emit(idx_start, idx_end, [Qt.ItemDataRole.DisplayRole])

    def data(self, index, role):
        if not index.isValid():
            return None
            
        row = index.row()
        col = index.column()
        item_dict = self._data[row]
        key = self._headers[col]
        raw_val = item_dict.get(key, '')

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 7: # 突破状态 custom text logic
                st = str(raw_val)
                if "放量突破" in st:
                    return f"🚀 {st}"
                elif "缩量突破" in st:
                    return f"⚠️ {st}"
                elif "临近" in st:
                    return f"⏳ {st}"
            if key in ["AI结论", "AI诊断"]:
                return str(raw_val).replace('\n', ' ')
                
            if "%" in key:
                s_val = str(raw_val)
                if s_val == "--" or s_val == "": return s_val
                if s_val.endswith("%"): return s_val
                try:
                    f_val = float(s_val.replace('%', ''))
                    return f"{f_val:+.2f}%"
                except (ValueError, TypeError):
                    pass
                    
            return str(raw_val)

        elif role == Qt.ItemDataRole.ToolTipRole:
            text = str(raw_val).strip()
            if text:
                if len(text) > 40:
                    import textwrap
                    return '\n'.join([textwrap.fill(line, width=50) for line in text.split('\n')])
                return text

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        elif role == Qt.ItemDataRole.FontRole:
            if col in [3, 4, 5, 6, 8, 9]:
                return self.mono_font
            if col == 7:
                st = str(raw_val)
                if "放量突破" in st or "缩量突破" in st:
                    return self.bold_font
            return None

        elif role == Qt.ItemDataRole.ForegroundRole:
            # Bug#4 修复: 按 header 名称匹配，不再硬编码列索引
            if "%" in key:
                try:
                    pct = float(str(raw_val).replace('%', '').replace('+', ''))
                    if pct >= 9.0: return QColor(COLOR_RISE_STRONG)
                    elif pct > 0: return QColor(COLOR_RISE)
                    elif pct <= -9.0: return QColor(COLOR_FALL_STRONG)
                    elif pct < 0: return QColor(COLOR_FALL)
                    else: return QColor(COLOR_FLAT)
                except (ValueError, TypeError):
                    return QColor(COLOR_FLAT)
            elif key == "突破状态":
                st = str(raw_val)
                if "放量突破" in st: return QColor(COLOR_RISE_STRONG)
                elif "缩量突破" in st: return QColor(COLOR_WARNING)
                elif "临近" in st: return QColor(STATUS_APPROACHING)
                elif "VCP蓄力" in st: return QColor(STATUS_VCP)
                elif "非红盘" in st or "异常" in st or "一字" in st or "观望" in st:
                    return QColor(STATUS_INACTIVE)
                
            return QColor(COLOR_FLAT)

        elif role == Qt.ItemDataRole.UserRole:
            # specifically for sorting numerical columns
            import re
            s_val = str(raw_val).replace(',', '')
            if col in [4, 6] or "万" in s_val or "亿" in s_val:
                if '万' in s_val:
                    m = re.search(r'([-+]?\d*\.?\d+)', s_val)
                    if m: return float(m.group(1)) * 10000
                    return 0.0
                if '亿' in s_val:
                    m = re.search(r'([-+]?\d*\.?\d+)', s_val)
                    if m: return float(m.group(1)) * 100000000
                    return 0.0
                m = re.search(r'([-+]?\d*\.?\d+)', s_val)
                if m: return float(m.group(1))
                return 0.0
            return str(raw_val)

        return None

    def headerData(self, section, orientation, role):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._headers[section]
        return None

class RtSortFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(Qt.ItemDataRole.UserRole)
        self._filter_text = ""

    def lessThan(self, left, right):
        leftData = self.sourceModel().data(left, Qt.ItemDataRole.UserRole)
        rightData = self.sourceModel().data(right, Qt.ItemDataRole.UserRole)
        
        # fallback to DisplayRole if UserRole is standard string
        if leftData is None or rightData is None:
            leftData = self.sourceModel().data(left, Qt.ItemDataRole.DisplayRole)
            rightData = self.sourceModel().data(right, Qt.ItemDataRole.DisplayRole)

        left_str = str(leftData).strip()
        right_str = str(rightData).strip()

        # Handle placeholders explicitly to sort them at the bottom
        if left_str in ('', '--', '-'): left_val = float('-inf')
        else:
            try: left_val = float(leftData)
            except (ValueError, TypeError): left_val = None

        if right_str in ('', '--', '-'): right_val = float('-inf')
        else:
            try: right_val = float(rightData)
            except (ValueError, TypeError): right_val = None

        if left_val is not None and right_val is not None:
            return left_val < right_val

        return left_str < right_str

    def setFilterText(self, text):
        self._filter_text = text.lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self._filter_text:
            return True
            
        model = self.sourceModel()
        # column 0 is code, 2 is name (or maybe 1, we must safely get them based on headers if possible, but 0 and 2 are defaults here)
        code_idx = model.index(source_row, 0, source_parent)
        name_idx = model.index(source_row, 2, source_parent)
        
        # in some tabs (like RTMonitor) code might be 1 and name 2
        # gracefully handle this by scanning for actual column names if needed, but for now we search all accessible text
        c_text = str(model.data(code_idx, Qt.ItemDataRole.DisplayRole) or "").lower()
        n_text = str(model.data(name_idx, Qt.ItemDataRole.DisplayRole) or "").lower()
        
        from ui.components import SearchFilter
        if SearchFilter.match_pinyin_or_text(self._filter_text, c_text, n_text):
            return True
            
        # fallback to scan all cols if columns shifted
        for col in range(model.columnCount()):
            idx = model.index(source_row, col, source_parent)
            text = str(model.data(idx, Qt.ItemDataRole.DisplayRole) or "").lower()
            if self._filter_text in text:
                return True
                
        return False

import time
from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QApplication, QStyle
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush

class StockItemDelegate(QStyledItemDelegate):
    """
    负责高级单元格渲染的委托类，包含闪烁褪色动画（后续由定时器或外部驱动刷新）
    和高级彩色状态胶囊（Pill）绘制。
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.flash_duration = 0.6  # 600ms
        
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        painter.save()
        
        # 0. 绘制基础默认背景（借用系统的绘制，并屏蔽默认文字）
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = option.widget
        style = widget.style() if widget else QApplication.style()

        # 1. 获取闪电更新动画数据
        flash_data = index.data(Qt.ItemDataRole.UserRole + 1)
        if flash_data and isinstance(flash_data, dict):
            update_time = flash_data.get("time", 0)
            diff = flash_data.get("diff", 0)  # >0 涨, <0 跌
            elapsed = time.time() - update_time
            if elapsed < self.flash_duration:
                alpha = int(255 * (1.0 - (elapsed / self.flash_duration)))
                color_hex = COLOR_RISE_STRONG if diff > 0 else COLOR_FALL_STRONG
                bg_color = QColor(color_hex)
                bg_color.setAlpha(min(80, max(0, int(alpha * 0.3))))
                painter.fillRect(option.rect, bg_color)
                
        # 2. 判断是否是自定义绘制的胶囊文本 (Pill)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        pill_color = index.data(Qt.ItemDataRole.UserRole + 2) # Pill Color Role
        
        if pill_color and text:
            rect = option.rect
            fm = painter.fontMetrics()
            text_width = fm.horizontalAdvance(str(text))
            text_height = fm.height()
            
            # 胶囊边界框
            pad_x = 12
            pad_y = 6
            pill_rect = option.rect.adjusted(2, 2, -2, -2)
            
            # 计算剧中或靠左的绘制位置
            align = index.data(Qt.ItemDataRole.TextAlignmentRole)
            draw_rect = QRect(0, 0, text_width + pad_x, text_height + pad_y)
            if align and (align & Qt.AlignmentFlag.AlignLeft.value):
                draw_rect.moveCenter(rect.center())
                draw_rect.moveLeft(rect.left() + 8)
            else:
                draw_rect.moveCenter(rect.center())

            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            p_color = QColor(pill_color)
            p_color.setAlpha(35)
            painter.setBrush(QBrush(p_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(draw_rect, 6, 6)
            
            text_color = QColor(pill_color)
            text_color.setAlpha(255)
            painter.setPen(QPen(text_color))
            painter.drawText(draw_rect, Qt.AlignmentFlag.AlignCenter, str(text))
        else:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        painter.restore()


class StockTableModel(QAbstractTableModel):
    def __init__(self, headers, data=None):
        super().__init__()
        self._headers = headers
        self._data = data or []
        self._flash_records = {} # row -> {col: {"time": stamp, "diff": val}}

        self.mono_font = QFont()
        self.mono_font.setFamilies(["Consolas", "Microsoft YaHei UI", "monospace"])
        self.mono_font.setPointSize(10)
        self.mono_font.setStyleHint(QFont.StyleHint.Monospace)
        
        self.bold_font = QFont()
        self.bold_font.setBold(True)

    @property
    def row_data(self):
        return self._data

    @property
    def headers(self):
        return self._headers

    def get_row_data(self, row):
        if 0 <= row < len(self._data):
            return self._data[row]
        return None

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return len(self._headers)

    def update_data(self, new_data):
        self.beginResetModel()
        self._data = new_data
        self.endResetModel()

    def set_cell_value(self, row, col_name, new_val):
        """用于局部闪动更新的接口"""
        if 0 <= row < len(self._data):
            old_val = self._data[row].get(col_name)
            self._data[row][col_name] = new_val
            
            if col_name in ["现价", "最新价", "最新", "涨幅%"]:
                try:
                    diff = float(str(new_val).strip('%').strip('+')) - float(str(old_val).strip('%').strip('+'))
                    if abs(diff) > 0.0001:
                        if row not in self._flash_records:
                            self._flash_records[row] = {}
                        try:
                            col_idx = self._headers.index(col_name)
                            self._flash_records[row][col_idx] = {"time": time.time(), "diff": diff}
                        except ValueError:
                            pass
                except (ValueError, TypeError):
                    pass
            
            try:
                col_idx = self._headers.index(col_name)
                idx = self.index(row, col_idx)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
            except ValueError:
                pass

    def update_quotes(self, quotes: dict):
        """批量更新现价与涨幅并触发局部刷新，解决主线程卡顿"""
        if not quotes or not self._data: return
        for row, item_dict in enumerate(self._data):
            code = item_dict.get("代码")
            if not code or code not in quotes: continue
            
            q = quotes[code]
            rt_close = float(q.get('close', 0) or 0)
            last_close = float(q.get('last_close', 0) or 0)
            
            if rt_close <= 0 and last_close > 0:
                rt_close = last_close
                
            if last_close > 0 and rt_close > 0:
                pct = ((rt_close / last_close) - 1) * 100
            else:
                pct = 0
                
            self.set_cell_value(row, "现价", f"{rt_close:.2f}" if rt_close > 0 else "--")
            self.set_cell_value(row, "涨幅%", pct)


    def data(self, index, role):
        if not index.isValid():
            return None
            
        row = index.row()
        col = index.column()
        item_dict = self._data[row]
        key = self._headers[col]
        raw_val = item_dict.get(key, '')

        if role == Qt.ItemDataRole.DisplayRole:
            if key == "代码" and isinstance(raw_val, str) and not raw_val.startswith("sz") and not raw_val.startswith("sh"):
                pass 
            if key in ["AI结论", "AI诊断"]:
                return str(raw_val).replace('\n', ' ')
                
            if "%" in key:
                s_val = str(raw_val)
                if s_val == "--" or s_val == "": return s_val
                if s_val.endswith("%"): return s_val
                try:
                    f_val = float(s_val.replace('%', ''))
                    return f"{f_val:+.2f}%"
                except (ValueError, TypeError):
                    pass
                    
            return str(raw_val)

        elif role == Qt.ItemDataRole.ToolTipRole:
            text = str(raw_val).strip()
            if text:
                if len(text) > 40:
                    import textwrap
                    return '\n'.join([textwrap.fill(line, width=50) for line in text.split('\n')])
                return text

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        elif role == Qt.ItemDataRole.FontRole:
            if key in ["现价", "涨幅%", "量比", "换手率%", "区间振幅", "市值", "流通市值", "成交额", "评分"]:
                return self.mono_font
            return None

        elif role == Qt.ItemDataRole.ForegroundRole:
            if "%" in key or key in ["涨跌", "净额"]:
                try:
                    pct = float(str(raw_val).replace('%', '').replace('+', '').replace(',', ''))
                    if pct >= 9.0: return QColor(COLOR_RISE_STRONG)
                    elif pct > 0: return QColor(COLOR_RISE)
                    elif pct <= -9.0: return QColor(COLOR_FALL_STRONG)
                    elif pct < 0: return QColor(COLOR_FALL)
                except (ValueError, TypeError):
                    pass
            return QColor(COLOR_FLAT)

        elif role == Qt.ItemDataRole.BackgroundRole:
            row_style = item_dict.get('_row_style', '')
            if row_style == 'breakout':
                return QColor(232, 93, 93, 20)
            elif row_style == 'fake_breakout':
                return QColor(245, 158, 11, 20)
            elif row_style == 'approaching':
                return QColor(139, 92, 246, 20)
            elif row_style == 'warning':
                return QColor(239, 68, 68, 15)
            elif row_style == 'vcp':
                return QColor(59, 130, 246, 15)
            return None

        elif role == Qt.ItemDataRole.UserRole:
            import re
            s_val = str(raw_val).replace(',', '')
            # try to parse numeric values strongly
            if '万' in s_val:
                m = re.search(r'([-+]?\d*\.?\d+)', s_val)
                if m: return float(m.group(1)) * 10000
                return 0.0
            if '亿' in s_val:
                m = re.search(r'([-+]?\d*\.?\d+)', s_val)
                if m: return float(m.group(1)) * 100000000
                return 0.0
            
            m = re.search(r'([-+]?\d*\.?\d+)', s_val)
            if m:
                return float(m.group(1))
            return str(raw_val)

        elif role == Qt.ItemDataRole.UserRole + 1:
            return self._flash_records.get(row, {}).get(col, None)

        elif role == Qt.ItemDataRole.UserRole + 2:
            if key == "突破状态" and str(raw_val) != "":
                st = str(raw_val)
                if "放量" in st: return QColor(COLOR_RISE_STRONG)
                elif "缩量" in st: return QColor(COLOR_WARNING)
                elif "临近" in st: return QColor(STATUS_APPROACHING)
                elif "VCP" in st: return QColor(STATUS_VCP)
                else: return QColor(STATUS_INACTIVE)
            if key == "评分" and str(raw_val) != "":
                try:
                    score = float(str(raw_val))
                    if score >= 90: return QColor(SCORE_EXCELLENT)
                    elif score >= 80: return QColor(SCORE_GOOD)
                    elif score >= 60: return QColor(SCORE_NORMAL)
                    else: return QColor(SCORE_LOW)
                except (ValueError, TypeError):
                    pass

        return None

    def headerData(self, section, orientation, role):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._headers[section]
        return None
