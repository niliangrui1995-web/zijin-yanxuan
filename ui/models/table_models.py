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
import re
import logging
import textwrap
from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel, QRect, pyqtSignal, QMimeData
from PyQt6.QtGui import QColor, QFont
from ui.components import SearchFilter

_log = logging.getLogger(__name__)
from ui.theme import theme_manager

# 运行时动态获取当前主题颜色（不再用 from import 快照，否则切换主题后颜色不更新）
def _c(token: str) -> str:
    return theme_manager.get(token)


def _build_cell_tooltip(raw_val):
    """统一表格悬浮提示文本，交给 QToolTip 自身样式渲染。"""
    text = str(raw_val).strip()
    if not text:
        return None

    wrapped_lines = []
    for line in text.splitlines() or [text]:
        wrapped_lines.append(textwrap.fill(line, width=50) if len(line) > 40 else line)
    return "\n".join(wrapped_lines)

class RtTableModel(QAbstractTableModel):
    def __init__(self, data=None):
        super().__init__()
        self._data = data or []
        self._headers = ["代码", "名称", "现价", "涨幅%", "市值", "时间", "评分", "RPS强度", "突破状态", "区间振幅", "热点板块"]
        self.base_font = QFont()
        self.base_font.setFamilies(["SimSun", "宋体"])
        self.base_font.setPointSize(11)
        self.base_font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)

        self.mono_font = QFont()
        self.mono_font.setFamilies(["SimSun", "宋体"])
        self.mono_font.setPointSize(11) # 从10调大10%至11
        self.mono_font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
        
        self.bold_font = QFont()
        self.bold_font.setFamilies(["SimSun", "宋体"])
        self.bold_font.setPointSize(11)
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
                item_dict["涨幅%"] = pct
                
            if rt_close > 0:
                item_dict["现价"] = f"{rt_close:.2f}"
            
            zbg = item_dict.get("_zongguben", 0)
            if zbg > 0 and rt_close > 0:
                cap = zbg * rt_close
                item_dict["市值"] = f"{cap / 1e8:.0f}亿"
                try:
                    col_cap_idx = self._headers.index("市值")
                    idx_start = self.index(row, min(col_price_idx, col_cap_idx))
                    idx_end = self.index(row, max(col_pct_idx, col_cap_idx))
                except ValueError:
                    idx_start = self.index(row, col_price_idx)
                    idx_end = self.index(row, col_pct_idx)
            else:
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

            if "%" in key:
                s_val = str(raw_val)
                if s_val == "--" or s_val == "": return s_val
                if s_val.endswith("%"): return s_val
                try:
                    f_val = float(s_val.replace('%', ''))
                    if "换手" in key: return f"{f_val:.2f}%"
                    return f"{f_val:+.2f}%"
                except (ValueError, TypeError):
                    pass
                    
            if key in ["现价", "市价"]:
                try:
                    f_val = float(raw_val)
                    if f_val <= 0: return "--"
                    # 小于10元的小市值精确到3位，大多数精确到2位
                    return f"{f_val:.3f}" if f_val < 10 else f"{f_val:.2f}"
                except (ValueError, TypeError):
                    pass
                    
            return str(raw_val)

        elif role == Qt.ItemDataRole.ToolTipRole:
            return _build_cell_tooltip(raw_val)

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        elif role == Qt.ItemDataRole.FontRole:
            if col in [3, 4, 5, 6, 8, 9]:
                return self.mono_font
            if col == 7:
                st = str(raw_val)
                if "放量突破" in st or "缩量突破" in st:
                    return self.bold_font
            return self.base_font

        elif role == Qt.ItemDataRole.ForegroundRole:
            # Bug#4 修复: 按 header 名称匹配，不再硬编码列索引
            if "%" in key and "换手" not in key:
                try:
                    pct = float(str(raw_val).replace('%', '').replace('+', ''))
                    if pct >= 9.0: return QColor(_c("COLOR_RISE_STRONG"))
                    elif pct > 0: return QColor(_c("COLOR_RISE"))
                    elif pct <= -9.0: return QColor(_c("COLOR_FALL_STRONG"))
                    elif pct < 0: return QColor(_c("COLOR_FALL"))
                    else: return QColor(_c("COLOR_FLAT"))
                except (ValueError, TypeError):
                    return QColor(_c("COLOR_FLAT"))
            elif key == "突破状态":
                st = str(raw_val)
                if "放量突破" in st: return QColor(_c("COLOR_RISE_STRONG"))
                elif "缩量突破" in st: return QColor(_c("COLOR_WARNING"))
                elif "临近" in st: return QColor(_c("STATUS_APPROACHING"))
                elif "VCP蓄力" in st: return QColor(_c("STATUS_VCP"))
                elif "非红盘" in st or "异常" in st or "一字" in st or "观望" in st:
                    return QColor(_c("STATUS_INACTIVE"))
                    
            elif key in ["上榜净买额(万)", "机构净买(万)"]:
                try:
                    f_val = float(raw_val)
                    if f_val > 0: return QColor(_c("COLOR_RISE"))
                    elif f_val < 0: return QColor(_c("COLOR_FALL"))
                except (ValueError, TypeError):
                    pass
                    
            elif key == "外资潜伏池":
                try:
                    fz_val = float(item_dict.get("外资净买(万)", 0))
                    if fz_val > 0: return QColor(_c("COLOR_RISE"))
                    elif fz_val < 0: return QColor(_c("COLOR_FALL"))
                except (ValueError, TypeError):
                    pass
                
            return QColor(_c("TEXT_PRIMARY"))

        elif role == Qt.ItemDataRole.UserRole:
            # specifically for sorting numerical columns

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
        self._exact_column_filters = {}

    def setColumnFilter(self, col_name, text):
        if text:
            self._exact_column_filters[col_name] = text
        else:
            self._exact_column_filters.pop(col_name, None)
        self.invalidateFilter()

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
        model = self.sourceModel()
        
        # 1. 拦截层：表头精确定向筛选（模拟 Excel 表头筛选）
        if getattr(self, '_exact_column_filters', None):
            headers = model._headers if hasattr(model, '_headers') else []
            for col_name, pattern in self._exact_column_filters.items():
                if col_name in headers:
                    col_idx = headers.index(col_name)
                    idx = model.index(source_row, col_idx, source_parent)
                    val = str(model.data(idx, Qt.ItemDataRole.DisplayRole) or "")
                    if pattern not in val:
                        return False

        # 2. 全局拼音搜索层
        if not self._filter_text:
            return True
            
        headers = model._headers if hasattr(model, '_headers') else []
        code_col = headers.index("代码") if "代码" in headers else 0
        name_col = headers.index("名称") if "名称" in headers else 1
        code_idx = model.index(source_row, code_col, source_parent)
        name_idx = model.index(source_row, name_col, source_parent)
        
        c_text = str(model.data(code_idx, Qt.ItemDataRole.DisplayRole) or "").lower()
        n_text = str(model.data(name_idx, Qt.ItemDataRole.DisplayRole) or "").lower()
        
        if SearchFilter.match_pinyin_or_text(self._filter_text, c_text, n_text):
            return True
            
        # fallback to scan all cols if columns shifted
        for col in range(model.columnCount()):
            idx = model.index(source_row, col, source_parent)
            text = str(model.data(idx, Qt.ItemDataRole.DisplayRole) or "").lower()
            if self._filter_text in text:
                return True
                
        return False

    def mimeTypes(self):
        """确保 proxy 层声明的拖拽 MIME 类型与 source model 一致"""
        return ["application/x-watchlist-row"]

    def canDropMimeData(self, data, action, row, column, parent):
        """
        【关键修复】Qt 默认的 QSortFilterProxyModel.canDropMimeData 会先做
        index 映射预检，从上往下拖时映射常常失败，导致 drop 被静默拒绝。
        这里直接绕过那套映射，只检查 MIME 类型即可。
        """
        return data.hasFormat("application/x-watchlist-row")

    def supportedDropActions(self):
        """直接声明支持移动操作，防止 Qt 默认链路吞掉 drop 事件"""
        return Qt.DropAction.MoveAction

    def mimeData(self, indices):
        """
        拖拽发起时 Qt 会调用 proxy 的 mimeData。
        这里要把 proxy 行号 → 映射成 source 行号 → 编码到 MIME 里，
        确保 dropMimeData 收到的永远是 source model 的真实行号。
        """
        import json
        source = self.sourceModel()
        if not source:
            return QMimeData()

        source_rows = set()
        for proxy_idx in indices:
            src_idx = self.mapToSource(proxy_idx)
            if src_idx.isValid():
                source_rows.add(src_idx.row())

        mime = QMimeData()
        if source_rows:
            mime.setData("application/x-watchlist-row",
                         json.dumps(sorted(source_rows)).encode('utf-8'))
        return mime

    def dropMimeData(self, data, action, row, column, parent):
        """
        拖拽释放时的核心处理：
        row/parent 是 proxy 空间的坐标，需要映射到 source 空间后转发给 source model。
        """
        if self.sortColumn() != -1:
            return False

        source = self.sourceModel()
        if not source:
            return False

        # 把 proxy 空间的 drop 位置转换为 source 空间
        if row >= 0:
            # 拖到两行之间 → 映射 proxy row → source row
            if row < self.rowCount():
                src_idx = self.mapToSource(self.index(row, 0))
                source_row = src_idx.row() if src_idx.isValid() else source.rowCount()
            else:
                source_row = source.rowCount()
        elif parent.isValid():
            # 拖到某行上面 → 取该行的 source 位置
            src_idx = self.mapToSource(parent)
            source_row = src_idx.row() if src_idx.isValid() else source.rowCount()
        else:
            source_row = source.rowCount()

        return source.dropMimeData(data, action, source_row, column, QModelIndex())

