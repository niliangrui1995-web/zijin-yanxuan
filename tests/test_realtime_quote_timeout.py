import time

from vcp.data_provider import _run_blocking_call_with_timeout


def test_run_blocking_call_with_timeout_returns_result_before_deadline():
    result = _run_blocking_call_with_timeout(lambda: "ok", 0.2, "timeout")
    assert result == "ok"


def test_run_blocking_call_with_timeout_raises_timeout_error():
    start = time.time()
    try:
        _run_blocking_call_with_timeout(lambda: time.sleep(0.3), 0.05, "quote timeout")
    except TimeoutError as exc:
        elapsed = time.time() - start
        assert "quote timeout" in str(exc)
        assert elapsed < 0.2
    else:
        raise AssertionError("expected TimeoutError")
