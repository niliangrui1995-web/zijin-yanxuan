import logging
import re
import time

from PyQt6.QtCore import QAbstractTableModel, QMimeData, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from core.buy_point import BUY_POINT_STYLE_TEXT, calculate_buy_point_from_history
from app.services.ui_runtime_service import resolve_quote_metrics
from ui.models.table_model_helpers import (
    SERIAL_HEADER,
    _alignment_for_cell,
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

_log = logging.getLogger(__name__)


class StockTableModel(QAbstractTableModel):
    sig_rows_reordered = pyqtSignal(list)

    def __init__(self, headers, data=None):
        super().__init__()
        self._headers = _with_serial_header(headers)
        self._data = data or []
        self._flash_records = {}
        self._plain_style_headers = set()
        self._plain_background_headers = set()

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

    def set_plain_style_headers(self, headers):
        self._plain_style_headers = {str(header) for header in (headers or []) if str(header).strip()}

    def _uses_plain_style(self, header: str) -> bool:
        return header in self._plain_style_headers

    def set_plain_background_headers(self, headers):
        self._plain_background_headers = {str(header) for header in (headers or []) if str(header).strip()}

    def _uses_plain_background(self, header: str) -> bool:
        return header in self._plain_background_headers

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
        if not self._data or "代码" not in self._headers:
            return
        if not any(header in self._headers for header in ("现价", "市价", "涨幅%", "市值", "买点")):
            return
        try:
            from core.global_store import global_store

            snapshot = global_store.get_latest_quotes()
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
            return

        if not snapshot:
            return

        self.update_quotes(snapshot)
        self._flash_records.clear()

    def supportedDropActions(self):
        return Qt.DropAction.MoveAction

    def flags(self, index):
        default_flags = super().flags(index)
        if index.isValid():
            return default_flags | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        return default_flags | Qt.ItemFlag.ItemIsDropEnabled

    def mimeTypes(self):
        return ["application/x-watchlist-row"]

    def mimeData(self, indices):
        import json

        mime_data = QMimeData()
        rows = list(set([i.row() for i in indices]))
        if not rows:
            return mime_data
        data_str = json.dumps(rows).encode('utf-8')
        mime_data.setData("application/x-watchlist-row", data_str)
        return mime_data

    def dropMimeData(self, data, action, row, column, parent):
        if not data.hasFormat("application/x-watchlist-row"):
            return False
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
        codes = [d.get("代码") for d in new_data if d.get("代码")]
        self.sig_rows_reordered.emit(codes)
        return False

    def set_cell_value(self, row, col_name, new_val, emit_signal: bool = True):
        if 0 <= row < len(self._data):
            old_val = self._data[row].get(col_name)
            self._data[row][col_name] = new_val

            if col_name in ["现价", "市价", "最新价", "最新", "涨幅%"]:
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
        if not quotes or not self._data:
            return

        changed_rows = []
        quote_cols = []
        for header in ("现价", "市价", "涨幅%", "市值"):
            if header in self._headers:
                quote_cols.append(self._headers.index(header))
        start_col = min(quote_cols) if quote_cols else None
        end_col = max(quote_cols) if quote_cols else None

        for row, item_dict in enumerate(self._data):
            code = item_dict.get("代码")
            if not code or code not in quotes:
                continue

            q = quotes[code]
            metrics = resolve_quote_metrics(item_dict, q)
            rt_close = float(metrics.get("rt_close", 0) or 0)
            pct = metrics.get("pct")
            row_changed = False
            zongguben = float(metrics.get("zongguben", 0) or 0)
            if zongguben > 0 and float(item_dict.get("_zongguben", 0) or 0) != zongguben:
                item_dict["_zongguben"] = zongguben
                row_changed = True

            price_text = metrics.get("price_text")
            if price_text is not None:
                for price_key in ("现价", "市价"):
                    if price_key in self._headers and item_dict.get(price_key) != price_text:
                        self.set_cell_value(row, price_key, price_text, emit_signal=False)
                        row_changed = True
            if pct is not None and item_dict.get("涨幅%") != pct:
                self.set_cell_value(row, "涨幅%", pct, emit_signal=False)
                row_changed = True

            if "市值" in self._headers:
                cap_text = metrics.get("market_cap_text")
                if cap_text and item_dict.get("市值") != cap_text:
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
                        temp_hist = history[:-1] + [rt_close]
                    else:
                        from app.services.ui_runtime_service import MarketCalendar

                        if now_time >= "09:15" and MarketCalendar.is_trade_day(today_str):
                            temp_hist = history[1:] + [rt_close]
                        else:
                            temp_hist = history[:-1] + [rt_close]

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

            if key == "PE":
                if raw_val in ("", "--", None):
                    return "--"
                try:
                    return f"{float(raw_val):.2f}"
                except (ValueError, TypeError):
                    return str(raw_val)

            if "日" in key or "期" in key or "时间" in key:
                s_val = str(raw_val).split(" ")[0].replace("-", "").replace("/", "")
                if len(s_val) == 8 and s_val.isdigit() and s_val.startswith("20"):
                    return s_val

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
                from app.services.ui_runtime_service import watchlist_vm

                code = str(item_dict.get("代码", ""))
                if watchlist_vm.is_in_watchlist(code):
                    return QColor("#E879F9")
            if self._uses_plain_style(key) or _is_date_like_header(key):
                return QColor(_c("TEXT_PRIMARY"))

            if ("%" in key and "换手" not in key) or key in ["涨跌", "净额", "现价", "收盘", "最新价"]:
                try:
                    target_pct = raw_val
                    if key in ["现价", "收盘", "最新价"]:
                        target_pct = item_dict.get("涨幅%") or item_dict.get("涨跌") or "0"

                    pct = float(str(target_pct).replace('%', '').replace('+', '').replace(',', ''))
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
            elif key == "卖方营业部":
                val_str = str(raw_val)
                if any(kw in val_str for kw in ["高盛", "摩根大通", "摩根士丹利", "瑞银", "法巴", "渣打", "野村", "汇丰", "星展", "大和"]):
                    return QColor(_c("COLOR_FALL"))
            elif key == "买方营业部":
                val_str = str(raw_val)
                if any(kw in val_str for kw in ["高盛", "摩根大通", "摩根士丹利", "瑞银", "法巴", "渣打", "野村", "汇丰", "星展", "大和"]):
                    return QColor(_c("COLOR_RISE"))
            elif key == "交易详情":
                val_str = str(raw_val)
                if "对倒" in val_str:
                    return QColor("#F59E0B")
                elif "买/" in val_str or "/卖" in val_str:
                    return QColor("#3B82F6")
                elif "卖出" in val_str:
                    return QColor(_c("COLOR_FALL"))
                elif "买入" in val_str:
                    return QColor(_c("COLOR_RISE"))
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
            elif key == "突破状态" and str(raw_val) != "":
                st = str(raw_val)
                if "放量" in st:
                    return QColor(_c("COLOR_RISE_STRONG"))
                elif "缩量" in st:
                    return QColor(_c("COLOR_WARNING"))
                elif "临近" in st:
                    return QColor(_c("STATUS_APPROACHING"))
                elif "VCP" in st:
                    return QColor(_c("STATUS_VCP"))
                return QColor(_c("STATUS_INACTIVE"))
            elif key == "股价弹性" and str(raw_val) != "":
                st = str(raw_val)
                if "高" in st:
                    return QColor(_c("COLOR_RISE"))
                return QColor(_c("TEXT_PRIMARY"))
            elif key == "评分" and str(raw_val) != "":
                try:
                    score = float(str(raw_val))
                    if score >= 90:
                        return QColor(_c("SCORE_EXCELLENT"))
                    elif score >= 80:
                        return QColor(_c("SCORE_GOOD"))
                    elif score >= 60:
                        return QColor(_c("SCORE_NORMAL"))
                    return QColor(_c("SCORE_LOW"))
                except (ValueError, TypeError):
                    pass

            return QColor(_c("TEXT_PRIMARY"))

        elif role == Qt.ItemDataRole.BackgroundRole:
            if self._uses_plain_style(key) or self._uses_plain_background(key):
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
                    return report_ts * 1000000 + max(0, 999999 - row_rank)

            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s_val):
                return int(s_val.replace('-', ''))
            if re.fullmatch(r'\d{8}', s_val):
                return int(s_val)

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
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self._headers[section]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        return None

