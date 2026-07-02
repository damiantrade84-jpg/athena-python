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


def test_resolve_runner_directive_matrix():
    import exit_policy as ep

    scale_out = "scale_out_structural_tp1_fallback_tp2"
    single = "single_fallback_rr_tp"

    # single-TP plans
    assert ep.resolve_runner_directive(single, "traditional_static") == ep.RUNNER_TO_TP2
    assert ep.resolve_runner_directive(single, "adaptive_trail") == ep.RUNNER_TRAIL
    assert ep.resolve_runner_directive(single, "time_based") == ep.RUNNER_TIMED
    # scale-out plans
    assert ep.resolve_runner_directive(scale_out, "traditional_static") == ep.RUNNER_TO_TP2
    assert ep.resolve_runner_directive(scale_out, "adaptive_trail") == ep.RUNNER_TRAIL
    assert ep.resolve_runner_directive(scale_out, "time_based") == ep.RUNNER_TIMED
    # manual carries a fixed bracket like static
    assert ep.resolve_runner_directive(scale_out, "manual") == ep.RUNNER_TO_TP2
    # unknown mode falls back to the static default
    assert ep.resolve_runner_directive(scale_out, None) == ep.RUNNER_TO_TP2
    assert ep.resolve_runner_directive(scale_out, "junk") == ep.RUNNER_TO_TP2
    # fail-closed: invalid/unknown exit strategies never get a runner plan
    assert ep.resolve_runner_directive(None, "traditional_static") == ep.RUNNER_NONE
    assert ep.resolve_runner_directive("invalid_levels", "adaptive_trail") == ep.RUNNER_NONE
    assert ep.resolve_runner_directive("junk", "time_based") == ep.RUNNER_NONE
    # single_structural_tp normalizes to the single plan
    assert ep.normalize_exit_strategy("single_structural_tp") == ep.EXIT_STRATEGY_SINGLE
    assert ep.normalize_exit_strategy(scale_out) == ep.EXIT_STRATEGY_SCALE_OUT
    assert ep.normalize_exit_strategy("invalid_levels") is None


def test_apply_engine_b_exit_strategy_stamps_mode_and_directive():
    from exit_mode_apply import apply_engine_b_exit_strategy

    cfg = {
        "ENGINE_B_EXIT_MODE_GLOBAL_DEFAULT": "traditional_static",
        "ENGINE_B_EXIT_MODE_BY_SCORE_GROUP": {"crypto_btc": "adaptive_trail"},
    }
    sig = {
        "engine": "engine_b",
        "engine_b_exit_strategy": "scale_out_structural_tp1_fallback_tp2",
    }
    mode = apply_engine_b_exit_strategy(
        sig, "engine_b", cfg, score_group_resolver=lambda _s: "crypto_btc"
    )
    assert mode == "adaptive_trail"
    assert sig["exit_mode"] == "adaptive_trail"
    assert sig["runner_directive"] == "runner_trail"

    # Global default applies when the group has no override.
    sig_static = {
        "engine": "engine_b",
        "engine_b_exit_strategy": "single_fallback_rr_tp",
    }
    mode = apply_engine_b_exit_strategy(
        sig_static, "engine_b", cfg, score_group_resolver=lambda _s: "forex_majors"
    )
    assert mode == "traditional_static"
    assert sig_static["runner_directive"] == "runner_to_tp2"

    # Non-Engine-B rows are untouched (Engine A keeps its own exit_mode path).
    sig_a = {"engine_b_exit_strategy": "scale_out_structural_tp1_fallback_tp2"}
    assert (
        apply_engine_b_exit_strategy(
            sig_a, "engine_a", cfg, score_group_resolver=lambda _s: "crypto_btc"
        )
        is None
    )
    assert "exit_mode" not in sig_a
    assert "runner_directive" not in sig_a

    # Invalid exit strategy: mode still resolves but no runner plan is invented.
    sig_bad = {"engine": "engine_b", "engine_b_exit_strategy": "invalid_levels"}
    apply_engine_b_exit_strategy(
        sig_bad, "engine_b", cfg, score_group_resolver=lambda _s: "forex_majors"
    )
    assert sig_bad["runner_directive"] == "runner_none"


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
