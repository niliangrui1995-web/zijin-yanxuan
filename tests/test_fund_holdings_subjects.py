from ui.tabs.fund_holdings_subjects import shorten_subject_name


def test_shorten_subject_name_matches_known_foreign_institution_aliases():
    assert shorten_subject_name("MORGAN STANLEY&CO.INT ERNATI ONAL PLC") == "MORGAN STANLEY"
    assert shorten_subject_name("J.P.Morgan Secur ities PLC") == "J.P.Morgan"
    assert shorten_subject_name("CITIGROUP GLOBAL MARKETS LIMITED") == "CITI"
    assert shorten_subject_name("ABU DHABI INVESTMENT AUTHORITY") == "ADIA"


def test_shorten_subject_name_trims_legal_suffixes_and_tail_descriptors():
    assert shorten_subject_name("Example Global Markets Limited") == "Example Global"
    assert shorten_subject_name("Alpha Beta Gamma Delta LLC") == "Alpha Beta Gamma"
    assert shorten_subject_name("睿远成长价值混合A") == "睿远成长价值混合A"
