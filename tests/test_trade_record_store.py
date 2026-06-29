from ui.trade_record_store import load_trade_records_for_security, normalize_trade_date


def test_normalize_trade_date_accepts_compact_statement_dates():
    assert normalize_trade_date("20260610") == "2026-06-10"
    assert normalize_trade_date("2026/06/10") == "2026-06-10"


def test_load_trade_records_for_security_matches_name_and_dedupes_overlap_rows(tmp_path):
    csv_path = tmp_path / "records.csv"
    csv_path.write_text(
        "\n".join(
            [
                "成交日期,证券代码,证券名称,成交数量,成交均价,成交金额,手续费,印花税,其他杂费,发生金额",
                "20260610,,奕东电子,1700,101.690,172873.00,19.95,0.00,0.00,-172894.68",
                "20260610,,奕东电子,1700,101.690,172873.00,19.95,0.00,0.00,-172894.68",
                "20260610,,麦格米特,-1100,146.580,161238.00,18.61,80.63,0.00,161137.13",
            ]
        ),
        encoding="utf-8",
    )

    records = load_trade_records_for_security("", "奕东电子", base_dir=tmp_path)

    assert len(records) == 1
    assert records[0]["date"] == "2026-06-10"
    assert records[0]["name"] == "奕东电子"
    assert records[0]["side"] == "buy"
    assert records[0]["cashAmount"] == -172894.68
