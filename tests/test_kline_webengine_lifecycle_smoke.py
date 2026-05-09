from scripts.kline_webengine_lifecycle_smoke import _summarize_cycles, evaluate_lifecycle


def _sample(label: str, children: int) -> dict:
    return {
        "label": label,
        "webengine_child_count": children,
    }


def test_kline_lifecycle_evaluation_accepts_reclaimed_webengine_child():
    summary = evaluate_lifecycle(
        [
            _sample("baseline", 0),
            _sample("after_open", 2),
            _sample("after_close", 0),
        ],
        opened=True,
        blocked=False,
        load_events=[True],
    )

    assert summary["status"] == "ok"
    assert summary["webengine_child_seen"] is True
    assert summary["webengine_child_reclaimed"] is True


def test_kline_lifecycle_evaluation_rejects_child_process_retention():
    summary = evaluate_lifecycle(
        [
            _sample("baseline", 0),
            _sample("after_open", 2),
            _sample("after_close", 1),
        ],
        opened=True,
        blocked=False,
        load_events=[True],
    )

    assert summary["status"] == "fail"
    assert summary["webengine_child_reclaimed"] is False


def test_kline_lifecycle_evaluation_rejects_load_failure():
    summary = evaluate_lifecycle(
        [
            _sample("baseline", 0),
            _sample("after_open", 1),
            _sample("after_close", 0),
        ],
        opened=True,
        blocked=False,
        load_events=[False],
    )

    assert summary["status"] == "fail"
    assert summary["load_failed"] is True


def test_kline_lifecycle_summary_supports_multiple_cycles():
    samples = [
        _sample("cycle_1:before_open", 0),
        _sample("cycle_1:after_open", 2),
        _sample("cycle_1:after_close", 0),
        _sample("cycle_2:before_open", 0),
        _sample("cycle_2:after_open", 1),
        _sample("cycle_2:after_close", 0),
    ]
    cycles = [
        {"cycle_index": 1, "summary": {"status": "ok"}},
        {"cycle_index": 2, "summary": {"status": "ok"}},
    ]

    summary = _summarize_cycles(cycles, samples)

    assert summary["status"] == "ok"
    assert summary["cycles"] == 2
    assert summary["ok_cycles"] == 2
    assert summary["failed_cycles"] == []
    assert summary["final_webengine_child_count"] == 0
