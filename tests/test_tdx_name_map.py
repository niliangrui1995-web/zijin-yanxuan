from domains.quotes.tdx_name_map import (
    TNF_CODE_OFFSET,
    TNF_NAME_OFFSET,
    TNF_RECORD_SIZE,
    normalize_code_name_targets,
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
