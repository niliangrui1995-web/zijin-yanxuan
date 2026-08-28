import logging
import re
import time
from collections.abc import Mapping
from typing import Any

from PyQt6.QtCore import QAbstractTableModel, QMimeData, QModelIndex, Qt, pyqtSignal
from PyQt6.QtGui import QColor

from app.services.ui_quote_service import resolve_quote_metrics
from core.buy_point import BUY_POINT_STYLE_TEXT, BUY_POINT_TEXT, calculate_buy_point_from_history
from core.observability import record_metric
from ui.models.table_model_helpers import (
    SERIAL_HEADER,
    STOCK_CELL_PRESENTATION_ROLE,
    STOCK_CELL_RENDER_ROLE,
    _active_flash_record,
    _alignment_for_cell,
    _apply_quote_metadata_to_row,
    _build_flash_record,
    _build_table_model_fonts,
    _c,
    _emit_model_row_ranges,
    _format_market_cap_display,
    _is_date_like_header,
    _is_numeric_header,
    _is_pct_like_header,
    _is_status_header,
    _is_strong_market_move,
    _numeric_heat_color,
    _parse_numeric_value,
    _prune_flash_records,
    _row_accent_color,
    _signed_amount_foreground,
    _status_badge_color,
    _summarize_long_text,
    _sync_serial_values,
    _tooltip_for_cell,
    _with_serial_header,
    apply_quote_total_shares_to_row,
)

_log = logging.getLogger(__name__)

LEGACY_MOJIBAKE_CODE_KEY = "\u6d60\uff47\u721c"
ROW_IDENTITY_KEYS = ("代码", LEGACY_MOJIBAKE_CODE_KEY, "code", "symbol", "股票代码", "证券代码")
BUY_POINT_TRIGGER_ICON = "🚀"
FOREIGN_BROKER_KEYWORDS = ("高盛", "摩根大通", "摩根士丹利", "瑞银", "法巴", "渣打", "野村", "汇丰", "星展", "大和")
_UNHANDLED_ROLE = object()
_PRICE_MOVE_HEADERS = ("现价", "市价", "收盘", "最新价")
_PRESENTATION_CACHE_MISS = object()
_PRESENTATION_CACHE_MAX_ENTRIES = 4096
_PRESENTATION_CACHE_ROLES = frozenset(
    {
        int(Qt.ItemDataRole.DisplayRole),
        int(Qt.ItemDataRole.ToolTipRole),
        int(Qt.ItemDataRole.TextAlignmentRole),
        int(Qt.ItemDataRole.FontRole),
        int(Qt.ItemDataRole.ForegroundRole),
        int(Qt.ItemDataRole.BackgroundRole),
        STOCK_CELL_RENDER_ROLE,
        STOCK_CELL_PRESENTATION_ROLE,
    }
)
_ROW_CHANGE_DEPENDENCIES = {
    **{key: _PRICE_MOVE_HEADERS for key in ("涨幅%", "涨幅", "涨跌%", "涨跌")},
    "_row_style": (SERIAL_HEADER,),
    "_suppress_accent_rail": (SERIAL_HEADER,),
    "货币": ("现价", "市价"),
    "来源标签": ("来源",),
    "_外资净买入_tooltip": ("外资净买入",),
    "外资净买(万)": ("外资净买入", "外资潜伏池"),
    "_最近上榜_raw": ("最近上榜",),
    "_report_ts": ("日报时间",),
    "_report_row_rank": ("日报时间",),
}
_MONEY_BAR_HEADERS = frozenset({"上榜净买额(万)", "机构净买(万)", "外资净买(万)", "外资净买入"})


def _build_code_row_index(rows) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for row, item in enumerate(rows):
        if not isinstance(item, dict):
            continue
        code = str(item.get("代码") or "").strip()
        if code:
            index.setdefault(code, []).append(row)
    return index


def _changed_row_codes(rows, changed_rows: list[int]) -> set[str]:
    return {
        str(rows[row].get("代码") or "").strip()
        for row in changed_rows
        if isinstance(rows[row], dict)
    }


def _quotes_for_codes(snapshot, code_row_index: Mapping[str, list[int]], codes: set[str] | None) -> dict:
    target_codes = code_row_index if codes is None else {code for code in codes if code}
    return {code: snapshot[code] for code in target_codes if code in snapshot}


def _iter_indexed_quote_rows(quotes: Mapping[str, Mapping[str, Any]], code_row_index: Mapping[str, list[int]]):
    for code, quote in quotes.items():
        for row in code_row_index.get(str(code).strip(), ()):
            yield row, quote


def _quote_column_bounds(headers) -> tuple[int | None, int | None]:
    quote_columns = [headers.index(header) for header in ("现价", "市价", "涨幅%", "涨幅", "市值") if header in headers]
    return min(quote_columns) if quote_columns else None, max(quote_columns) if quote_columns else None


def _clear_money_bar_cache_for_changed_value(model, old_value, new_value) -> None:
    if old_value != new_value:
        model._clear_money_bar_max_abs_cache()


def _indicator_tone(text: str) -> str:
    value = str(text or "").strip()
    tones = (
        (("🔴", "红", "高", "风险", "异常"), "error"),
        (("🟡", "黄", "中", "午", "竞价", "警"), "warning"),
        (("🟢", "绿", "交易中", "开盘", "安全", "低"), "success"),
        (("收盘", "休市", "离线"), "offline"),
    )
    return next((tone for tokens, tone in tones if any(token in value for token in tokens)), "neutral")


def _indicator_payload(header: str, raw_value) -> dict | None:
    if header == "风控":
        return {"kind": "risk_light", "tone": _indicator_tone(str(raw_value)), "label": ""}
    if header == "状态":
        return {
            "kind": "status_light",
            "tone": _indicator_tone(str(raw_value)),
            "pulse": "交易中" in str(raw_value) or "🟢" in str(raw_value),
        }
    return None


