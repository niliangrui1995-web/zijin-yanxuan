from app.services.runtime_services import initialize_search_filter_runtime, is_search_filter_runtime_ready
from ui.components import SearchFilter


def test_match_pinyin_or_text_supports_polyphonic_initials():
    assert SearchFilter.match_pinyin_or_text("zghx", "688048", "长光华芯")
    assert SearchFilter.match_pinyin_or_text("cghx", "688048", "长光华芯")


def test_match_pinyin_or_text_supports_partial_initials():
    assert SearchFilter.match_pinyin_or_text("ghx", "688048", "长光华芯")
    assert not SearchFilter.match_pinyin_or_text("zhxg", "688048", "长光华芯")


def test_match_pinyin_or_text_keeps_plain_text_and_code_match():
    assert SearchFilter.match_pinyin_or_text("688", "688048", "长光华芯")
    assert SearchFilter.match_pinyin_or_text("华芯", "688048", "长光华芯")


def test_preheated_search_runtime_preserves_pinyin_matching():
    initialize_search_filter_runtime()

    assert is_search_filter_runtime_ready() is True
    assert SearchFilter.match_pinyin_or_text("payh", "", "平安银行")
