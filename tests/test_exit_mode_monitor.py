import timed_exit_monitor as tem


def test_is_engine_a_engine():
    assert tem._is_engine_a_engine("engine_a") is True
    assert tem._is_engine_a_engine("Engine A") is True
    for e in ("engine_b", "scalp", "", None):
        assert tem._is_engine_a_engine(e) is False


def test_dispatch_non_engine_a_always_trails():
    assert tem._engine_a_exit_dispatch("engine_b", "traditional_static", 999, 10) == "trail"


def test_dispatch_engine_a_adaptive_trails():
    assert tem._engine_a_exit_dispatch("engine_a", "adaptive_trail", 999, 10) == "trail"


def test_dispatch_engine_a_static_and_manual_hold():
    assert tem._engine_a_exit_dispatch("engine_a", "traditional_static", 999, 10) == "hold"
    assert tem._engine_a_exit_dispatch("engine_a", "manual", 999, 10) == "hold"


def test_dispatch_engine_a_unknown_mode_trails():
    # NULL/blank exit_mode (legacy rows) -> safe default to today's behavior.
    assert tem._engine_a_exit_dispatch("engine_a", None, 999, 10) == "trail"
    assert tem._engine_a_exit_dispatch("engine_a", "junk", 999, 10) == "trail"


def test_dispatch_engine_a_time_based():
    assert tem._engine_a_exit_dispatch("engine_a", "time_based", 5, 10) == "hold"          # not due
    assert tem._engine_a_exit_dispatch("engine_a", "time_based", 10, 10) == "timed_close"  # due
    assert tem._engine_a_exit_dispatch("engine_a", "time_based", 99, 10) == "timed_close"


def test_time_close_after_min_uses_bars_times_tf():
    tcfg = {
        "engine_a_time_exit_bars": {"scalp": 12, "intraday": 18, "swing": 10},
        "trail_timeframe": {"scalp": "H1", "intraday": "H4", "swing": "D1"},
    }
    assert tem._time_close_after_min(tcfg, "intraday") == 18 * 240   # H4
    assert tem._time_close_after_min(tcfg, "scalp") == 12 * 60       # H1
    assert tem._time_close_after_min(tcfg, "swing") == 10 * 1440     # D1


def test_time_close_after_min_falls_back_to_defaults():
    # Missing maps -> built-in bar defaults and H4 timeframe.
    assert tem._time_close_after_min({}, "intraday") == 18 * 240
