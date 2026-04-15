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
from functools import lru_cache
from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel, QRect, pyqtSignal, QMimeData
from PyQt6.QtGui import QColor, QFont
from core.buy_point import BUY_POINT_STYLE_TEXT, calculate_buy_point_from_history
from ui.components import SearchFilter
from ui.theme_tokens import build_ui_tokens

_log = logging.getLogger(__name__)
from ui.theme import theme_manager

SERIAL_HEADER = "序号"

def _current_table_density():
    try:
        from core.app_config import app_config

        return getattr(app_config, "table_density", None)
    except Exception:
        return None


# 运行时动态获取当前主题颜色（不再用 from import 快照，否则切换主题后颜色不更新）
@lru_cache(maxsize=128)
def _theme_token_cached(theme_name: str, token: str) -> str:
    theme = theme_manager.THEMES.get(theme_name, theme_manager.current_theme)
    return theme.get(token, "")


def _c(token: str) -> str:
    return _theme_token_cached(theme_manager.current_theme_name, token)


@lru_cache(maxsize=8)
def _theme_table_tokens_cached(theme_name: str, density: str | None) -> dict:
    theme = theme_manager.THEMES.get(theme_name, theme_manager.current_theme)
    return build_ui_tokens(theme=theme, density=density)["table"]


def _theme_table_tokens() -> dict:
    return _theme_table_tokens_cached(theme_manager.current_theme_name, _current_table_density())


@lru_cache(maxsize=512)
def _qcolor_from_text_cached(text: str) -> QColor:
    rgba_match = re.fullmatch(
        r"rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*([0-9]*\.?[0-9]+)\s*\)",
        text,
        re.IGNORECASE,
    )
    if rgba_match:
        r, g, b = (int(rgba_match.group(i)) for i in range(1, 4))
        alpha_raw = float(rgba_match.group(4))
        alpha = int(round(alpha_raw * 255)) if alpha_raw <= 1 else int(round(alpha_raw))
        return QColor(r, g, b, max(0, min(255, alpha)))

    rgb_match = re.fullmatch(
        r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)",
        text,
        re.IGNORECASE,
    )
    if rgb_match:
        return QColor(*(int(rgb_match.group(i)) for i in range(1, 4)))

    return QColor(text)


def _qcolor_from_token(color) -> QColor:
    if isinstance(color, QColor):
        return QColor(color)

    if color is None:
        return QColor()

    text = str(color).strip()
    if not text:
        return QColor()
    return QColor(_qcolor_from_text_cached(text))


def _parse_numeric_value(raw_val):
    if raw_val is None:
        return None
    text = str(raw_val).strip().replace(",", "")
    if not text or text in {"--", "-"}:
        return None
    match = re.search(r"[-+]?\d*\.?\d+", text)
    if not match:
        return None
    try:
        value = float(match.group(0))
    except (TypeError, ValueError):
        return None

    if "万" in text:
        return value * 10000
    if "亿" in text:
        return value * 100000000
    return value


def _is_status_header(header: str) -> bool:
    return header in {
        SERIAL_HEADER,
        "突破状态",
        "状态",
        "买点",
        "关注",
        "市场",
    }


def _is_date_like_header(header: str) -> bool:
    if header in {
        "揭晓日",
        "报告期",
        "最近上榜",
        "上榜日",
        "公告日",
    }:
        return True
    return any(keyword in header for keyword in ("日期", "时间"))


def _is_numeric_header(header: str) -> bool:
    if header == SERIAL_HEADER or _is_status_header(header):
        return False
    keywords = (
        "%",
        "价",
        "额",
        "量",
        "值",
        "分",
        "幅",
        "RPS",
        "评分",
        "强度",
        "净买",
        "振幅",
        "换手",
        "流通",
        "成交",
    )
    return any(keyword in header for keyword in keywords)