def _currency_stamp_text(currency: str) -> str:
    text = str(currency or "").strip()
    return {
        "TWD": "NT$",
        "NT$": "NT$",
        "JPY": "¥",
        "円": "円",
        "KRW": "₩",
        "HKD": "HK$",
        "USD": "$",
    }.get(text.upper(), text)


def _currency_stamp_payload(header: str, row: dict, headers) -> dict | None:
    if header not in {"现价", "市价"} or "货币" not in headers:
        return None
    stamp = _currency_stamp_text(str(row.get("货币", "") or ""))
    return {"kind": "currency_stamp", "stamp": stamp} if stamp and stamp not in {"--", "---"} else None


def _model_currency_stamp_payload(model, header: str, row: dict) -> dict | None:
    return _currency_stamp_payload(header, row, model._headers)


def _changed_row_keys(old_row: dict, new_row: dict) -> set[str]:
    return {key for key in old_row.keys() | new_row.keys() if old_row.get(key) != new_row.get(key)}


def _affected_columns_for_row_change(headers, changed_keys: set[str]) -> list[int]:
    header_columns = {header: col for col, header in enumerate(headers)}
    affected_headers = set(changed_keys) & header_columns.keys()
    for key in changed_keys:
        affected_headers.update(_ROW_CHANGE_DEPENDENCIES.get(key, ()))
    if any(_is_status_header(key) for key in changed_keys):
        affected_headers.add(headers[0])
    return sorted(header_columns[header] for header in affected_headers if header in header_columns)


def _contiguous_column_spans(columns: list[int]) -> list[tuple[int, int]]:
    if not columns:
        return []
    spans = []
    start = end = columns[0]
    for col in columns[1:]:
        if col == end + 1:
            end = col
        else:
            spans.append((start, end))
            start = end = col
    spans.append((start, end))
    return spans


def _custom_cell_role_value(model, role, row, col, key, raw_val, item_dict):
    if role == Qt.ItemDataRole.UserRole + 1:
        return _active_flash_record(model._flash_records, row, col)
    if role == Qt.ItemDataRole.UserRole + 2:
        return model._status_badge_value(key, raw_val)
    if role == Qt.ItemDataRole.UserRole + 3:
        return model._uses_plain_style(key)
    if role == Qt.ItemDataRole.UserRole + 4:
        return model._accent_rail_value(item_dict)
    if role == Qt.ItemDataRole.UserRole + 5:
        return model._visual_payload(key, raw_val, item_dict)
    if role == STOCK_CELL_RENDER_ROLE:
        return model._cell_render_payload(row, col, key, raw_val, item_dict)
    return _UNHANDLED_ROLE


