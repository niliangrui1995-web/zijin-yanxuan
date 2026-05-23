# ui/components.py - 通用 UI 组件
# 从 main_window_qt.py 拆分出来的独立工具类
from functools import lru_cache

from .table_controls import (
    MultiSelectFilterButton as MultiSelectFilterButton,
)
from .table_controls import (
    PulsingDot as PulsingDot,
)
from .table_controls import (
    StatusGlyph as StatusGlyph,
)
from .table_controls import (
    TableStateOverlay as TableStateOverlay,
)
from .table_controls import (
    TableStateWrapper as TableStateWrapper,
)
from .table_controls import (
    VCPTableView as VCPTableView,
)
from .table_controls import (
    format_multi_select_summary as format_multi_select_summary,
)
from .toggle_switch import ToggleSwitch as ToggleSwitch


class SearchFilter:
    @staticmethod
    @lru_cache(maxsize=4096)
    def _build_initial_options(name_text: str):
        import pypinyin

        options = []
        heteronym_groups = pypinyin.pinyin(
            name_text,
            style=pypinyin.Style.FIRST_LETTER,
            heteronym=True,
            errors=lambda item: list(str(item).lower()),
        )
        for group in heteronym_groups:
            normalized = {str(val).strip().lower() for val in group if str(val).strip()}
            if normalized:
                options.append(normalized)
        return tuple(options)

    @classmethod
    def _match_pinyin_initials(cls, search_val: str, name_text: str) -> bool:
        if not search_val or not name_text:
            return False

        initial_options = cls._build_initial_options(name_text)
        query = str(search_val).strip().lower()
        query_len = len(query)
        total = len(initial_options)
        if query_len == 0 or total == 0 or query_len > total:
            return False

        for start in range(total - query_len + 1):
            if all(query[offset] in initial_options[start + offset] for offset in range(query_len)):
                return True
        return False

    @staticmethod
    def match_pinyin_or_text(search_val, code_text, name_text):
        """辅助方法: 判断 search_val 是否匹配代码、名称或拼音首字母"""
        if not search_val:
            return True
        if search_val in code_text or search_val in name_text:
            return True

        return SearchFilter._match_pinyin_initials(search_val, name_text)
