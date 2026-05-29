import exit_mode_apply as ema
import exit_policy as ep


# ---- pip_size (pure) --------------------------------------------------------

def test_pip_size_fx_5digit_uses_ten_points():
    assert ema.pip_size({"point": 0.00001, "digits": 5}, "forex") == 0.0001


def test_pip_size_fx_3digit_uses_ten_points():
    assert ema.pip_size({"point": 0.001, "digits": 3}, "forex") == 0.01


def test_pip_size_non_fx_uses_point():
    assert ema.pip_size({"point": 0.1, "digits": 1}, "index") == 0.1


def test_pip_size_missing_returns_zero():
    assert ema.pip_size({}, "forex") == 0.0
    assert ema.pip_size({"point": 0}, "forex") == 0.0


# ---- apply_engine_a_exit_mode ----------------------------------------------

def _grp(_pair):
    return "forex_majors"


def test_non_engine_a_is_noop():
    sig = {"pair": "EUR/USD", "direction": "LONG", "sl": 1.0, "tp1": 1.02}
    out = ema.apply_engine_a_exit_mode(sig, "engine_b", {}, {}, score_group_resolver=_grp)
    assert out is None
    assert "exit_mode" not in sig


def test_engine_a_stamps_global_default_when_no_group_or_override():
    sig = {"pair": "EUR/USD", "direction": "LONG", "price": 1.0, "sl": 0.99, "tp1": 1.02, "type": "forex"}
    cfg = {"ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT": "traditional_static"}
    out = ema.apply_engine_a_exit_mode(
        sig, "engine_a", {"point": 0.0001, "digits": 4}, cfg, score_group_resolver=_grp
    )
    assert out == ep.EXIT_MODE_STATIC
    assert sig["exit_mode"] == ep.EXIT_MODE_STATIC


def test_per_trade_override_beats_group_default():
    sig = {"pair": "EUR/USD", "direction": "LONG", "price": 1.0, "sl": 0.99, "tp1": 1.02, "type": "forex"}
    cfg = {
        "ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT": "traditional_static",
        "ENGINE_A_EXIT_MODE_BY_SCORE_GROUP": {"forex_majors": "adaptive_trail"},
    }
    out = ema.apply_engine_a_exit_mode(
        sig, "engine_a", {"point": 0.0001, "digits": 4}, cfg,
        level_override={"exit_mode": "manual"}, score_group_resolver=_grp,
    )
    assert out == ep.EXIT_MODE_MANUAL


def test_advisable_pip_clamp_applied_for_non_manual():
    # entry 1.0, sl 0.9995 -> 0.0005 dist; pip_size 0.0001 -> 5 pips.
    # min_pip 10 -> 0.0010 -> SL widens to 0.9990.
    sig = {"pair": "EUR/USD", "direction": "LONG", "price": 1.0, "sl": 0.9995, "tp1": 1.0010, "tp2": 1.0015, "type": "forex"}
    cfg = {
        "ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT": "traditional_static",
        "ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP": {"forex_majors": {"min_pip": 10}},
    }
    out = ema.apply_engine_a_exit_mode(
        sig, "engine_a", {"point": 0.0001, "digits": 4}, cfg, score_group_resolver=_grp
    )
    assert out == ep.EXIT_MODE_STATIC
    assert abs(sig["sl"] - 0.9990) < 1e-9          # widened to min band
    assert abs(sig["tp1"] - 1.0020) < 1e-9         # rr1 (=2.0) preserved


def test_manual_mode_is_not_clamped():
    sig = {"pair": "EUR/USD", "direction": "LONG", "price": 1.0, "sl": 0.9995, "tp1": 1.0010, "tp2": 1.0015, "type": "forex"}
    cfg = {
        "ENGINE_A_EXIT_MODE_GLOBAL_DEFAULT": "manual",
        "ENGINE_A_ADVISABLE_PIP_BY_SCORE_GROUP": {"forex_majors": {"min_pip": 10}},
    }
    out = ema.apply_engine_a_exit_mode(
        sig, "engine_a", {"point": 0.0001, "digits": 4}, cfg, score_group_resolver=_grp
    )
    assert out == ep.EXIT_MODE_MANUAL
    assert sig["sl"] == 0.9995  # untouched