def _numeric_heat_color(header: str, raw_val):
    value = _parse_numeric_value(raw_val)
    if value is None:
        return None

    tokens = _theme_table_tokens()
    alpha = 0
    base = None

    if "%" in header or header in {"涨跌", "现价", "收盘", "最新价"}:
        if abs(value) < 0.01:
            return None
        base = _c("COLOR_RISE") if value > 0 else _c("COLOR_FALL")
        alpha = min(tokens["numeric_heat_max_alpha"], max(8, int(abs(value) * 2.6)))
    elif header == "评分":
        if value >= 90:
            base = _c("SCORE_EXCELLENT")
            alpha = 28
        elif value >= 80:
            base = _c("SCORE_GOOD")
            alpha = 22
        elif value >= 60:
            base = _c("SCORE_NORMAL")
            alpha = 16
    elif "RPS" in header:
        if value >= 95:
            base = _c("COLOR_INFO")
            alpha = 24
        elif value >= 85:
            base = _c("COLOR_INFO")
            alpha = 16

    if not base or alpha <= 0:
        return None

    color = QColor(base)
    color.setAlpha(alpha)
    return color


@lru_cache(maxsize=2048)
def _build_cell_tooltip_cached(text: str):
    if not text:
        return None

    wrapped_lines = []
    for line in text.splitlines() or [text]:
        wrapped_lines.append(textwrap.fill(line, width=50) if len(line) > 40 else line)
    return "\n".join(wrapped_lines)


def _build_cell_tooltip(raw_val):
    """统一表格悬浮提示文本，交给 QToolTip 自身样式渲染。"""
    text = str(raw_val).strip()
    return _build_cell_tooltip_cached(text)


_DYNAMIC_ELIDE_HEADERS = {
    "\u5916\u8d44\u51c0\u4e70\u5165",
    "\u4e70\u65b9\u8425\u4e1a\u90e8",
    "\u5356\u65b9\u8425\u4e1a\u90e8",
    "\u4ea4\u6613\u8be6\u60c5",
    "\u89d2\u8272\u5b9a\u4f4d",
    "\u4ea7\u4e1a\u94fe\u5730\u4f4d",
}


def _summarize_long_text(header: str, raw_val):
    text = str(raw_val or "").strip()
    if not text:
        return text
    if str(header) not in _DYNAMIC_ELIDE_HEADERS:
        return text
    return " | ".join(part.strip() for part in text.splitlines() if part.strip()) or text


def _status_badge_color(text: str, header: str | None = None):
    st = str(text or "").strip()
    if not st or st in ("--", "-"):
        return None
    if header == "状态":
        if any(keyword in st for keyword in ("盘中", "交易中", "开盘")):
            return _c("COLOR_SUCCESS")
        if any(keyword in st for keyword in ("休市", "收盘", "闭市")):
            return _c("COLOR_WARNING")
        return _c("COLOR_INFO")
    if header == "买点":
        if any(keyword in st for keyword in ("触发", "确认", "✅")):
            return _c("COLOR_RISE_STRONG")
        return _c("COLOR_RISE")
    if "假突破" in st or "缩量" in st:
        return _c("COLOR_ERROR")
    if "临近" in st or "关注" in st:
        return _c("COLOR_WARNING")
    if "突破" in st:
        return _c("COLOR_SUCCESS")
    return None


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]", text or ""))


def _normalized_alignment_text(header: str, raw_val) -> str:
    text = str(raw_val or "").strip()
    if not text:
        return ""

    if "日" in header or "期" in header or "时间" in header:
        normalized = text.split(" ")[0].replace("-", "").replace("/", "")
        if normalized.isdigit():
            return normalized

    return text


def _is_numeric_like_text(text: str) -> bool:
    if not text:
        return False
    if _contains_cjk(text):
        return False

    normalized = re.sub(r"[\s,\+\-\.:%/]", "", text)
    return bool(normalized) and normalized.isdigit()


def _alignment_for_cell(header: str, raw_val):
    if header == SERIAL_HEADER:
        return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

    text = _normalized_alignment_text(header, raw_val)
    if not text or text in {"--", "-"}:
        if _is_numeric_header(header) or _is_date_like_header(header):
            return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    if _is_numeric_like_text(text):
        return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

    return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


