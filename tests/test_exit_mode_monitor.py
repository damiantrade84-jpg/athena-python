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


def test_row_for_live_position_threads_exit_mode():
    now = "2026-06-10T12:00:00+00:00"
    audit_rows = [
        {
            "id": 7,
            "ticket": "555",
            "pair": "EUR/USD",
            "engine": "engine_a",
            "style": "intraday",
            "ts": now,
            "direction": "LONG",
            "entry_price": 1.1000,
            "sl": 1.0900,
            "tp": 1.1200,
            "volume": 0.02,
            "risk_amount": 20.0,
            "grade": "EXECUTED",
            "exit_mode": "traditional_static",
            "exit_time": None,
        }
    ]
    pos = {
        "ticket": "555",
        "pair": "EUR/USD",
        "direction": "LONG",
        "entry": 1.1000,
    }
    row = tem._row_for_live_position(pos, audit_rows)
    assert row is not None
    assert row["exit_mode"] == "traditional_static"
    assert tem._engine_a_exit_dispatch(
        row["engine"], row["exit_mode"], 999, 10
    ) == "hold"


def test_exit_mode_resolution_matches_exit_strategy_defaults():
    import exit_policy as ep

    cfg = {
        "ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT": "traditional_static",
        "ENGINE_A_EXIT_MODE_BY_SCORE_GROUP": {"forex_majors": "adaptive_trail"},
    }
    assert ep.resolve_exit_mode(
        per_trade=None,
        group_default=ep.group_default_for("forex_majors", cfg["ENGINE_A_EXIT_MODE_BY_SCORE_GROUP"]),
        global_default=cfg["ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT"],
    ) == "adaptive_trail"
    assert ep.resolve_exit_mode(
        per_trade="adaptive_trail",
        group_default=ep.group_default_for("crypto", cfg["ENGINE_A_EXIT_MODE_BY_SCORE_GROUP"]),
        global_default=cfg["ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT"],
    ) == "adaptive_trail"
    assert ep.resolve_exit_mode(
        per_trade=None,
        group_default=ep.group_default_for("crypto", cfg["ENGINE_A_EXIT_MODE_BY_SCORE_GROUP"]),
        global_default=cfg["ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT"],
    ) == "traditional_static"


def test_row_for_live_position_legacy_null_exit_mode_trails():
    now = "2026-06-10T12:00:00+00:00"
    audit_rows = [
        {
            "id": 8,
            "ticket": "556",
            "pair": "EUR/USD",
            "engine": "engine_a",
            "style": "intraday",
            "ts": now,
            "direction": "LONG",
            "entry_price": 1.1000,
            "sl": 1.0900,
            "tp": 1.1200,
            "volume": 0.01,
            "risk_amount": 10.0,
            "grade": "EXECUTED",
            "exit_mode": None,
            "exit_time": None,
        }
    ]
    pos = {"ticket": "556", "pair": "EUR/USD", "direction": "LONG", "entry": 1.1000}
    row = tem._row_for_live_position(pos, audit_rows)
    assert row is not None
    assert row.get("exit_mode") is None
    assert tem._engine_a_exit_dispatch(row["engine"], row["exit_mode"], 999, 10) == "trail"
