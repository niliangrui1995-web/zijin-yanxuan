import re
import time

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QColor

from ui.models.table_model_helpers import (
    FLASH_DURATION_SECONDS,
    SERIAL_HEADER,
    _accent_rail_color_for_row_style,
    _alignment_for_cell,
    _apply_quote_metrics_to_row,
    _build_cell_tooltip,
    _build_flash_record,
    _build_table_model_fonts,
    _c,
    _emit_model_row_ranges,
    _format_market_cap_display,
    _is_date_like_header,
    _is_numeric_header,
    _is_status_header,
    _is_strong_market_move,
    _numeric_heat_color,
    _parse_numeric_value,
    _prune_flash_records,
    _status_badge_color,
    _summarize_long_text,
    _sync_serial_values,
    _with_serial_header,
)


class RtTableModel(QAbstractTableModel):
    def __init__(self, data=None):
        super().__init__()
        self._data = data or []
        self._headers = _with_serial_header(
            ["代码", "名称", "现价", "涨幅%", "市值", "时间", "评分", "RPS强度", "突破状态", "区间振幅", "热点板块"]
        )
        self._flash_records = {}
        fonts = _build_table_model_fonts()
        self.base_font = fonts["base"]
        self.mono_font = fonts["mono"]
        self.bold_font = fonts["bold"]
        self.bold_mono_font = fonts["bold_mono"]

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

    @staticmethod
    def _row_identity(row) -> str:
        if not isinstance(row, dict):
            return ""
        return str(row.get("代码", "") or "").strip()

    @classmethod
    def _row_id_sequence(cls, rows: list) -> list[str]:
        return [cls._row_identity(row) for row in rows]

    def _flash_roles(self) -> list[Qt.ItemDataRole]:
        return [
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
            Qt.ItemDataRole.BackgroundRole,
            Qt.ItemDataRole.ForegroundRole,
            Qt.ItemDataRole.FontRole,
            Qt.ItemDataRole.TextAlignmentRole,
            Qt.ItemDataRole.UserRole,
            Qt.ItemDataRole.UserRole + 1,
            Qt.ItemDataRole.UserRole + 2,
            Qt.ItemDataRole.UserRole + 4,
        ]

    def _record_cell_flash(self, row: int, col: int, old_value, new_value) -> None:
        if row < 0 or col < 0 or col >= len(self._headers):
            return
        flash_record = _build_flash_record(self._headers[col], old_value, new_value)
        if not flash_record:
            return
        self._flash_records.setdefault(row, {})[col] = flash_record

    def _record_row_flashes(self, row: int, old_row: dict, new_row: dict) -> None:
        if not isinstance(old_row, dict) or not isinstance(new_row, dict):
            return
        for col, header in enumerate(self._headers):
            if old_row.get(header) != new_row.get(header):
                self._record_cell_flash(row, col, old_row.get(header), new_row.get(header))

    def _can_update_incrementally(self, rows: list) -> bool:
        if len(rows) != len(self._data) or not rows:
            return False
        old_ids = self._row_id_sequence(self._data)
        new_ids = self._row_id_sequence(rows)
        return bool(all(old_ids)) and old_ids == new_ids

    def _can_reorder_incrementally(self, rows: list) -> bool:
        if len(rows) != len(self._data) or not rows:
            return False
        old_ids = self._row_id_sequence(self._data)
        new_ids = self._row_id_sequence(rows)
        if old_ids == new_ids:
            return False
        if not all(old_ids) or not all(new_ids):
            return False
        if len(set(old_ids)) != len(old_ids) or len(set(new_ids)) != len(new_ids):
            return False
        return set(old_ids) == set(new_ids)

    def _reset_data(self, rows: list) -> None:
        self.beginResetModel()
        self._data = rows
        _sync_serial_values(self._data)
        self._flash_records.clear()
        self.endResetModel()

    def update_data(self, new_data):
        _prune_flash_records(self._flash_records)
        normalized_rows = [dict(item) for item in (new_data or [])]
        _sync_serial_values(normalized_rows)

        if self._can_update_incrementally(normalized_rows):
            self._emit_incremental_rows(normalized_rows)
            return
        if self._can_reorder_incrementally(normalized_rows):
            self._emit_reordered_rows(normalized_rows)
            return

        self._reset_data(normalized_rows)

    def _emit_row_update_ranges(self, changed_rows):
        if not changed_rows:
            return

        roles = self._flash_roles()
        start_row = prev_row = changed_rows[0]
        last_column = self.columnCount() - 1

        for row in changed_rows[1:]:
            if row == prev_row + 1:
                prev_row = row
                continue
            self.dataChanged.emit(self.index(start_row, 0), self.index(prev_row, last_column), roles)
            start_row = prev_row = row

        self.dataChanged.emit(self.index(start_row, 0), self.index(prev_row, last_column), roles)

    def _emit_incremental_rows(self, rows: list) -> None:
        changed_rows = []
        for row_idx, new_row in enumerate(rows):
            if self._data[row_idx] != new_row:
                self._record_row_flashes(row_idx, self._data[row_idx], new_row)
                self._data[row_idx] = new_row
                changed_rows.append(row_idx)

        self._emit_row_update_ranges(changed_rows)

    def _emit_reordered_rows(self, rows: list) -> None:
        self.layoutAboutToBeChanged.emit()
        self._data = rows
        self._flash_records.clear()
        self.layoutChanged.emit()
        if self.rowCount() and self.columnCount():
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self.rowCount() - 1, self.columnCount() - 1),
                self._flash_roles(),
            )

    def update_rows_incremental(self, new_data):
        _prune_flash_records(self._flash_records)
        normalized_rows = [dict(item) for item in (new_data or [])]
        _sync_serial_values(normalized_rows)

        if self._can_update_incrementally(normalized_rows):
            self._emit_incremental_rows(normalized_rows)
            return True
        if self._can_reorder_incrementally(normalized_rows):
            self._emit_reordered_rows(normalized_rows)
            return True

        self._reset_data(normalized_rows)
        return False

    def get_row_data(self, row):
        if 0 <= row < len(self._data):
            return self._data[row]
        return {}

    def _record_quote_flashes(self, row: int, before: dict, after: dict) -> None:
        for header in ("现价", "涨幅%", "市值", "时间", "突破状态"):
            if header not in self._headers:
                continue
            if before.get(header) == after.get(header):
                continue
            self._record_cell_flash(row, self._headers.index(header), before.get(header), after.get(header))

    def update_quotes(self, quotes: dict):
        if not quotes or not self._data:
            return
        _prune_flash_records(self._flash_records)
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

            before = dict(item_dict)
            row_changed, _ = _apply_quote_metrics_to_row(item_dict, quotes[code])
            if row_changed:
                self._record_quote_flashes(row, before, item_dict)
                changed_rows.append(row)

        _emit_model_row_ranges(
            self,
            changed_rows,
            start_col,
            end_col,
            self._flash_roles(),
        )

    def _display_value(self, row: int, key: str, raw_val):
        if key == SERIAL_HEADER:
            return str(row + 1)
        market_cap_text = _format_market_cap_display(key, raw_val)
        if market_cap_text is not None:
            return market_cap_text
        if "%" in key:
            s_val = str(raw_val)
            if s_val == "--" or s_val == "":
                return s_val
            if s_val.endswith("%"):
                return s_val
            try:
                f_val = float(s_val.replace("%", ""))
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

    def _tooltip_value(self, key: str, raw_val, item_dict: dict):
        if key == SERIAL_HEADER:
            return None
        if key == "外资净买入":
            custom_tip = item_dict.get("_外资净买入_tooltip")
            if custom_tip:
                return custom_tip
        return _build_cell_tooltip(raw_val)

    def _font_value(self, key: str, raw_val, item_dict: dict):
        if key == SERIAL_HEADER:
            return self.mono_font
        if _is_strong_market_move(key, raw_val, item_dict):
            return self.bold_mono_font
        if _is_numeric_header(key) or _is_date_like_header(key):
            return self.mono_font
        if key in ["涨幅%", "市值", "时间", "评分", "突破状态", "区间振幅"]:
            return self.mono_font
        if key == "突破状态":
            st = str(raw_val)
            if "放量突破" in st or "缩量突破" in st:
                return self.bold_font
        return self.base_font

    def _percentage_foreground(self, key: str, raw_val):
        if "%" not in key or "换手" in key:
            return None
        try:
            pct = float(str(raw_val).replace("%", "").replace("+", ""))
            if pct >= 9.0:
                return QColor(_c("COLOR_RISE_STRONG"))
            if pct > 0:
                return QColor(_c("COLOR_RISE"))
            if pct <= -9.0:
                return QColor(_c("COLOR_FALL_STRONG"))
            if pct < 0:
                return QColor(_c("COLOR_FALL"))
            return QColor(_c("COLOR_FLAT"))
        except (ValueError, TypeError):
            return QColor(_c("COLOR_FLAT"))

    def _status_foreground(self, key: str, raw_val):
        if key != "突破状态":
            return None
        st = str(raw_val)
        if "放量突破" in st:
            return QColor(_c("COLOR_RISE_STRONG"))
        if "缩量突破" in st:
            return QColor(_c("COLOR_WARNING"))
        if "临近" in st:
            return QColor(_c("STATUS_APPROACHING"))
        if "VCP蓄力" in st:
            return QColor(_c("STATUS_VCP"))
        if "非红盘" in st or "异常" in st or "一字" in st or "观望" in st:
            return QColor(_c("STATUS_INACTIVE"))
        return None

    def _signed_amount_foreground(self, key: str, raw_val):
        if key not in ["上榜净买额(万)", "机构净买(万)"]:
            return None
        try:
            f_val = float(raw_val)
            if f_val > 0:
                return QColor(_c("COLOR_RISE"))
            if f_val < 0:
                return QColor(_c("COLOR_FALL"))
        except (ValueError, TypeError):
            pass
        return None

    def _foreign_net_buy_foreground(self, key: str, item_dict: dict):
        if key != "外资净买入":
            return None
        try:
            f_val = float(item_dict.get("外资净买(万)", 0) or 0)
            if f_val > 0:
                return QColor(_c("COLOR_RISE"))
            if f_val < 0:
                return QColor(_c("COLOR_FALL"))
            return QColor(_c("TEXT_SECONDARY"))
        except (ValueError, TypeError):
            return None

    def _foreign_pool_foreground(self, key: str, item_dict: dict):
        if key != "外资潜伏池":
            return None
        try:
            fz_val = float(item_dict.get("外资净买(万)", 0))
            if fz_val > 0:
                return QColor(_c("COLOR_RISE"))
            if fz_val < 0:
                return QColor(_c("COLOR_FALL"))
        except (ValueError, TypeError):
            pass
        return None

    def _foreground_value(self, key: str, raw_val, item_dict: dict):
        if key == SERIAL_HEADER:
            return QColor(_c("TEXT_SECONDARY"))
        for resolver in (
            lambda: self._percentage_foreground(key, raw_val),
            lambda: self._status_foreground(key, raw_val),
            lambda: self._signed_amount_foreground(key, raw_val),
            lambda: self._foreign_net_buy_foreground(key, item_dict),
            lambda: self._foreign_pool_foreground(key, item_dict),
        ):
            color = resolver()
            if color is not None:
                return color
        return QColor(_c("TEXT_PRIMARY"))

    def _flash_value(self, row: int, col: int):
        flash_record = self._flash_records.get(row, {}).get(col, None)
        if not flash_record:
            return None
        if time.time() - float(flash_record.get("time", 0) or 0) > FLASH_DURATION_SECONDS:
            return None
        return flash_record

    def _row_accent_value(self, item_dict: dict):
        rail_color = _accent_rail_color_for_row_style(item_dict.get("_row_style", ""))
        if rail_color:
            return rail_color
        for header in self._headers:
            if not _is_status_header(header):
                continue
            badge = _status_badge_color(item_dict.get(header, ""), header)
            if badge:
                return badge
        return None

    def _sort_value(self, row: int, key: str, raw_val):
        if key == SERIAL_HEADER:
            return row + 1

        s_val = str(raw_val).replace(",", "")
        parsed_value = _parse_numeric_value(raw_val)
        if parsed_value is not None and (_is_numeric_header(key) or "万" in s_val or "亿" in s_val):
            return parsed_value
        if key in ["市值", "评分"] or "万" in s_val or "亿" in s_val:
            if "万" in s_val:
                m = re.search(r"([-+]?\d*\.?\d+)", s_val)
                if m:
                    return float(m.group(1)) * 10000
                return 0.0
            if "亿" in s_val:
                m = re.search(r"([-+]?\d*\.?\d+)", s_val)
                if m:
                    return float(m.group(1)) * 100000000
                return 0.0
            m = re.search(r"([-+]?\d*\.?\d+)", s_val)
            if m:
                return float(m.group(1))
            return 0.0
        return str(raw_val)

    def data(self, index, role):
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()
        item_dict = self._data[row]
        key = self._headers[col]
        raw_val = item_dict.get(key, "")

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_value(row, key, raw_val)

        elif role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip_value(key, raw_val, item_dict)

        elif role == Qt.ItemDataRole.TextAlignmentRole:
            return _alignment_for_cell(key, raw_val)

        elif role == Qt.ItemDataRole.FontRole:
            return self._font_value(key, raw_val, item_dict)

        elif role == Qt.ItemDataRole.ForegroundRole:
            return self._foreground_value(key, raw_val, item_dict)

        elif role == Qt.ItemDataRole.BackgroundRole:
            heat_color = _numeric_heat_color(key, raw_val)
            if heat_color is not None:
                return heat_color

        elif role == Qt.ItemDataRole.UserRole + 1:
            return self._flash_value(row, col)

        elif role == Qt.ItemDataRole.UserRole + 2:
            if _is_status_header(key):
                badge = _status_badge_color(raw_val, key)
                if badge:
                    return badge

        elif role == Qt.ItemDataRole.UserRole + 4:
            return self._row_accent_value(item_dict)

        elif role == Qt.ItemDataRole.UserRole:
            return self._sort_value(row, key, raw_val)

        return None

    def headerData(self, section, orientation, role):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self._headers[section]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        return None
