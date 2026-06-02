from domains.quotes.tdx_name_map import (
    TNF_CODE_OFFSET,
    TNF_NAME_OFFSET,
    TNF_RECORD_SIZE,
    decode_tnf_name,
    is_placeholder_name,
    normalize_code_name_targets,
    parse_tnf_name_file,
    parse_tnf_name_payload,
)


def _build_record(code: str, name: str) -> bytes:
    record = bytearray(TNF_RECORD_SIZE)
    record[TNF_CODE_OFFSET : TNF_CODE_OFFSET + 6] = code.encode("ascii")
    name_bytes = name.encode("gbk")
    record[TNF_NAME_OFFSET : TNF_NAME_OFFSET + len(name_bytes)] = name_bytes
    return bytes(record)


def test_tdx_name_map_parses_payload_and_filters_targets():
    payload = b"".join(
        [
            _build_record("300236", "上海新阳"),
            _build_record("002709", "天赐材料"),
            _build_record("600000", "600000"),
        ]
    )

    assert parse_tnf_name_payload(payload) == {
        "300236": "上海新阳",
        "002709": "天赐材料",
    }
    assert parse_tnf_name_payload(payload, target_codes={"002709"}) == {"002709": "天赐材料"}


def test_tdx_name_map_normalizes_target_codes():
    assert normalize_code_name_targets([" 300236 ", "bad", "300236", "002709"]) == ["300236", "002709"]


def test_tdx_name_map_decode_and_file_edges(monkeypatch, tmp_path):
    assert is_placeholder_name("000001", "") is True
    assert is_placeholder_name("000001", "000001") is True
    assert decode_tnf_name(b"\x00 padded") == ""
    assert decode_tnf_name(b"\xff\xff") == "ÿÿ"

    invalid_record = bytearray(TNF_RECORD_SIZE)
    invalid_record[TNF_CODE_OFFSET : TNF_CODE_OFFSET + 6] = b"ABCDEF"
    assert parse_tnf_name_payload(bytes(invalid_record)) == {}

    assert parse_tnf_name_file(str(tmp_path / "missing.tnf")) == {}

    existing = tmp_path / "bad.tnf"
    existing.write_bytes(b"bad")
    monkeypatch.setattr("builtins.open", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read failed")))
    assert parse_tnf_name_file(str(existing)) == {}
