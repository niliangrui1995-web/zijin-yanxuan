import os
import time

import core.cache_policy as cache_policy
from core.logger import _clean_old_logs


def _touch(path, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_cleanup_stale_caches_prunes_asian_history_without_latest(tmp_path, monkeypatch):
    now = 1_800_000_000
    old = now - 5 * 86400
    recent = now - 1 * 86400
    cache_dir = tmp_path / "data" / "Cache"

    stale_history = cache_dir / "asian_klines_20260520_1635.json"
    recent_history = cache_dir / "asian_klines_20260523_1855.json"
    latest_snapshot = cache_dir / "asian_klines_latest.json"
    stale_tmp = cache_dir / "vcp_prices_matrix.parquet.123.tmp"
    _touch(stale_history, old)
    _touch(recent_history, recent)
    _touch(latest_snapshot, old)
    _touch(stale_tmp, old)

    monkeypatch.setattr(cache_policy.time, "time", lambda: now)

    result = cache_policy.cleanup_stale_caches(str(tmp_path))

    assert result["cleaned"] == 2
    assert not stale_history.exists()
    assert not stale_tmp.exists()
    assert recent_history.exists()
    assert latest_snapshot.exists()


def test_clean_old_logs_removes_rotated_log_files(tmp_path):
    now = time.time()
    old = now - 10 * 86400
    recent = now - 1 * 86400

    old_plain = tmp_path / "vcp_20260501.log"
    old_rotated = tmp_path / "vcp_20260501.log.1"
    recent_rotated = tmp_path / "vcp_20260523.log.1"
    unrelated = tmp_path / "notes.txt"
    for path, mtime in (
        (old_plain, old),
        (old_rotated, old),
        (recent_rotated, recent),
        (unrelated, old),
    ):
        _touch(path, mtime)

    _clean_old_logs(str(tmp_path), max_age_days=7)

    assert not old_plain.exists()
    assert not old_rotated.exists()
    assert recent_rotated.exists()
    assert unrelated.exists()
