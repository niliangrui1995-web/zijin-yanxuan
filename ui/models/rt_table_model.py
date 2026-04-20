import re

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QColor, QFont

from ui.models.table_model_helpers import (
    SERIAL_HEADER,
    _alignment_for_cell,
    _apply_quote_metrics_to_row,
    _build_cell_tooltip,
    _c,
    _emit_model_row_ranges,
    _is_date_like_header,
    _is_numeric_header,
    _is_status_header,
    _numeric_heat_color,
    _status_badge_color,
    _summarize_long_text,
    _sync_serial_values,
    _with_serial_header,
)


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
        self.mono_font.setPointSize(12)
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
            start_col = min(col_price_idx, col_pct_idx)
            end_col = max(col_price_idx, col_pct_idx)

        changed_rows = []
        for row, item_dict in enumerate(self._data):
            code = item_dict.get("代码")
            if not code or code not in quotes:
                continue

            row_changed, _ = _apply_quote_metrics_to_row(item_dict, quotes[code])
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
                if s_val == "--" or s_val == "":
                    return s_val
                if s_val.endswith("%"):
                    return s_val
                try:
                    f_val = float(s_val.replace('%', ''))
                    if "换手" in key:
                        return f"{f_val:.2f}%"
                    return f"{f_val:+.2f}%"
                except (ValueError, TypeError):
                    pass

            if key in ["现价", "市价"]:
                try:
                    f_val = float(raw_val)
                    if f_val <= 0:
                        return "--"
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
            if "%" in key and "换手" not in key:
                try:
                    pct = float(str(raw_val).replace('%', '').replace('+', ''))
                    if pct >= 9.0:
                        return QColor(_c("COLOR_RISE_STRONG"))
                    elif pct > 0:
                        return QColor(_c("COLOR_RISE"))
                    elif pct <= -9.0:
                        return QColor(_c("COLOR_FALL_STRONG"))
                    elif pct < 0:
                        return QColor(_c("COLOR_FALL"))
                    return QColor(_c("COLOR_FLAT"))
                except (ValueError, TypeError):
                    return QColor(_c("COLOR_FLAT"))
            elif key == "突破状态":
                st = str(raw_val)
                if "放量突破" in st:
                    return QColor(_c("COLOR_RISE_STRONG"))
                elif "缩量突破" in st:
                    return QColor(_c("COLOR_WARNING"))
                elif "临近" in st:
                    return QColor(_c("STATUS_APPROACHING"))
                elif "VCP蓄力" in st:
                    return QColor(_c("STATUS_VCP"))
                elif "非红盘" in st or "异常" in st or "一字" in st or "观望" in st:
                    return QColor(_c("STATUS_INACTIVE"))
            elif key in ["上榜净买额(万)", "机构净买(万)"]:
                try:
                    f_val = float(raw_val)
                    if f_val > 0:
                        return QColor(_c("COLOR_RISE"))
                    elif f_val < 0:
                        return QColor(_c("COLOR_FALL"))
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
                    if fz_val > 0:
                        return QColor(_c("COLOR_RISE"))
                    elif fz_val < 0:
                        return QColor(_c("COLOR_FALL"))
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
            if key == SERIAL_HEADER:
                return row + 1

            s_val = str(raw_val).replace(',', '')
            if key in ["市值", "评分"] or "万" in s_val or "亿" in s_val:
                if '万' in s_val:
                    m = re.search(r'([-+]?\d*\.?\d+)', s_val)
                    if m:
                        return float(m.group(1)) * 10000
                    return 0.0
                if '亿' in s_val:
                    m = re.search(r'([-+]?\d*\.?\d+)', s_val)
                    if m:
                        return float(m.group(1)) * 100000000
                    return 0.0
                m = re.search(r'([-+]?\d*\.?\d+)', s_val)
                if m:
                    return float(m.group(1))
                return 0.0
            return str(raw_val)

        return None

    def headerData(self, section, orientation, role):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self._headers[section]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        return None
