from scripts.runtime_env_self_check import build_report


def test_runtime_env_self_check_report_has_required_sections():
    report = build_report(skip_webengine_preflight=True)

    assert report["report_type"] == "runtime_env_self_check"
    assert report["python"]["executable"]
    assert report["app"]["version"]
    assert "PyQt6" in report["imports"]
    assert "PyQt6-WebEngine" in report["imports"]
    assert report["qt_webengine_preflight"]["skipped"] is True
    assert "tdx_vipdoc" in report
    assert "rps_cache" in report["cache_files"]
    assert "sqlite_state" in report["cache_files"]
