# -*- coding: utf-8 -*-
"""Filter proxy model for the fund-holdings tab."""

from __future__ import annotations

from ui.components import SearchFilter
from ui.models.table_models import RtSortFilterProxyModel


class FundHoldingsFilterProxyModel(RtSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._subject_names: set[str] = set()
        self._capital_attributes: set[str] = set()
        self._quarter_keys: set[str] = set()
        self._change_types: set[str] = set()
        self._latest_only = True

    @staticmethod
    def _normalized_values(values) -> set[str]:
        return {str(value or "").strip() for value in (values or []) if str(value or "").strip()}

    def set_filter_state(
        self,
        *,
        subject_names=None,
        capital_attributes=None,
        quarter_keys=None,
        change_types=None,
        latest_only=None,
        filter_text=None,
    ) -> None:
        changed = False

        if subject_names is not None:
            normalized = self._normalized_values(subject_names)
            changed = changed or normalized != self._subject_names
            self._subject_names = normalized

        if capital_attributes is not None:
            normalized = self._normalized_values(capital_attributes)
            changed = changed or normalized != self._capital_attributes
            self._capital_attributes = normalized

        if quarter_keys is not None:
            normalized = self._normalized_values(quarter_keys)
            changed = changed or normalized != self._quarter_keys
            self._quarter_keys = normalized

        if change_types is not None:
            normalized = self._normalized_values(change_types)
            changed = changed or normalized != self._change_types
            self._change_types = normalized

        if latest_only is not None:
            normalized_latest_only = bool(latest_only)
            changed = changed or normalized_latest_only != self._latest_only
            self._latest_only = normalized_latest_only

        if filter_text is not None:
            normalized_text = str(filter_text or "").strip().lower()
            changed = changed or normalized_text != self._filter_text
            self._filter_text = normalized_text

        if changed:
            self.invalidateFilter()

    def set_subject_name(self, subject_name: str):
        self.set_subject_names([subject_name] if subject_name else [])

    def set_subject_names(self, subject_names):
        self.set_filter_state(subject_names=subject_names)

    def set_capital_attributes(self, capital_attributes):
        self.set_filter_state(capital_attributes=capital_attributes)

    def set_quarter_keys(self, quarter_keys):
        self.set_filter_state(quarter_keys=quarter_keys)

    def set_change_types(self, change_types):
        self.set_filter_state(change_types=change_types)

    def set_latest_only(self, latest_only: bool):
        self.set_filter_state(latest_only=latest_only)

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        row_data = model.row_data[source_row]

        if self._subject_names and str(row_data.get("主体", "")).strip() not in self._subject_names:
            return False

        if (
            self._capital_attributes
            and str(row_data.get("_capital_attribute_value", "")).strip() not in self._capital_attributes
        ):
            return False

        if self._change_types and str(row_data.get("变化类型", "")).strip() not in self._change_types:
            return False

        if self._quarter_keys and str(row_data.get("季度", "")).strip() not in self._quarter_keys:
            return False

        if self._latest_only and not bool(row_data.get("_is_latest_subject_quarter")):
            return False

        filter_text = getattr(self, "_filter_text", "")
        if not filter_text:
            return True

        code_text = str(row_data.get("代码", "") or "").lower()
        name_text = str(row_data.get("名称", "") or "").lower()
        subject_text = str(row_data.get("主体", "") or "").lower()
        if SearchFilter.match_pinyin_or_text(filter_text, code_text, name_text):
            return True
        if filter_text in subject_text:
            return True

        return any(filter_text in str(value or "").lower() for value in row_data.values())


__all__ = ["FundHoldingsFilterProxyModel"]
