from pathlib import Path

import infra.storage.trade_record_repository as trade_record_store
from app.services.ui_trade_record_service import load_trade_records_for_security
from infra.storage.trade_record_repository import normalize_trade_date, resolve_trade_record_dir


def test_normalize_trade_date_accepts_compact_statement_dates():
    assert normalize_trade_date("20260610") == "2026-06-10"
    assert normalize_trade_date("2026/06/10") == "2026-06-10"


def test_load_trade_records_for_security_matches_name_and_dedupes_overlap_rows(tmp_path):
    csv_path = tmp_path / "records.csv"
    csv_path.write_text(
        "\n".join(
            [
                "成交日期,证券代码,证券名称,成交数量,成交均价,成交金额,手续费,印花税,其他杂费,发生金额",
                "20260610,000001,示例科技,100,10.000,1000.00,1.00,0.00,0.00,-1001.00",
                "20260610,000001,示例科技,100,10.000,1000.00,1.00,0.00,0.00,-1001.00",
                "20260610,000002,样例股份,-200,20.000,4000.00,2.00,4.00,0.00,3994.00",
            ]
        ),
        encoding="utf-8",
    )

    records = load_trade_records_for_security("000001", "示例科技", base_dir=tmp_path)

    assert len(records) == 1
    assert records[0]["date"] == "2026-06-10"
    assert records[0]["name"] == "示例科技"
    assert records[0]["side"] == "buy"
    assert records[0]["cashAmount"] == -1001.0


def test_trade_record_directory_uses_user_data_location_and_honors_override(tmp_path):
    override = tmp_path / "custom-trades"

    assert resolve_trade_record_dir({"VCP_HUNTER_TRADE_RECORD_DIR": str(override)}) == override
    assert resolve_trade_record_dir({"LOCALAPPDATA": "C:/Users/Test/AppData/Local"}) == Path(
        "C:/Users/Test/AppData/Local/ZijinYanxuan/Trade"
    )


def test_default_load_copies_legacy_trade_csv_without_deleting_source(monkeypatch, tmp_path):
    legacy_dir = tmp_path / "repo" / "data" / "Trade"
    user_dir = tmp_path / "local-app-data" / "ZijinYanxuan" / "Trade"
    legacy_dir.mkdir(parents=True)
    source = legacy_dir / "legacy.csv"
    source.write_text(
        "成交日期,证券代码,证券名称,成交数量,成交均价,成交金额\n"
        "20260610,000001,示例科技,100,10.0,1000.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(trade_record_store, "LEGACY_TRADE_RECORD_DIR", legacy_dir)
    monkeypatch.setattr(trade_record_store, "TRADE_RECORD_DIR", user_dir)

    records = trade_record_store.load_all_trade_records()

    assert source.exists()
    assert (user_dir / source.name).read_bytes() == source.read_bytes()
    assert [record["code"] for record in records] == ["000001"]


def test_ui_trade_record_module_is_only_a_compatibility_facade():
    source = Path("ui/trade_record_store.py").read_text(encoding="utf-8")

    assert "app.services.ui_trade_record_service" in source
    assert "import csv" not in source
    assert "path.open(" not in source