class StockTableModel(QAbstractTableModel):
    sig_rows_reordered = pyqtSignal(list)
    _indicator_tone = staticmethod(_indicator_tone)
    _currency_stamp_text = staticmethod(_currency_stamp_text)
    _currency_stamp_payload = _model_currency_stamp_payload

    def __init__(self, headers, data=None):
        super().__init__()
        self._headers = _with_serial_header(headers)
        self._data = data or []
        self._rebuild_code_row_index()
        self._flash_records = {}
        self._sort_value_cache = {}
        self._presentation_cache_enabled = False
        self._presentation_cache: dict[int, dict[tuple[int, int], object]] = {}
        self._presentation_cache_entry_count = 0
        self._money_bar_max_abs_cache: dict[str, float] = {}
        self._plain_style_headers = set()
        self._plain_background_headers = set()
        self._muted_text_headers = set()
        self._sparse_update_coalescing = False
        self._sparse_quote_update_coalescing: bool | None = None
        self.dataChanged.connect(self._invalidate_sort_cache_for_changed_indexes)
        self.dataChanged.connect(self._invalidate_presentation_cache_for_changed_indexes)

        fonts = _build_table_model_fonts()
        self.base_font = fonts["base"]
        self.small_font = fonts["small"]
        self.mono_font = fonts["mono"]
        self.small_mono_font = fonts["small_mono"]
        self.bold_font = fonts["bold"]
        self.bold_mono_font = fonts["bold_mono"]

    @property
    def row_data(self):
        return self._data

    @property
    def headers(self):
        return self._headers

    def set_plain_style_headers(self, headers):
        self._plain_style_headers = {str(header) for header in (headers or []) if str(header).strip()}
        self.clear_presentation_cache()

    def _uses_plain_style(self, header: str) -> bool:
        return header in self._plain_style_headers

    def set_plain_background_headers(self, headers):
        self._plain_background_headers = {str(header) for header in (headers or []) if str(header).strip()}
        self.clear_presentation_cache()

    def _uses_plain_background(self, header: str) -> bool:
        return header in self._plain_background_headers

    def set_muted_text_headers(self, headers):
        self._muted_text_headers = {str(header) for header in (headers or []) if str(header).strip()}
        self.clear_presentation_cache()

    def set_presentation_cache_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._presentation_cache_enabled == enabled:
            return
        self._presentation_cache_enabled = enabled
        self.clear_presentation_cache()

    def clear_presentation_cache(self) -> None:
        self._presentation_cache.clear()
        self._presentation_cache_entry_count = 0

    def _presentation_cache_value(self, row: int, col: int, role: int):
        if not self._presentation_cache_enabled or role not in _PRESENTATION_CACHE_ROLES:
            return _PRESENTATION_CACHE_MISS
        return self._presentation_cache.get(row, {}).get((col, role), _PRESENTATION_CACHE_MISS)

    def _cache_presentation_value(self, row: int, col: int, role: int, value):
        if not self._presentation_cache_enabled or role not in _PRESENTATION_CACHE_ROLES:
            return value
        row_cache = self._presentation_cache.get(row)
        if row_cache is None:
            row_cache = {}
            self._presentation_cache[row] = row_cache
        cache_key = (col, role)
        if cache_key not in row_cache:
            if self._presentation_cache_entry_count >= _PRESENTATION_CACHE_MAX_ENTRIES:
                self.clear_presentation_cache()
                row_cache = {}
                self._presentation_cache[row] = row_cache
            self._presentation_cache_entry_count += 1
        row_cache[cache_key] = value
        return value

    def set_sparse_update_coalescing(self, enabled: bool) -> None:
        self._sparse_update_coalescing = bool(enabled)

    def set_sparse_quote_update_coalescing(self, enabled: bool | None) -> None:
        self._sparse_quote_update_coalescing = None if enabled is None else bool(enabled)

    def _uses_muted_text(self, header: str) -> bool:
        return header in self._muted_text_headers

    @staticmethod
    def _split_source_tags(raw_value, row: dict) -> list[str]:
        source_tags = row.get("来源标签") if isinstance(row, dict) else None
        if isinstance(source_tags, (list, tuple, set)):
            tags = [str(tag or "").strip() for tag in source_tags if str(tag or "").strip()]
        else:
            text = str(source_tags or raw_value or "").strip()
            tags = [part.strip() for part in re.split(r"[|｜、,，/]+", text) if part.strip()]
        if tags:
            return tags
        fallback = str(raw_value or "").strip()
        return [fallback] if fallback else []

    @staticmethod
    def _source_badge_color(tag: str) -> str:
        text = str(tag or "")
        if "龙虎榜" in text:
            return _c("COLOR_RISE")
        if "扫描" in text or "AI" in text or "算法" in text:
            return _c("ACCENT_PRIMARY")
        if "手动" in text or "自选" in text:
            return _c("COLOR_INFO")
        return _c("COLOR_INFO")

    def _source_badges_payload(self, raw_value, row: dict) -> dict | None:
        tags = self._split_source_tags(raw_value, row)
        if not tags:
            return None
        return {
            "kind": "tag_badges",
            "tags": [{"text": tag, "color": self._source_badge_color(tag)} for tag in tags],
        }

    def _money_value_for_visual(self, header: str, row: dict):
        if header == "外资净买入":
            return _parse_numeric_value(row.get("外资净买(万)", row.get(header)))
        return _parse_numeric_value(row.get(header))

    def _clear_money_bar_max_abs_cache(self) -> None:
        self._money_bar_max_abs_cache.clear()

    def _rebuild_code_row_index(self) -> None:
        """Keep the quote lookup index synchronized with the backing rows."""
        self._code_row_index = _build_code_row_index(self._data)

    def _money_bar_max_abs(self, header: str) -> float:
        if header in self._money_bar_max_abs_cache:
            return self._money_bar_max_abs_cache[header]
        max_abs = 0.0
        for item in self._data:
            if not isinstance(item, dict):
                continue
            item_value = self._money_value_for_visual(header, item)
            if item_value is not None:
                max_abs = max(max_abs, abs(float(item_value)))
        self._money_bar_max_abs_cache[header] = max_abs
        return max_abs

    def _money_bar_payload(self, header: str, row: dict) -> dict | None:
        if header not in {"上榜净买额(万)", "机构净买(万)", "外资净买入"}:
            return None
        value = self._money_value_for_visual(header, row)
        if value is None or abs(value) < 0.0001:
            return None
        max_abs = self._money_bar_max_abs(header)
        return {"kind": "money_bar", "value": float(value), "max_abs": max(max_abs, abs(float(value)), 1.0)}

    def _visual_payload(self, header: str, raw_value, row: dict):
        if header == "来源" and not self._uses_plain_style(header):
            return self._source_badges_payload(raw_value, row)
        payload = self._money_bar_payload(header, row)
        if payload:
            return payload
        payload = _indicator_payload(header, raw_value)
        if payload:
            return payload
        payload = _currency_stamp_payload(header, row, self._headers)
        if payload:
            return payload
        return None

    def get_row_data(self, row):
        if 0 <= row < len(self._data):
            return self._data[row]
        return None

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return len(self._headers)

    @staticmethod
    def _row_identity(row) -> str:
        if not isinstance(row, dict):
            return ""
        for key in ROW_IDENTITY_KEYS:
            value = str(row.get(key, "") or "").strip()
            if value:
                return value
        return ""

    @classmethod
    def _row_id_sequence(cls, rows: list) -> list[str]:
        return [cls._row_identity(row) for row in rows]

    def _can_update_incrementally(self, rows: list) -> bool:
        if len(rows) != len(self._data) or not rows:
            return False
        if rows is self._data or any(old is new for old, new in zip(self._data, rows)):
            return False
        old_ids = self._row_id_sequence(self._data)
        new_ids = self._row_id_sequence(rows)
        return bool(all(old_ids)) and old_ids == new_ids

    def _can_reorder_incrementally(self, rows: list) -> bool:
        if len(rows) != len(self._data) or not rows or rows is self._data:
            return False

        old_ids = self._row_id_sequence(self._data)
        new_ids = self._row_id_sequence(rows)
        if old_ids == new_ids:
            return False
        if not old_ids or not all(old_ids) or not all(new_ids):
            return False
        if len(set(old_ids)) != len(old_ids) or len(set(new_ids)) != len(new_ids):
            return False
        return set(old_ids) == set(new_ids)

    def _single_row_membership_delta(self, rows: list) -> tuple[str, int] | None:
        """Return a safe one-row insert/remove delta, otherwise keep the reset path."""
        if not self._data or not rows or abs(len(rows) - len(self._data)) != 1:
            return None

        old_ids = self._row_id_sequence(self._data)
        new_ids = self._row_id_sequence(rows)
        if (
            not all(old_ids)
            or not all(new_ids)
            or len(set(old_ids)) != len(old_ids)
            or len(set(new_ids)) != len(new_ids)
        ):
            return None

        if len(rows) > len(self._data):
            added_ids = set(new_ids).difference(old_ids)
            if len(added_ids) != 1:
                return None
            row = new_ids.index(next(iter(added_ids)))
            retained_rows = rows[:row] + rows[row + 1 :]
            if (
                old_ids == new_ids[:row] + new_ids[row + 1 :]
                and not any(old is new for old, new in zip(self._data, retained_rows, strict=True))
            ):
                return "insert", row
            return None

        removed_ids = set(old_ids).difference(new_ids)
        if len(removed_ids) != 1:
            return None
        row = old_ids.index(next(iter(removed_ids)))
        retained_rows = self._data[:row] + self._data[row + 1 :]
        if (
            new_ids == old_ids[:row] + old_ids[row + 1 :]
            and not any(old is new for old, new in zip(retained_rows, rows, strict=True))
        ):
            return "remove", row
        return None

    def _record_cell_flash(self, row: int, col: int, old_value, new_value) -> bool:
        if row < 0 or col < 0 or col >= len(self._headers):
            return False
        self._sort_value_cache.pop((row, col), None)
        flash_record = _build_flash_record(self._headers[col], old_value, new_value)
        if not flash_record:
            return False
        self._flash_records.setdefault(row, {})[col] = flash_record
        return True

    def _clear_flash_records_for_rows(self, rows) -> None:
        for row in set(rows or ()):
            self._flash_records.pop(row, None)

    def _clear_sort_value_cache(self) -> None:
        self._sort_value_cache.clear()

    def _clear_presentation_cache_for_rows(self, rows) -> None:
        for row in set(rows or ()):
            row_cache = self._presentation_cache.pop(row, None)
            if row_cache is not None:
                self._presentation_cache_entry_count = max(
                    0,
                    self._presentation_cache_entry_count - len(row_cache),
                )

    def _clear_sort_value_cache_for_rows(self, rows) -> None:
        if not self._sort_value_cache:
            return
        row_set = set(rows or [])
        if not row_set:
            return
        for cache_key in list(self._sort_value_cache):
            if cache_key[0] in row_set:
                self._sort_value_cache.pop(cache_key, None)

    def _invalidate_sort_cache_for_changed_indexes(self, top_left, bottom_right, *_args) -> None:
        if not self._sort_value_cache:
            return
        if not top_left.isValid() or not bottom_right.isValid():
            return

        row_start, row_end = sorted((top_left.row(), bottom_right.row()))
        col_start, col_end = sorted((top_left.column(), bottom_right.column()))
        for cache_key in list(self._sort_value_cache):
            row, col = cache_key
            if row_start <= row <= row_end and col_start <= col <= col_end:
                self._sort_value_cache.pop(cache_key, None)

    def _invalidate_presentation_cache_for_changed_indexes(self, top_left, bottom_right, *_args) -> None:
        if not self._presentation_cache_enabled:
            return
        if not top_left.isValid() or not bottom_right.isValid():
            self.clear_presentation_cache()
            return
        row_start, row_end = sorted((top_left.row(), bottom_right.row()))
        self._clear_presentation_cache_for_rows(range(row_start, row_end + 1))

    def _record_row_flashes(self, row: int, old_row: dict, new_row: dict) -> bool:
        if not isinstance(old_row, dict) or not isinstance(new_row, dict):
            return False
        recorded = False
        for col, header in enumerate(self._headers):
            if old_row.get(header) != new_row.get(header):
                recorded = self._record_cell_flash(row, col, old_row.get(header), new_row.get(header)) or recorded
        return recorded

    def _flash_roles(self, *, include_flash: bool = True) -> list[Qt.ItemDataRole]:
        roles = [
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.ToolTipRole,
            Qt.ItemDataRole.ForegroundRole,
            Qt.ItemDataRole.BackgroundRole,
            Qt.ItemDataRole.FontRole,
            Qt.ItemDataRole.TextAlignmentRole,
            Qt.ItemDataRole.UserRole,
            Qt.ItemDataRole.UserRole + 2,
            Qt.ItemDataRole.UserRole + 4,
            Qt.ItemDataRole.UserRole + 5,
            STOCK_CELL_RENDER_ROLE,
            STOCK_CELL_PRESENTATION_ROLE,
        ]
        if include_flash:
            roles.insert(7, Qt.ItemDataRole.UserRole + 1)
        return roles

    def _emit_incremental_rows(self, rows: list) -> list[int]:
        _sync_serial_values(rows)
        changed_rows = []
        changed_rows_by_span = {}
        flash_recorded = False
        codes_changed = False
        for row_idx, new_row in enumerate(rows):
            old_row = self._data[row_idx]
            if old_row != new_row:
                changed_keys = _changed_row_keys(old_row, new_row)
                codes_changed = codes_changed or "代码" in changed_keys
                changed_cols = _affected_columns_for_row_change(self._headers, changed_keys)
                flash_recorded = self._record_row_flashes(row_idx, old_row, new_row) or flash_recorded
                self._data[row_idx] = new_row
                changed_rows.append(row_idx)
                for span in _contiguous_column_spans(changed_cols):
                    changed_rows_by_span.setdefault(span, []).append(row_idx)

        if not changed_rows:
            return []

        if codes_changed:
            self._rebuild_code_row_index()
        self._clear_sort_value_cache_for_rows(changed_rows)
        self._clear_presentation_cache_for_rows(changed_rows)
        for (start_col, end_col), span_rows in sorted(changed_rows_by_span.items()):
            _emit_model_row_ranges(
                self,
                span_rows,
                start_col,
                end_col,
                self._flash_roles(include_flash=flash_recorded),
                coalesce=self._sparse_update_coalescing,
            )
        return changed_rows

    def _emit_reordered_rows(self, rows: list) -> None:
        _sync_serial_values(rows)
        self.layoutAboutToBeChanged.emit()
        self._data = rows
        self._rebuild_code_row_index()
        self._flash_records.clear()
        self._clear_sort_value_cache()
        self.clear_presentation_cache()
        self.layoutChanged.emit()

    def _emit_single_row_membership_delta(self, rows: list, delta: tuple[str, int]) -> None:
        action, row = delta
        _sync_serial_values(rows)
        self._flash_records.clear()
        self._clear_sort_value_cache()
        self.clear_presentation_cache()
        if action == "insert":
            self.beginInsertRows(QModelIndex(), row, row)
            self._data.insert(row, rows[row])
            self.endInsertRows()
        else:
            self.beginRemoveRows(QModelIndex(), row, row)
            self._data.pop(row)
            self.endRemoveRows()
        self._rebuild_code_row_index()
        self._emit_incremental_rows(rows)

    def update_data(
        self,
        new_data,
        *,
        hydrate_latest_quotes: bool = True,
        record_flash: bool = True,
        allow_single_row_membership_delta: bool = False,
        membership_reconcile_source: str = "",
    ):
        _prune_flash_records(self._flash_records)
        self._clear_money_bar_max_abs_cache()
        rows = list(new_data or [])
        if self._can_update_incrementally(rows):
            changed_rows = self._emit_incremental_rows(rows)
            if hydrate_latest_quotes:
                self._hydrate_latest_quotes_from_store(
                    _changed_row_codes(rows, changed_rows),
                    record_flash=record_flash,
                )
            return
        if self._can_reorder_incrementally(rows):
            self._emit_reordered_rows(rows)
            if hydrate_latest_quotes:
                self._hydrate_latest_quotes_from_store(record_flash=record_flash)
            return
        membership_delta = None
        if allow_single_row_membership_delta and len(rows) != len(self._data):
            membership_delta = self._single_row_membership_delta(rows)
            record_metric(
                "watchlist_membership_reconcile",
                1,
                unit="count",
                tags={
                    "mode": f"{membership_delta[0]}_one" if membership_delta is not None else "reset_fallback",
                    "old_rows": str(len(self._data)),
                    "new_rows": str(len(rows)),
                    "source": membership_reconcile_source or "watchlist_rows",
                },
            )
        if membership_delta is not None:
            self._emit_single_row_membership_delta(rows, membership_delta)
            if hydrate_latest_quotes:
                self._hydrate_latest_quotes_from_store(record_flash=record_flash)
            return

        self.beginResetModel()
        self._data = rows
        self._rebuild_code_row_index()
        _sync_serial_values(self._data)
        self._flash_records.clear()
        self._clear_sort_value_cache()
        self.clear_presentation_cache()
        self.endResetModel()
        if hydrate_latest_quotes:
            self._hydrate_latest_quotes_from_store(record_flash=record_flash)

    def append_rows(
        self,
        new_data,
        *,
        hydrate_latest_quotes: bool = True,
        record_flash: bool = False,
    ) -> int:
        """Append rows through the model-owned structural-change contract."""
        rows = list(new_data or [])
        if not rows:
            return 0

        start_row = len(self._data)
        serial_header = self._headers[0]
        quote_codes = set()
        for row_number, row in enumerate(rows, start_row + 1):
            if not isinstance(row, dict):
                continue
            row[serial_header] = row_number
            code = str(row.get("代码") or "").strip()
            if code:
                quote_codes.add(code)

        self._clear_money_bar_max_abs_cache()
        self.beginInsertRows(QModelIndex(), start_row, start_row + len(rows) - 1)
        self._data.extend(rows)
        self._rebuild_code_row_index()
        self.endInsertRows()
        if hydrate_latest_quotes and quote_codes:
            self._hydrate_latest_quotes_from_store(quote_codes, record_flash=record_flash)
        return len(rows)

    def _hydrate_latest_quotes_from_store(
        self,
        codes: set[str] | None = None,
        *,
        record_flash: bool = True,
    ):
        if not self._data or "代码" not in self._headers:
            return
        if not any(header in self._headers for header in ("现价", "市价", "涨幅%", "涨幅", "市值", "买点")):
            return
        try:
            from core.global_store import global_store

            snapshot = global_store.get_latest_quotes()
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
            return

        if not snapshot:
            return

        self.update_quotes(
            _quotes_for_codes(snapshot, self._code_row_index, codes),
            record_flash=record_flash,
        )

    def supportedDropActions(self):
        return Qt.DropAction.MoveAction

    def flags(self, index):
        if index.isValid():
            return (
                Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsDragEnabled
                | Qt.ItemFlag.ItemIsDropEnabled
            )
        return Qt.ItemFlag.ItemIsDropEnabled

    def mimeTypes(self):
        return ["application/x-watchlist-row"]

    def mimeData(self, indices):
        import json

        mime_data = QMimeData()
        rows = list(set([i.row() for i in indices]))
        if not rows:
            return mime_data
        data_str = json.dumps(rows).encode("utf-8")
        mime_data.setData("application/x-watchlist-row", data_str)
        return mime_data

    def dropMimeData(self, data, action, row, column, parent):
        if not data.hasFormat("application/x-watchlist-row"):
            return False
        import json

        try:
            drag_rows = sorted(json.loads(data.data("application/x-watchlist-row").data().decode("utf-8")))
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
        self._clear_sort_value_cache()
        self.clear_presentation_cache()
        codes = [d.get("代码") for d in new_data if d.get("代码")]
        self.sig_rows_reordered.emit(codes)
        return False

    def set_cell_value(
        self,
        row,
        col_name,
        new_val,
        emit_signal: bool = True,
        *,
        record_flash: bool = True,
    ):
        flash_recorded = False
        if 0 <= row < len(self._data):
            old_val = self._data[row].get(col_name)
            self._data[row][col_name] = new_val
            if col_name == "代码" and old_val != new_val:
                self._rebuild_code_row_index()
            self._clear_presentation_cache_for_rows((row,))
            if col_name in _MONEY_BAR_HEADERS:
                _clear_money_bar_cache_for_changed_value(self, old_val, new_val)

            try:
                col_idx = self._headers.index(col_name)
            except ValueError:
                col_idx = -1
            if col_idx >= 0:
                self._sort_value_cache.pop((row, col_idx), None)
                if record_flash:
                    flash_recorded = self._record_cell_flash(row, col_idx, old_val, new_val)

            if emit_signal and col_idx >= 0:
                idx = self.index(row, col_idx)
                self.dataChanged.emit(idx, idx, self._flash_roles(include_flash=flash_recorded))
        return flash_recorded

    def update_quotes(
        self,
        quotes: Mapping[str, Mapping[str, Any]],
        *,
        record_flash: bool = True,
    ) -> int:
        started_at = time.perf_counter()
        payload_codes = len(quotes or {})
        scanned_rows = 0
        if not quotes or not self._data:
            return 0
        _prune_flash_records(self._flash_records)

        changed_row_set = set()
        flash_recorded = False
        start_col, end_col = _quote_column_bounds(self._headers)
        buy_point_col = self._headers.index("买点") if "买点" in self._headers else -1
        buy_point_clock = None

        for row, q in _iter_indexed_quote_rows(quotes, self._code_row_index):
            item_dict = self._data[row]
            scanned_rows += 1
            metrics = resolve_quote_metrics(item_dict, q)
            rt_close = float(metrics.get("rt_close", 0) or 0)
            pct = metrics.get("pct")
            row_changed = _apply_quote_metadata_to_row(item_dict, q)
            row_changed = apply_quote_total_shares_to_row(item_dict, metrics) or row_changed

            price_text = metrics.get("price_text")
            if price_text is not None:
                for price_key in ("现价", "市价"):
                    if price_key in self._headers and item_dict.get(price_key) != price_text:
                        flash_recorded = (
                            self.set_cell_value(
                                row,
                                price_key,
                                price_text,
                                emit_signal=False,
                                record_flash=record_flash,
                            )
                            or flash_recorded
                        )
                        row_changed = True
            if pct is not None:
                for pct_key in ("涨幅%", "涨幅"):
                    if pct_key in self._headers and item_dict.get(pct_key) != pct:
                        flash_recorded = (
                            self.set_cell_value(
                                row,
                                pct_key,
                                pct,
                                emit_signal=False,
                                record_flash=record_flash,
                            )
                            or flash_recorded
                        )
                        row_changed = True

            if "市值" in self._headers:
                cap_text = metrics.get("market_cap_text")
                if cap_text and item_dict.get("市值") != cap_text:
                    flash_recorded = (
                        self.set_cell_value(
                            row,
                            "市值",
                            cap_text,
                            emit_signal=False,
                            record_flash=record_flash,
                        )
                        or flash_recorded
                    )
                    row_changed = True

            if row_changed:
                changed_row_set.add(row)

            if buy_point_col >= 0 and rt_close > 0:
                history = item_dict.get("_history_20", [])
                history_date = item_dict.get("_history_date", "")
                pos_str = ""

                if history:
                    if buy_point_clock is None:
                        import datetime

                        from app.services.ui_market_calendar_service import MarketCalendar

                        now = datetime.datetime.now()
                        today_str = now.strftime("%Y-%m-%d")
                        now_time = now.strftime("%H:%M")
                        buy_point_clock = (
                            today_str,
                            now_time >= "09:15" and MarketCalendar.is_trade_day(today_str),
                        )
                    today_str, is_trade_refresh_time = buy_point_clock

                    if history_date == today_str:
                        temp_hist = history[:-1] + [rt_close]
                    elif is_trade_refresh_time:
                        temp_hist = history[1:] + [rt_close]
                    else:
                        temp_hist = history[:-1] + [rt_close]

                    rt_open = float(q.get("open") or rt_close)
                    pos_str = calculate_buy_point_from_history(
                        history=temp_hist,
                        open_price=rt_open,
                        close_price=rt_close,
                        style=BUY_POINT_STYLE_TEXT,
                    )

                if pos_str != item_dict.get("买点", ""):
                    flash_recorded = (
                        self.set_cell_value(
                            row,
                            "买点",
                            pos_str,
                            emit_signal=False,
                            record_flash=record_flash,
                        )
                        or flash_recorded
                    )
                    changed_row_set.add(row)

        if not record_flash:
            # A hidden projection has no user-visible delta.  Drop prior flash
            # metadata for touched rows so it cannot revive on the next tab
            # reveal and force the expensive custom-paint path.
            self._clear_flash_records_for_rows(changed_row_set)

        if changed_row_set and start_col is not None and end_col is not None:
            if buy_point_col >= 0:
                start_col = min(start_col, buy_point_col)
                end_col = max(end_col, buy_point_col)
            _emit_model_row_ranges(
                self,
                sorted(changed_row_set),
                start_col,
                end_col,
                self._flash_roles(include_flash=flash_recorded),
                coalesce=(
                    self._sparse_update_coalescing
                    if self._sparse_quote_update_coalescing is None
                    else self._sparse_quote_update_coalescing
                ),
            )
        changed_row_count = len(changed_row_set)
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        record_metric(
            "stock_table_update_quotes_ms",
            elapsed_ms,
            unit="ms",
            tags={
                "model": self.__class__.__name__,
                "payload_codes": str(payload_codes),
                "scanned_rows": str(scanned_rows),
                "changed_rows": str(changed_row_count),
                "record_flash": str(bool(record_flash)).lower(),
            },
        )
        return changed_row_count

    def _display_value(self, row, key, raw_val, item_dict):
        if key == SERIAL_HEADER:
            return str(row + 1)
        if key == "PE":
            if raw_val in ("", "--", None):
                return "--"
            try:
                return f"{float(raw_val):.2f}"
            except (ValueError, TypeError):
                return str(raw_val)
        if key == "风控":
            return ""
        if key == "状态":
            return str(raw_val or "").replace("🟢", "").replace("🟡", "").replace("🔴", "").strip()
        if key in {"买点", "龙虎榜"} and str(raw_val or "").strip() == BUY_POINT_TEXT:
            return BUY_POINT_TRIGGER_ICON
        if "日" in key or "期" in key or "时间" in key:
            s_val = str(raw_val).split(" ")[0].replace("-", "").replace("/", "")
            if len(s_val) == 8 and s_val.isdigit() and s_val.startswith("20"):
                return s_val
        market_cap_text = _format_market_cap_display(key, raw_val)
        if market_cap_text is not None:
            return market_cap_text
        if _is_pct_like_header(key):
            pct_text = self._percent_display_value(key, raw_val)
            if pct_text is not None:
                return pct_text
        return _summarize_long_text(key, raw_val)

    @staticmethod
    def _percent_display_value(key, raw_val):
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
            return None

    @staticmethod
    def _tooltip_value(key, raw_val, item_dict):
        return _tooltip_for_cell(key, raw_val, item_dict)

    @staticmethod
    def _alignment_value(key, raw_val):
        if key in {"风控", "评级"}:
            return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        return _alignment_for_cell(key, raw_val)

    def _font_value(self, key, raw_val, item_dict):
        if key == SERIAL_HEADER:
            return self.mono_font
        if key == "评级":
            return self.bold_mono_font
        if key in {"最近上榜", "换手率%"}:
            return self.small_mono_font
        if key == "AI细分板块/备注":
            return self.small_font
        if key in {"代码", "现价"} and "上榜净买额(万)" in self._headers:
            return self.bold_mono_font
        if _is_strong_market_move(key, raw_val, item_dict):
            return self.bold_mono_font
        if _is_numeric_header(key) or _is_date_like_header(key):
            return self.mono_font
        return self.base_font

    def _base_foreground_value(self, key, raw_val, item_dict):
        if key == SERIAL_HEADER:
            return QColor(_c("TEXT_SECONDARY"))
        if key == "名称":
            from app.services.ui_watchlist_service import watchlist_vm

            code = str(item_dict.get("代码", ""))
            if watchlist_vm.is_in_watchlist(code):
                return QColor(_c("BRAND_HOVER"))
        if key == "变化类型" and str(raw_val or "").strip() in {"新进", "增持"}:
            return QColor(_c("BRAND_HOVER"))
        if key in {"最近上榜", "换手率%", "AI细分板块/备注"} or self._uses_muted_text(key):
            return QColor(_c("TEXT_MUTED"))
        if key == "评级":
            return QColor(_c("COLOR_WARNING"))
        if self._uses_plain_style(key) or _is_date_like_header(key):
            return QColor(_c("TEXT_PRIMARY"))
        return None

    @staticmethod
    def _market_move_foreground_value(key, raw_val, item_dict):
        if not ((_is_pct_like_header(key) and "换手" not in key) or key in ["净额", "现价", "市价", "收盘", "最新价"]):
            return None
        try:
            target_pct = raw_val
            if key in ["现价", "市价", "收盘", "最新价"]:
                target_pct = item_dict.get("涨幅%") or item_dict.get("涨幅") or item_dict.get("涨跌") or "0"

            pct = float(str(target_pct).replace("%", "").replace("+", "").replace(",", ""))
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

    @staticmethod
    def _broker_foreground_value(key, raw_val):
        if key not in {"卖方营业部", "买方营业部"}:
            return None
        val_str = str(raw_val)
        if not any(kw in val_str for kw in FOREIGN_BROKER_KEYWORDS):
            return None
        return QColor(_c("COLOR_FALL" if key == "卖方营业部" else "COLOR_RISE"))

    @staticmethod
    def _trade_detail_foreground_value(key, raw_val):
        if key != "交易详情":
            return None
        val_str = str(raw_val)
        if "对倒" in val_str:
            return QColor("#F59E0B")
        if "买/" in val_str or "/卖" in val_str:
            return QColor("#3B82F6")
        if "卖出" in val_str:
            return QColor(_c("COLOR_FALL"))
        if "买入" in val_str:
            return QColor(_c("COLOR_RISE"))
        return None

    @staticmethod
    def _amount_foreground_value(key, raw_val):
        if key != "成交金额(万元)":
            return None
        try:
            f_val = float(str(raw_val).replace(",", ""))
            if f_val >= 10000:
                return QColor(_c("COLOR_RISE"))
        except (ValueError, TypeError):
            pass
        return None

    @staticmethod
    def _net_buy_foreground_value(key, raw_val):
        return _signed_amount_foreground(key, raw_val)

    @staticmethod
    def _foreign_net_foreground_value(key, item_dict):
        if key not in {"外资净买入", "外资潜伏池"}:
            return None
        try:
            f_val = float(item_dict.get("外资净买(万)", 0) or 0)
            if f_val > 0:
                return QColor(_c("COLOR_RISE"))
            if f_val < 0:
                return QColor(_c("COLOR_FALL"))
            if key == "外资净买入":
                return QColor(_c("TEXT_SECONDARY"))
        except (ValueError, TypeError):
            pass
        return None

    @staticmethod
    def _status_foreground_value(key, raw_val):
        if key != "突破状态" or str(raw_val) == "":
            return None
        st = str(raw_val)
        if "放量" in st:
            return QColor(_c("COLOR_RISE_STRONG"))
        if "缩量" in st:
            return QColor(_c("COLOR_WARNING"))
        if "临近" in st:
            return QColor(_c("STATUS_APPROACHING"))
        if "VCP" in st:
            return QColor(_c("STATUS_VCP"))
        return QColor(_c("STATUS_INACTIVE"))

    @staticmethod
    def _elasticity_foreground_value(key, raw_val):
        if key != "股价弹性" or str(raw_val) == "":
            return None
        if "高" in str(raw_val):
            return QColor(_c("COLOR_RISE"))
        return QColor(_c("TEXT_PRIMARY"))

    @staticmethod
    def _score_foreground_value(key, raw_val):
        if key != "评分" or str(raw_val) == "":
            return None
        try:
            score = float(str(raw_val))
            if score >= 90:
                return QColor(_c("SCORE_EXCELLENT"))
            if score >= 80:
                return QColor(_c("SCORE_GOOD"))
            if score >= 60:
                return QColor(_c("SCORE_NORMAL"))
            return QColor(_c("SCORE_LOW"))
        except (ValueError, TypeError):
            return None

    def _foreground_value(self, key, raw_val, item_dict):
        color = self._base_foreground_value(key, raw_val, item_dict)
        if color is not None:
            return color
        for color in (
            self._market_move_foreground_value(key, raw_val, item_dict),
            self._broker_foreground_value(key, raw_val),
            self._trade_detail_foreground_value(key, raw_val),
            self._amount_foreground_value(key, raw_val),
            self._net_buy_foreground_value(key, raw_val),
            self._foreign_net_foreground_value(key, item_dict),
            self._status_foreground_value(key, raw_val),
            self._elasticity_foreground_value(key, raw_val),
            self._score_foreground_value(key, raw_val),
        ):
            if color is not None:
                return color
        return QColor(_c("TEXT_PRIMARY"))

    def _background_value(self, key, raw_val):
        if self._uses_plain_style(key) or self._uses_plain_background(key):
            return None
        heat_color = _numeric_heat_color(key, raw_val)
        if heat_color is not None:
            return heat_color
        return None

    @staticmethod
    def _text_sort_value(raw_val):
        s_val = str(raw_val).replace(",", "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s_val):
            return int(s_val.replace("-", ""))
        if re.fullmatch(r"\d{8}", s_val):
            return int(s_val)
        if "万" in s_val:
            m = re.search(r"([-+]?\d*\.?\d+)", s_val)
            return float(m.group(1)) * 10000 if m else 0.0
        if "亿" in s_val:
            m = re.search(r"([-+]?\d*\.?\d+)", s_val)
            return float(m.group(1)) * 100000000 if m else 0.0
        m = re.search(r"([-+]?\d*\.?\d+)", s_val)
        if m:
            return float(m.group(1))
        return str(raw_val)

    @staticmethod
    def _uncached_sort_value(row, key, raw_val, item_dict):
        if key == SERIAL_HEADER:
            return row + 1
        if key == "外资净买入":
            try:
                return float(item_dict.get("外资净买(万)", 0) or 0)
            except (ValueError, TypeError):
                return 0.0
        if key == "最近上榜":
            raw_date = str(item_dict.get("_最近上榜_raw", "") or raw_val).strip()
            if re.fullmatch(r"\d{8}", raw_date):
                return int(raw_date)
        if key == "日报时间":
            report_ts = int(item_dict.get("_report_ts", 0) or 0)
            row_rank = int(item_dict.get("_report_row_rank", 0) or 0)
            if report_ts:
                return report_ts * 1000000 + max(0, 999999 - row_rank)
        return StockTableModel._text_sort_value(raw_val)

    def _sort_value(self, row, col, key, raw_val, item_dict):
        cache_key = (row, col)
        if cache_key not in self._sort_value_cache:
            self._sort_value_cache[cache_key] = self._uncached_sort_value(row, key, raw_val, item_dict)
        return self._sort_value_cache[cache_key]

    def _status_badge_value(self, key, raw_val):
        if key == "买点" and str(raw_val or "").strip() == BUY_POINT_TEXT:
            return None
        if not self._uses_plain_style(key) and _is_status_header(key):
            return _status_badge_color(raw_val, key)
        return None

    def _accent_rail_value(self, item_dict):
        if item_dict.get("_suppress_accent_rail"):
            return None
        return _row_accent_color(item_dict, self._headers)

    def _cell_render_payload(self, row, col, key, raw_val, item_dict):
        return (
            self._accent_rail_value(item_dict),
            self._uses_plain_style(key),
            _active_flash_record(self._flash_records, row, col),
            self._status_badge_value(key, raw_val),
            self._visual_payload(key, raw_val, item_dict),
        )

    def _cell_presentation_payload(self, row, col, key, raw_val, item_dict):
        render_payload = self._cell_render_payload(row, col, key, raw_val, item_dict)
        return (
            self._display_value(row, key, raw_val, item_dict),
            self._alignment_value(key, raw_val),
            self._font_value(key, raw_val, item_dict),
            self._foreground_value(key, raw_val, item_dict),
            self._background_value(key, raw_val),
            render_payload,
        )

    def data(self, index, role):
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()
        role_value = int(role)
        cached_value = self._presentation_cache_value(row, col, role_value)
        if cached_value is not _PRESENTATION_CACHE_MISS:
            return cached_value
        item_dict = self._data[row]
        key = self._headers[col]
        raw_val = item_dict.get(key, "")

        if role == Qt.ItemDataRole.DisplayRole:
            return self._cache_presentation_value(row, col, role_value, self._display_value(row, key, raw_val, item_dict))
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._cache_presentation_value(row, col, role_value, self._tooltip_value(key, raw_val, item_dict))
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return self._cache_presentation_value(row, col, role_value, self._alignment_value(key, raw_val))
        if role == Qt.ItemDataRole.FontRole:
            return self._cache_presentation_value(row, col, role_value, self._font_value(key, raw_val, item_dict))
        if role == Qt.ItemDataRole.ForegroundRole:
            return self._cache_presentation_value(row, col, role_value, self._foreground_value(key, raw_val, item_dict))
        if role == Qt.ItemDataRole.BackgroundRole:
            return self._cache_presentation_value(row, col, role_value, self._background_value(key, raw_val))
        if role == Qt.ItemDataRole.UserRole:
            return self._sort_value(row, col, key, raw_val, item_dict)
        if role == STOCK_CELL_RENDER_ROLE:
            # Flash metadata is time-sensitive. Stable cells, which dominate a
            # full Watchlist return paint, can safely reuse the combined
            # delegate payload; live flashes keep their original dynamic path.
            payload = self._cell_render_payload(row, col, key, raw_val, item_dict)
            if payload[2] is None:
                return self._cache_presentation_value(row, col, role_value, payload)
            return payload
        if role == STOCK_CELL_PRESENTATION_ROLE:
            # A stable cell can hand every native-paint input to the delegate
            # in one model/proxy round trip. Live flashes remain dynamic.
            payload = self._cell_presentation_payload(row, col, key, raw_val, item_dict)
            if payload[-1][2] is None:
                return self._cache_presentation_value(row, col, role_value, payload)
            return payload
        custom_value = _custom_cell_role_value(self, role, row, col, key, raw_val, item_dict)
        return None if custom_value is _UNHANDLED_ROLE else custom_value

    def headerData(self, section, orientation, role):
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return self._headers[section]
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        return None