def _with_serial_header(headers):
    header_list = list(headers or [])
    if not header_list or header_list[0] != SERIAL_HEADER:
        header_list.insert(0, SERIAL_HEADER)
    return header_list


def _sync_serial_values(rows):
    for idx, item in enumerate(rows or [], 1):
        if isinstance(item, dict):
            item[SERIAL_HEADER] = idx


def _emit_model_row_ranges(model, changed_rows, start_col: int, end_col: int, roles):
    if not changed_rows:
        return

    start_row = prev_row = changed_rows[0]
    for row in changed_rows[1:]:
        if row == prev_row + 1:
            prev_row = row
            continue
        model.dataChanged.emit(model.index(start_row, start_col), model.index(prev_row, end_col), roles)
        start_row = prev_row = row

    model.dataChanged.emit(model.index(start_row, start_col), model.index(prev_row, end_col), roles)

class RtTableModel(QAbstractTableModel):
    def __init__(self, data=None):
        super().__init__()
        self._data = data or []
        self._headers = _with_serial_header(["代码", "名称", "现价", "涨幅%", "市值", "时间", "评分", "RPS强度", "突破状态", "区间振幅", "热点板块"])
        self.base_font = QFont()
        self.base_font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "SimSun"])
        self.base_font.setPointSize(12)
        self.base_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)

        self.mono_font = QFont()
        self.mono_font.setFamilies(["Consolas", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "SimSun"])
        self.mono_font.setPointSize(12) # 从10调大10%至11
        self.mono_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        
        self.bold_font = QFont()
        self.bold_font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "SimSun"])
        self.bold_font.setPointSize(12)
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
        _sync_serial_values(self._data)
        self.endResetModel()

    def _emit_row_update_ranges(self, changed_rows):
        if not changed_rows:
            return

        roles = [
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
            Qt.ItemDataRole.BackgroundRole,
            Qt.ItemDataRole.ForegroundRole,
            Qt.ItemDataRole.FontRole,
            Qt.ItemDataRole.TextAlignmentRole,
        ]
        start_row = prev_row = changed_rows[0]
        last_column = self.columnCount() - 1

        for row in changed_rows[1:]:
            if row == prev_row + 1:
                prev_row = row
                continue
            self.dataChanged.emit(self.index(start_row, 0), self.index(prev_row, last_column), roles)
            start_row = prev_row = row

        self.dataChanged.emit(self.index(start_row, 0), self.index(prev_row, last_column), roles)

    def update_rows_incremental(self, new_data):
        normalized_rows = [dict(item) for item in (new_data or [])]
        _sync_serial_values(normalized_rows)

        if len(normalized_rows) != len(self._data):
            self.update_data(normalized_rows)
            return False

        old_codes = [row.get("代码") for row in self._data]
        new_codes = [row.get("代码") for row in normalized_rows]
        if old_codes != new_codes:
            self.update_data(normalized_rows)
            return False

        changed_rows = []
        for row_idx, new_row in enumerate(normalized_rows):
            if self._data[row_idx] != new_row:
                self._data[row_idx] = new_row
                changed_rows.append(row_idx)

        self._emit_row_update_ranges(changed_rows)
        return True

    def get_row_data(self, row):
        if 0 <= row < len(self._data):
            return self._data[row]
        return {}

    def update_quotes(self, quotes: dict):
        if not quotes or not self._data:
            return
        try:
            col_price_idx = self._headers.index("现价")
            col_pct_idx = self._headers.index("涨幅%")
        except ValueError:
            return

        try:
            col_cap_idx = self._headers.index("市值")
            start_col = min(col_price_idx, col_pct_idx, col_cap_idx)
            end_col = max(col_price_idx, col_pct_idx, col_cap_idx)
        except ValueError:
            col_cap_idx = None
            start_col = min(col_price_idx, col_pct_idx)
            end_col = max(col_price_idx, col_pct_idx)

        changed_rows = []
        for row, item_dict in enumerate(self._data):
            code = item_dict.get("代码")
            if not code or code not in quotes:
                continue

            q = quotes[code]
            rt_close = float(q.get('close', 0) or 0)
            last_close = float(q.get('last_close', 0) or 0)

            if rt_close <= 0 and last_close > 0:
                rt_close = last_close

            row_changed = False
            if last_close > 0 and rt_close > 0:
                pct = ((rt_close / last_close) - 1) * 100
                if item_dict.get("涨幅%") != pct:
                    item_dict["涨幅%"] = pct
                    row_changed = True

            if rt_close > 0:
                price_text = f"{rt_close:.2f}"
                if item_dict.get("现价") != price_text:
                    item_dict["现价"] = price_text
                    row_changed = True

            zbg = item_dict.get("_zongguben", 0)
            if zbg > 0 and rt_close > 0:
                cap = zbg * rt_close
                cap_text = f"{cap / 1e8:.0f}亿"
                if item_dict.get("市值") != cap_text:
                    item_dict["市值"] = cap_text
                    row_changed = True

            if row_changed:
                changed_rows.append(row)

        _emit_model_row_ranges(
            self,
            changed_rows,
            start_col,
            end_col,
            [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ForegroundRole, Qt.ItemDataRole.BackgroundRole],
        )

    def data(self, index, role):
        if not index.isValid():
            return None
            
        row = index.row()
        col = index.column()
        item_dict = self._data[row]
        key = self._headers[col]
        raw_val = item_dict.get(key, '')

        if role == Qt.ItemDataRole.DisplayRole:
            if key == SERIAL_HEADER:
                return str(row + 1)
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

            return _summarize_long_text(key, raw_val)

        elif role == Qt.ItemDataRole.ToolTipRole:
            if key == SERIAL_HEADER:
                return None
            if key == "外资净买入":
                custom_tip = item_dict.get("_外资净买入_tooltip")
                if custom_tip:
                    return custom_tip
            return _build_cell_tooltip(raw_val)

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            return _alignment_for_cell(key, raw_val)

        elif role == Qt.ItemDataRole.FontRole:
            if key == SERIAL_HEADER:
                return self.mono_font
            if _is_numeric_header(key) or _is_date_like_header(key):
                return self.mono_font
            if key in ["涨幅%", "市值", "时间", "评分", "突破状态", "区间振幅"]:
                return self.mono_font
            if key == "突破状态":
                st = str(raw_val)
                if "放量突破" in st or "缩量突破" in st:
                    return self.bold_font
            return self.base_font

        elif role == Qt.ItemDataRole.ForegroundRole:
            if key == SERIAL_HEADER:
                return QColor(_c("TEXT_SECONDARY"))
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

            elif key == "外资净买入":
                try:
                    f_val = float(item_dict.get("外资净买(万)", 0) or 0)
                    if f_val > 0:
                        return QColor(_c("COLOR_RISE"))
                    if f_val < 0:
                        return QColor(_c("COLOR_FALL"))
                    return QColor(_c("TEXT_SECONDARY"))
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
            heat_color = _numeric_heat_color(key, raw_val)
            if heat_color is not None:
                return heat_color

        elif role == Qt.ItemDataRole.UserRole + 2:
            if _is_status_header(key):
                badge = _status_badge_color(raw_val, key)
                if badge:
                    return badge

        elif role == Qt.ItemDataRole.UserRole:
            # specifically for sorting numerical columns
            if key == SERIAL_HEADER:
                return row + 1

            s_val = str(raw_val).replace(',', '')
            if key in ["市值", "评分"] or "万" in s_val or "亿" in s_val:
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

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        source = self.sourceModel()
        header = source.headerData(index.column(), Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) if source else None
        if header == SERIAL_HEADER:
            if role == Qt.ItemDataRole.DisplayRole:
                return str(index.row() + 1)
            if role == Qt.ItemDataRole.UserRole:
                return index.row() + 1
            if role == Qt.ItemDataRole.ToolTipRole:
                return None
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(_c("TEXT_SECONDARY"))
        return super().data(index, role)

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        if column >= 0:
            source = self.sourceModel()
            header = source.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) if source else None
            if header == SERIAL_HEADER:
                return
        super().sort(column, order)

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
from PyQt6.QtGui import QPainter, QPen, QBrush, QPalette

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
        table_tokens = _theme_table_tokens()
        
        # 0. 绘制基础默认背景（借用系统的绘制，并屏蔽默认文字）
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = option.widget
        style = widget.style() if widget else QApplication.style()
        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        show_selected_rail = is_selected and index.column() == 0
        selected_rail_width = table_tokens["selected_rail_width"] if show_selected_rail else 0
        current_index = widget.currentIndex() if widget and hasattr(widget, "currentIndex") else QModelIndex()
        is_current = current_index.isValid() and current_index == index
        plain_style_cell = bool(index.data(Qt.ItemDataRole.UserRole + 3))
        sorted_column = widget.sorted_column() if widget and hasattr(widget, "sorted_column") else -1
        sorted_overlay = None
        if not is_selected and not plain_style_cell and sorted_column == index.column():
            sorted_overlay = _qcolor_from_token(table_tokens["sorted_column_bg"])

        def draw_current_cell_indicator():
            if not is_current:
                return

            left_inset = 2 + selected_rail_width + (2 if show_selected_rail else 0)
            indicator_rect = option.rect.adjusted(left_inset, 2, -2, -2)
            if indicator_rect.width() <= 4 or indicator_rect.height() <= 4:
                return

            fill_token = "current_cell_bg_selected" if is_selected else "current_cell_bg"
            fill_color = _qcolor_from_token(table_tokens[fill_token])
            border_color = _qcolor_from_token(table_tokens["current_cell_border"])

            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(fill_color))
            painter.drawRoundedRect(indicator_rect, 4, 4)

            pen = QPen(border_color)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            painter.drawRoundedRect(indicator_rect, 4, 4)

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
            if sorted_overlay is not None:
                painter.fillRect(option.rect, sorted_overlay)
            if show_selected_rail:
                rail_rect = QRect(
                    option.rect.left(),
                    option.rect.top() + 1,
                    selected_rail_width,
                    max(0, option.rect.height() - 2),
                )
                painter.fillRect(rail_rect, QColor(_c("BRAND_PRIMARY")))
            draw_current_cell_indicator()
            rect = option.rect
            painter.setFont(opt.font)
            fm = painter.fontMetrics()
            text_width = fm.horizontalAdvance(str(text))
            text_height = fm.height()
            
            # 胶囊边界框
            pad_x = 12
            pad_y = 6
            # 计算剧中或靠左的绘制位置
            align = index.data(Qt.ItemDataRole.TextAlignmentRole)
            draw_rect = QRect(0, 0, text_width + pad_x, text_height + pad_y)
            if align and (align & Qt.AlignmentFlag.AlignLeft.value):
                draw_rect.moveCenter(rect.center())
                draw_rect.moveLeft(rect.left() + 8 + selected_rail_width + (4 if show_selected_rail else 0))
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
            opt_bg = QStyleOptionViewItem(opt)
            opt_bg.text = ""
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt_bg, painter, widget)
            if sorted_overlay is not None:
                painter.fillRect(option.rect, sorted_overlay)
            if show_selected_rail:
                rail_rect = QRect(
                    option.rect.left(),
                    option.rect.top() + 1,
                    selected_rail_width,
                    max(0, option.rect.height() - 2),
                )
                painter.fillRect(rail_rect, QColor(_c("BRAND_PRIMARY")))
            draw_current_cell_indicator()
            left_padding = 8 + selected_rail_width + (4 if show_selected_rail else 0)
            text_rect = option.rect.adjusted(left_padding, 0, -8, 0)

            font = index.data(Qt.ItemDataRole.FontRole)
            if isinstance(font, QFont):
                painter.setFont(font)
            else:
                painter.setFont(opt.font)

            text_color = index.data(Qt.ItemDataRole.ForegroundRole)
            if not isinstance(text_color, QColor):
                color_role = QPalette.ColorRole.HighlightedText if is_selected else QPalette.ColorRole.Text
                text_color = opt.palette.color(color_role)

            alignment = index.data(Qt.ItemDataRole.TextAlignmentRole)
            if alignment is None:
                alignment = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            elided_text = painter.fontMetrics().elidedText(
                str(text or ""),
                opt.textElideMode,
                max(0, text_rect.width() - 2),
            )
            painter.setPen(QPen(text_color))
            painter.drawText(text_rect, alignment, elided_text)

        painter.restore()