import time
from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem, QApplication, QStyle
from PyQt6.QtGui import QPainter, QPen, QBrush

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
                color_hex = _c("COLOR_RISE_STRONG") if diff > 0 else _c("COLOR_FALL_STRONG")
                bg_color = QColor(color_hex)
                bg_color.setAlpha(min(80, max(0, int(alpha * 0.3))))
                painter.fillRect(option.rect, bg_color)
                
        # 2. 判断是否是自定义绘制的胶囊文本 (Pill)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        pill_color = index.data(Qt.ItemDataRole.UserRole + 2) # Pill Color Role
        
        if pill_color and text:
            opt_bg = QStyleOptionViewItem(opt)
            opt_bg.text = ""
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt_bg, painter, widget)
            rect = option.rect
            painter.setFont(opt.font)
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
    sig_rows_reordered = pyqtSignal(list)

    def __init__(self, headers, data=None):
        super().__init__()
        self._headers = headers
        self._data = data or []
        self._flash_records = {} # row -> {col: {"time": stamp, "diff": val}}

        self.base_font = QFont()
        self.base_font.setFamilies(["SimSun", "宋体"])
        self.base_font.setPointSize(11)
        self.base_font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)

        self.mono_font = QFont()
        self.mono_font.setFamilies(["SimSun", "宋体"])
        self.mono_font.setPointSize(11) # 从10调大10%至11
        self.mono_font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
        
        self.bold_font = QFont()
        self.bold_font.setFamilies(["SimSun", "宋体"])
        self.bold_font.setPointSize(11)
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
        self._flash_records.clear()
        self.endResetModel()

    def supportedDropActions(self):
        return Qt.DropAction.MoveAction

    def flags(self, index):
        default_flags = super().flags(index)
        if index.isValid():
            return default_flags | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        else:
            return default_flags | Qt.ItemFlag.ItemIsDropEnabled

    def mimeTypes(self):
        return ["application/x-watchlist-row"]

    def mimeData(self, indices):
        import json
        mime_data = QMimeData()
        rows = list(set([i.row() for i in indices]))
        if not rows: return mime_data
        data_str = json.dumps(rows).encode('utf-8')
        mime_data.setData("application/x-watchlist-row", data_str)
        return mime_data

    def dropMimeData(self, data, action, row, column, parent):
        if not data.hasFormat("application/x-watchlist-row"): return False
        import json
        try:
            drag_rows = sorted(json.loads(data.data("application/x-watchlist-row").data().decode('utf-8')))
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as _e:
            _log.debug(f"[拖拽] MIME 数据解析失败: {_e}")
            return False
            
        target_row = row
        if row == -1:
            target_row = parent.row() if parent.isValid() else self.rowCount()
        
        items_to_move = [self._data[r] for r in drag_rows]
        new_data = [item for i, item in enumerate(self._data) if i not in drag_rows]
        
        insert_row = target_row
        for r in drag_rows:
            if r < target_row:
                insert_row -= 1
                
        new_data[insert_row:insert_row] = items_to_move
        
        # We manually trigger the array rebuild and signal the VM
        codes = [d.get("代码") for d in new_data if d.get("代码")]
        self.sig_rows_reordered.emit(codes)
        
        # Returning False stops the view from doing standard Qt double-delete shenanigans,
        # but because we emit a signal that eventually rebuilds the table, it snaps visually correctly!
        return False

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
            
            if "市值" in self._headers:
                zbg = item_dict.get("_zongguben", 0)
                if zbg > 0 and rt_close > 0:
                    cap = zbg * rt_close
                    self.set_cell_value(row, "市值", f"{cap / 1e8:.0f}亿")


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

            if "日" in key or "期" in key or "时间" in key:
                s_val = str(raw_val).split(" ")[0].replace("-", "").replace("/", "")
                if len(s_val) == 8 and s_val.isdigit() and s_val.startswith("20"):
                    return s_val
                    
            if "%" in key:
                s_val = str(raw_val)
                if s_val == "--" or s_val == "": return s_val
                if s_val.endswith("%"): return s_val
                try:
                    f_val = float(s_val.replace('%', ''))
                    if "换手" in key: return f"{f_val:.2f}%"
                    return f"{f_val:+.2f}%"
                except (ValueError, TypeError):
                    pass
                    
            return str(raw_val)

        elif role == Qt.ItemDataRole.ToolTipRole:
            return _build_cell_tooltip(raw_val)

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        elif role == Qt.ItemDataRole.FontRole:
            if key in ["现价", "涨幅%", "量比", "换手率%", "区间振幅", "市值", "流通市值", "成交额", "评分"]:
                return self.mono_font
            return self.base_font

        elif role == Qt.ItemDataRole.ForegroundRole:
            if key == "名称":
                from ui.viewmodels.watchlist_vm import watchlist_vm
                code = str(item_dict.get("代码", ""))
                if watchlist_vm.is_in_watchlist(code):
                    return QColor("#E879F9")  # 醒目紫粉色，用于标示自选股
                    
            if ("%" in key and "换手" not in key) or key in ["涨跌", "净额", "现价", "收盘", "最新价"]:
                try:
                    target_pct = raw_val
                    if key in ["现价", "收盘", "最新价"]:
                        target_pct = item_dict.get("涨幅%") or item_dict.get("涨跌") or "0"
                        
                    pct = float(str(target_pct).replace('%', '').replace('+', '').replace(',', ''))
                    if pct >= 9.0: return QColor(_c("COLOR_RISE_STRONG"))
                    elif pct > 0: return QColor(_c("COLOR_RISE"))
                    elif pct <= -9.0: return QColor(_c("COLOR_FALL_STRONG"))
                    elif pct < 0: return QColor(_c("COLOR_FALL"))
                except (ValueError, TypeError):
                    pass
            elif key == "卖方营业部":
                val_str = str(raw_val)
                if any(kw in val_str for kw in ["高盛", "摩根大通", "摩根士丹利", "瑞银", "法巴", "渣打", "野村", "汇丰", "星展", "大和"]):
                    return QColor(_c("COLOR_FALL"))  # 外资卖出标为绿
            elif key == "买方营业部":
                val_str = str(raw_val)
                if any(kw in val_str for kw in ["高盛", "摩根大通", "摩根士丹利", "瑞银", "法巴", "渣打", "野村", "汇丰", "星展", "大和"]):
                    return QColor(_c("COLOR_RISE"))  # 外资买入标为红
            elif key == "交易详情":
                val_str = str(raw_val)
                if "卖出" in val_str:
                    return QColor(_c("COLOR_FALL"))  # 卖出标为绿
                elif "买入" in val_str:
                    return QColor(_c("COLOR_RISE"))  # 买入标为红
                    
            elif key in ["上榜净买额(万)", "机构净买(万)"]:
                try:
                    f_val = float(raw_val)
                    if f_val > 0: return QColor(_c("COLOR_RISE"))
                    elif f_val < 0: return QColor(_c("COLOR_FALL"))
                except (ValueError, TypeError):
                    pass
                    
            elif key == "外资潜伏池":
                try:
                    fz_val = float(item_dict.get("外资净买(万)", 0))
                    if fz_val > 0: return QColor(_c("COLOR_RISE"))
                    elif fz_val < 0: return QColor(_c("COLOR_FALL"))
                except (ValueError, TypeError):
                    pass
                    
            return QColor(_c("TEXT_PRIMARY"))

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
            s_val = str(raw_val).replace(',', '')

            if key == "日报时间":
                report_ts = int(item_dict.get("_report_ts", 0) or 0)
                row_rank = int(item_dict.get("_report_row_rank", 0) or 0)
                if report_ts:
                    # 复合排序值：
                    # 1. 战报时间戳越新越靠前
                    # 2. 同一时间戳内，战报原始顺序越靠前越靠前
                    return report_ts * 1000000 + max(0, 999999 - row_rank)
             
            # 日期格式识别：YYYY-MM-DD 或 YYYYMMDD，转为整数以正确排序
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s_val):
                return int(s_val.replace('-', ''))
            if re.fullmatch(r'\d{8}', s_val):
                return int(s_val)
            
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
                if "放量" in st: return QColor(_c("COLOR_RISE_STRONG"))
                elif "缩量" in st: return QColor(_c("COLOR_WARNING"))
                elif "临近" in st: return QColor(_c("STATUS_APPROACHING"))
                elif "VCP" in st: return QColor(_c("STATUS_VCP"))
                else: return QColor(_c("STATUS_INACTIVE"))
            if key == "评分" and str(raw_val) != "":
                try:
                    score = float(str(raw_val))
                    if score >= 90: return QColor(_c("SCORE_EXCELLENT"))
                    elif score >= 80: return QColor(_c("SCORE_GOOD"))
                    elif score >= 60: return QColor(_c("SCORE_NORMAL"))
                    else: return QColor(_c("SCORE_LOW"))
                except (ValueError, TypeError):
                    pass

        return None

    def headerData(self, section, orientation, role):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._headers[section]
        return None
