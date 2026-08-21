# -*- coding: utf-8 -*-
from __future__ import annotations

import os

TNF_RECORD_SIZE = 360
TNF_CODE_OFFSET = 50
TNF_NAME_OFFSET = 81
TNF_NAME_FIELD_LEN = 45
TNF_NAME_FILES = ("shs.tnf", "szs.tnf", "bjs.tnf")


def is_placeholder_name(code, name) -> bool:
    code_text = str(code or "").strip()
    name_text = str(name or "").strip()
    return not name_text or name_text == code_text


def normalize_code_name_targets(codes) -> list[str]:
    normalized: list[str] = []
    for raw_code in codes or []:
        code = str(raw_code or "").strip()
        if len(code) == 6 and code.isdigit():
            normalized.append(code)
    return list(dict.fromkeys(normalized))


def decode_tnf_name(raw_name: bytes) -> str:
    payload = bytes(raw_name or b"").split(b"\x00", 1)[0].rstrip(b" \x00")
    if not payload:
        return ""
    for encoding in ("gbk", "gb18030", "utf-8"):
        try:
            return payload.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return payload.decode("latin1", errors="ignore").strip()


def parse_tnf_name_payload(payload: bytes, target_codes: set[str] | None = None) -> dict[str, str]:
    remaining = set(target_codes or set())
    has_target_filter = bool(remaining)
    code_names: dict[str, str] = {}
    code_start = TNF_CODE_OFFSET
    code_end = code_start + 6
    name_start = TNF_NAME_OFFSET
    name_end = name_start + TNF_NAME_FIELD_LEN

    for offset in range(0, len(payload) - TNF_RECORD_SIZE + 1, TNF_RECORD_SIZE):
        if has_target_filter and not remaining:
            break
        record = payload[offset : offset + TNF_RECORD_SIZE]
        code_bytes = record[code_start:code_end]
        if len(code_bytes) != 6 or not code_bytes.isdigit():
            continue
        code = code_bytes.decode("ascii", errors="ignore").strip()
        if len(code) != 6:
            continue
        if has_target_filter and code not in remaining:
            continue
        name = decode_tnf_name(record[name_start:name_end])
        if not is_placeholder_name(code, name):
            code_names[code] = name
        if has_target_filter:
            remaining.discard(code)

    return code_names


def parse_tnf_name_file(tnf_path: str, target_codes: set[str] | None = None) -> dict[str, str]:
    if not tnf_path or not os.path.exists(tnf_path):
        return {}

    try:
        with open(tnf_path, "rb") as handle:
            payload = handle.read()
    except OSError:
        return {}

    return parse_tnf_name_payload(payload, target_codes=target_codes)
