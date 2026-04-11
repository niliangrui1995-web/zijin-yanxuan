from ui.tabs.base_stock_tab import BaseStockTab


def test_normalize_quote_code_extracts_six_digits():
    assert BaseStockTab._normalize_quote_code("SH600519") == "600519"
    assert BaseStockTab._normalize_quote_code("sz.000001") == "000001"
    assert BaseStockTab._normalize_quote_code(" bj430047 ") == "430047"


def test_detect_quote_prefix_handles_sh_sz_bj():
    assert BaseStockTab._detect_quote_prefix("600519") == "SH"
    assert BaseStockTab._detect_quote_prefix("000001") == "SZ"
    assert BaseStockTab._detect_quote_prefix("430047") == "BJ"
