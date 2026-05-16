import json

from core.json_cache import save_json_file


def test_save_json_file_creates_missing_parent_directories(tmp_path):
    cache_file = tmp_path / "cache" / "nested" / "payload.json"

    save_json_file(str(cache_file), {"code": "000001", "values": [1, 2, 3]})

    assert cache_file.exists()
    assert json.loads(cache_file.read_text(encoding="utf-8")) == {
        "code": "000001",
        "values": [1, 2, 3],
    }
