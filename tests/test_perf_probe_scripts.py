from scripts.soak_leak_probe import _trend


def _sample(rss: float, private: float, threads: int = 20, label: str = "") -> dict:
    return {
        "label": label,
        "main": {
            "rss_mb": rss,
            "private_mb": private,
            "thread_count": threads,
        }
    }


def test_soak_trend_treats_post_warmup_plateau_as_ok():
    samples = [
        _sample(170.0, 550.0, 18),
        _sample(188.0, 564.0, 20),
        _sample(292.0, 650.0, 100),
        _sample(301.0, 720.0, 110),
        _sample(304.0, 724.0, 110),
        _sample(302.0, 722.0, 110),
    ]

    result = _trend(samples, threshold_mb=48.0)

    assert result["rss"]["status"] == "ok"
    assert result["private"]["status"] == "ok"
    assert result["threads"]["status"] == "ok"


def test_soak_trend_flags_sustained_growth():
    samples = [
        _sample(170.0, 550.0, 18),
        _sample(190.0, 580.0, 20),
        _sample(230.0, 620.0, 30),
        _sample(270.0, 670.0, 40),
        _sample(315.0, 725.0, 50),
        _sample(355.0, 780.0, 60),
    ]

    result = _trend(samples, threshold_mb=48.0)

    assert result["rss"]["status"] == "warn"
    assert result["private"]["status"] == "warn"


def test_soak_trend_uses_close_samples_for_open_close_cycles():
    samples = [
        _sample(188.0, 564.0, 20, "after_window"),
        _sample(306.0, 728.0, 111, "kline_cycle_1_open"),
        _sample(299.0, 718.0, 111, "kline_cycle_1_close"),
        _sample(307.0, 729.0, 111, "kline_cycle_2_open"),
        _sample(301.0, 721.0, 111, "kline_cycle_2_close"),
        _sample(307.0, 728.0, 111, "kline_cycle_3_open"),
        _sample(302.0, 722.0, 110, "kline_cycle_3_close"),
        _sample(302.0, 722.0, 110, "after_window_close"),
    ]

    result = _trend(samples, threshold_mb=48.0)

    assert result["growth_basis"] == "stable_close_samples"
    assert result["private"]["status"] == "ok"
