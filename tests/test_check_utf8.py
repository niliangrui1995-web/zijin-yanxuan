from scripts.check_utf8 import DEFAULT_TARGETS, scan_text_issues


def test_default_targets_include_guardrail_paths():
    assert {"domains", "earnings", "requirements-dev.txt"}.issubset(set(DEFAULT_TARGETS))


def test_scan_text_issues_accepts_normal_utf8_text():
    assert scan_text_issues("紫金研选\n正常文本\n") == []


def test_scan_text_issues_detects_replacement_character():
    issues = scan_text_issues(f"状态异常：{chr(0xFFFD)}")

    assert issues == ["包含 Unicode 替换字符(可能已发生乱码)"]


def test_scan_text_issues_detects_nul_byte():
    issues = scan_text_issues("abc\x00def")

    assert issues == ["包含 NUL 字节"]


def test_scan_text_issues_detects_valid_utf8_mojibake():
    issues = scan_text_issues(
        "\u6807\u9898\uff1a\u7ef1\ue0a6\u567e\u942e\u65c8\u20ac\u5910\u567a\u9356\u682b\u7c93\u7ed4?\n"
    )

    assert issues == ["包含疑似 mojibake 文本(合法 UTF-8 但像是错误解码后的中文)"]


def test_scan_text_issues_allows_legacy_mojibake_code_key_escape():
    text = 'LEGACY_MOJIBAKE_CODE_KEY = "\\u6d60\\uff47\\u721c"\nROW_IDENTITY_KEYS = ("代码", LEGACY_MOJIBAKE_CODE_KEY)'

    assert scan_text_issues(text) == []