class StockTableModel(QAbstractTableModel):
    sig_rows_reordered = pyqtSignal(list)

    def __init__(self, headers, data=None):
        super().__init__()
        self._headers = _with_serial_header(headers)
        self._data = data or []
        self._flash_records = {} # row -> {col: {"time": stamp, "diff": val}}
        self._plain_style_headers = set()

        self.base_font = QFont()
        self.base_font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "SimSun"])
        self.base_font.setPointSize(12)
        self.base_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)

        self.mono_font = QFont()
        self.mono_font.setFamilies(["Consolas", "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "SimSun"])
        self.mono_font.setPointSize(12) # 从10调大10%至11
        self.mono_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        
        self.bold_font = QFont()
        self.bold_font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "SimSun"])
        self.bold_font.setPointSize(12)
        self.bold_font.setBold(True)

    @property
    def row_data(self):
        return self._data

    @property
    def headers(self):
        return self._headers

    def set_plain_style_headers(self, headers):
        self._plain_style_headers = {str(header) for header in (headers or []) if str(header).strip()}

    def _uses_plain_style(self, header: str) -> bool:
        return header in self._plain_style_headers

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
        _sync_serial_values(self._data)
        self._flash_records.clear()
        self.endResetModel()
        self._hydrate_latest_quotes_from_store()

    def _hydrate_latest_quotes_from_store(self):
        """表格数据落地后，立刻吃一口全局最新行情快照，避免新 Tab 干等下一轮轮询。"""
        if not self._data or "代码" not in self._headers:
            return

        if not any(header in self._headers for header in ("现价", "涨幅%", "市值", "买点")):
            return

        try:
            from core.global_store import global_store

            snapshot = global_store.get_latest_quotes()
        except Exception:
            return

        if not snapshot:
            return

        self.update_quotes(snapshot)
        # 首次建表吃快照属于初始化补齐，不需要保留闪烁态。
        self._flash_records.clear()

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

    def set_cell_value(self, row, col_name, new_val, emit_signal: bool = True):
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
            
            if emit_signal:
                try:
                    col_idx = self._headers.index(col_name)
                    idx = self.index(row, col_idx)
                    self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
                except ValueError:
                    pass

    def update_quotes(self, quotes: dict):
        """批量更新现价与涨幅并触发局部刷新，解决主线程卡顿"""
        if not quotes or not self._data:
            return

        changed_rows = []
        quote_cols = []
        for header in ("现价", "涨幅%", "市值"):
            if header in self._headers:
                quote_cols.append(self._headers.index(header))
        start_col = min(quote_cols) if quote_cols else None
        end_col = max(quote_cols) if quote_cols else None

        for row, item_dict in enumerate(self._data):
            code = item_dict.get("代码")
            if not code or code not in quotes:
                continue

            q = quotes[code]
            rt_close = float(q.get('close', 0) or 0)
            last_close = float(q.get('last_close', 0) or 0)

            if rt_close <= 0 and last_close > 0:
                rt_close = last_close

            if last_close > 0 and rt_close > 0:
                pct = ((rt_close / last_close) - 1) * 100
            else:
                pct = 0

            row_changed = False
            price_text = f"{rt_close:.2f}" if rt_close > 0 else "--"
            if item_dict.get("现价") != price_text:
                self.set_cell_value(row, "现价", price_text, emit_signal=False)
                row_changed = True
            if item_dict.get("涨幅%") != pct:
                self.set_cell_value(row, "涨幅%", pct, emit_signal=False)
                row_changed = True

            if "市值" in self._headers:
                zbg = item_dict.get("_zongguben", 0)
                if zbg > 0 and rt_close > 0:
                    cap = zbg * rt_close
                    cap_text = f"{cap / 1e8:.0f}亿"
                    if item_dict.get("市值") != cap_text:
                        self.set_cell_value(row, "市值", cap_text, emit_signal=False)
                        row_changed = True

            if row_changed:
                changed_rows.append(row)

            if "买点" in self._headers and rt_close > 0:
                history = item_dict.get("_history_20", [])
                history_date = item_dict.get("_history_date", "")
                pos_str = ""
                
                if history:
                    import datetime
                    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
                    now_time = datetime.datetime.now().strftime("%H:%M")
                    
                    if history_date == today_str:
                        # 内存里已经是最新的未完成 K 线（说明早上开盘后拉取过） -> 用现价替换最后一根
                        temp_hist = history[:-1] + [rt_close]
                    else:
                        # 内存里 K 线停留在昨天或更早
                        from core.market_calendar import MarketCalendar
                        # 如果是今天早盘 09:15 分以后，并且今天正是法定交易日，说明进入了新的一天，现价是“新长出来”的第 21 根 K 线
                        # 如果不是，说明系统停在盘中或非交易日（如双休日），仅仅作为静态复盘的实时跳动预览（替换最后一根避免复制叠加）
                        if now_time >= "09:15" and MarketCalendar.is_trade_day(today_str):
                            temp_hist = history[1:] + [rt_close]
                        else:
                            # e.g 凌晨，或者周末，现价仅仅用于代替最后一根避免K线叠加
                            temp_hist = history[:-1] + [rt_close]
                            
                    # 获取当天的开盘价
                    rt_open = float(q.get('open') or rt_close)
                    pos_str = calculate_buy_point_from_history(
                        history=temp_hist,
                        open_price=rt_open,
                        close_price=rt_close,
                        style=BUY_POINT_STYLE_TEXT,
                    )

                if pos_str != item_dict.get("买点", ""):
                    self.set_cell_value(row, "买点", pos_str)

        if start_col is not None and end_col is not None:
            _emit_model_row_ranges(
                self,
                changed_rows,
                start_col,
                end_col,
                [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ForegroundRole, Qt.ItemDataRole.BackgroundRole],
            )


    def data(self, index, role):
        if not index.isValid():
            return None
            
        row = index.row()
        col = index.column()
        item_dict = self._data[row]
        key = self._headers[col]
        raw_val = item_dict.get(key, '')

        if role == Qt.ItemDataRole.DisplayRole:
            if key == SERIAL_HEADER:
                return str(row + 1)
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

            return _summarize_long_text(key, raw_val)

        elif role == Qt.ItemDataRole.ToolTipRole:
            if key == SERIAL_HEADER:
                return None
            if key == "外资净买入":
                custom_tip = item_dict.get("_外资净买入_tooltip")
                if custom_tip:
                    return custom_tip
            return _build_cell_tooltip(raw_val)

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            return _alignment_for_cell(key, raw_val)

        elif role == Qt.ItemDataRole.FontRole:
            if key == SERIAL_HEADER:
                return self.mono_font
            if _is_numeric_header(key) or _is_date_like_header(key):
                return self.mono_font
            return self.base_font

        elif role == Qt.ItemDataRole.ForegroundRole:
            if key == SERIAL_HEADER:
                return QColor(_c("TEXT_SECONDARY"))
            if key == "名称":
                from ui.viewmodels.watchlist_vm import watchlist_vm
                code = str(item_dict.get("代码", ""))
                if watchlist_vm.is_in_watchlist(code):
                    return QColor("#E879F9")  # 醒目紫粉色，用于标示自选股
            if self._uses_plain_style(key) or _is_date_like_header(key):
                return QColor(_c("TEXT_PRIMARY"))
                    
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
                    else: return QColor(_c("COLOR_FLAT"))
                except (ValueError, TypeError):
                    return QColor(_c("COLOR_FLAT"))
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
                if "对倒" in val_str:
                    return QColor("#F59E0B")
                elif "买/" in val_str or "/卖" in val_str:
                    return QColor("#3B82F6")
                elif "卖出" in val_str:
                    return QColor(_c("COLOR_FALL"))  # 卖出标为绿
                elif "买入" in val_str:
                    return QColor(_c("COLOR_RISE"))  # 买入标为红
                    
            elif key == "成交金额(万元)":
                try:
                    f_val = float(str(raw_val).replace(',', ''))
                    if f_val >= 10000:
                        return QColor(_c("COLOR_RISE"))
                except (ValueError, TypeError):
                    pass
                    
            elif key in ["上榜净买额(万)", "机构净买(万)"]:
                try:
                    f_val = float(raw_val)
                    if f_val > 0: return QColor(_c("COLOR_RISE"))
                    elif f_val < 0: return QColor(_c("COLOR_FALL"))
                except (ValueError, TypeError):
                    pass

            elif key == "外资净买入":
                try:
                    f_val = float(item_dict.get("外资净买(万)", 0) or 0)
                    if f_val > 0:
                        return QColor(_c("COLOR_RISE"))
                    if f_val < 0:
                        return QColor(_c("COLOR_FALL"))
                    return QColor(_c("TEXT_SECONDARY"))
                except (ValueError, TypeError):
                    pass
                     
            elif key == "外资潜伏池":
                try:
                    fz_val = float(item_dict.get("外资净买(万)", 0))
                    if fz_val > 0: return QColor(_c("COLOR_RISE"))
                    elif fz_val < 0: return QColor(_c("COLOR_FALL"))
                except (ValueError, TypeError):
                    pass
                    
            elif key == "突破状态" and str(raw_val) != "":
                st = str(raw_val)
                if "放量" in st: return QColor(_c("COLOR_RISE_STRONG"))
                elif "缩量" in st: return QColor(_c("COLOR_WARNING"))
                elif "临近" in st: return QColor(_c("STATUS_APPROACHING"))
                elif "VCP" in st: return QColor(_c("STATUS_VCP"))
                else: return QColor(_c("STATUS_INACTIVE"))
                
            elif key == "股价弹性" and str(raw_val) != "":
                st = str(raw_val)
                if "高" in st: return QColor(_c("COLOR_RISE"))
                else: return QColor(_c("TEXT_PRIMARY"))
                
            elif key == "评分" and str(raw_val) != "":
                try:
                    score = float(str(raw_val))
                    if score >= 90: return QColor(_c("SCORE_EXCELLENT"))
                    elif score >= 80: return QColor(_c("SCORE_GOOD"))
                    elif score >= 60: return QColor(_c("SCORE_NORMAL"))
                    else: return QColor(_c("SCORE_LOW"))
                except (ValueError, TypeError):
                    pass
                    
            return QColor(_c("TEXT_PRIMARY"))

        elif role == Qt.ItemDataRole.BackgroundRole:
            if self._uses_plain_style(key):
                return None
            heat_color = _numeric_heat_color(key, raw_val)
            if heat_color is not None:
                return heat_color
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
            if key == SERIAL_HEADER:
                return row + 1
            if key == "外资净买入":
                try:
                    return float(item_dict.get("外资净买(万)", 0) or 0)
                except (ValueError, TypeError):
                    return 0.0
            if key == "最近上榜":
                raw_date = str(item_dict.get("_最近上榜_raw", "") or raw_val).strip()
                if re.fullmatch(r'\d{8}', raw_date):
                    return int(raw_date)
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
            if not self._uses_plain_style(key) and _is_status_header(key):
                return _status_badge_color(raw_val, key)
            return None

        elif role == Qt.ItemDataRole.UserRole + 3:
            return self._uses_plain_style(key)

        return None

    def headerData(self, section, orientation, role):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._headers[section]
        return None
