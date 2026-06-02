# -*- coding: utf-8 -*-
"""Subject-name display helpers for the fund holdings tab."""

from __future__ import annotations

import re

SUBJECT_NAME_ALIASES = (
    ("MORGANSTANLEY", "MORGAN STANLEY"),
    ("JPMORGAN", "J.P.Morgan"),
    ("BARCLAYS", "BARCLAYS"),
    ("UBS", "UBS"),
    ("GOLDMANSACHS", "GOLDMAN SACHS"),
    ("CITIGROUP", "CITI"),
    ("CITIBANK", "CITI"),
    ("MERRILLLYNCH", "MERRILL LYNCH"),
    ("BOFA", "BOFA"),
    ("HSBC", "HSBC"),
    ("NOMURA", "NOMURA"),
    ("BNPPARIBAS", "BNP PARIBAS"),
    ("DEUTSCHEBANK", "DEUTSCHE BANK"),
    ("STANDARDCHARTERED", "STANDARD CHARTERED"),
    ("SOCIETEGENERALE", "SOCIETE GENERALE"),
    ("CREDITSUISSE", "CREDIT SUISSE"),
    ("MACQUARIE", "MACQUARIE"),
    ("DAIWASECURITIES", "DAIWA"),
    ("MIZUHO", "MIZUHO"),
    ("JEFFERIES", "JEFFERIES"),
    ("CLSA", "CLSA"),
    ("KGIASIA", "KGI ASIA"),
    ("ABUDHABIINVESTMENTAUTHORITY", "ADIA"),
    ("HKSCC", "HKSCC"),
)
SUBJECT_LEGAL_SUFFIXES = frozenset({"PLC", "LIMITED", "LTD", "INC", "LLC", "LLP", "AG", "SA", "NV"})
SUBJECT_TAIL_DESCRIPTORS = frozenset(
    {"SECURITIES", "INTERNATIONAL", "MARKETS", "GLOBAL", "BANK", "CO", "COMPANY", "CORPORATION"}
)


def shorten_subject_name(subject_name: str) -> str:
    raw_name = str(subject_name or "").strip()
    if not raw_name:
        return ""

    compact_name = re.sub(r"[^A-Z0-9]+", "", raw_name.upper())
    for marker, display_name in SUBJECT_NAME_ALIASES:
        if marker in compact_name:
            return display_name

    if not re.fullmatch(r"[A-Za-z0-9 .,&'()/+-]+", raw_name):
        return raw_name

    cleaned_name = re.sub(r"[_]+", " ", raw_name.replace("&", " "))
    cleaned_name = re.sub(r"\s+", " ", cleaned_name).strip(" .,-")
    tokens = [token.strip(" .,-") for token in cleaned_name.split() if token.strip(" .,-")]
    while tokens and tokens[-1].upper().replace(".", "") in SUBJECT_LEGAL_SUFFIXES:
        tokens.pop()
    while len(tokens) > 2 and tokens[-1].upper().replace(".", "") in SUBJECT_TAIL_DESCRIPTORS:
        tokens.pop()
    if not tokens:
        return raw_name
    if len(tokens) > 3:
        tokens = tokens[:3]
    return " ".join(tokens)
