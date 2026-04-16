from scripts.check_utf8 import scan_text_issues


def test_scan_text_issues_accepts_normal_utf8_text():
    assert scan_text_issues("紫金研选\n正常文本\n") == []


def test_scan_text_issues_detects_replacement_character():
    issues = scan_text_issues(f"状态异常：{chr(0xFFFD)}")

    assert issues == ["包含 Unicode 替换字符(可能已发生乱码)"]


def test_scan_text_issues_detects_nul_byte():
    issues = scan_text_issues("abc\x00def")

    assert issues == ["包含 NUL 字节"]
