import exit_policy as ep


def test_mode_constants_are_distinct_and_known():
    modes = {
        ep.EXIT_MODE_STATIC,
        ep.EXIT_MODE_ADAPTIVE,
        ep.EXIT_MODE_MANUAL,
        ep.EXIT_MODE_TIME,
    }
    assert len(modes) == 4
    assert modes == set(ep.VALID_EXIT_MODES)
    assert ep.DEFAULT_EXIT_MODE == ep.EXIT_MODE_STATIC


def test_normalize_mode_accepts_known_with_case_and_whitespace():
    assert ep.normalize_mode("  Traditional_Static ") == ep.EXIT_MODE_STATIC
    assert ep.normalize_mode("ADAPTIVE_TRAIL") == ep.EXIT_MODE_ADAPTIVE


def test_normalize_mode_rejects_unknown_and_empty():
    assert ep.normalize_mode("nonsense") is None
    assert ep.normalize_mode("") is None
    assert ep.normalize_mode(None) is None


def test_group_default_for_reads_map_and_normalizes():
    gmap = {"forex_majors": "ADAPTIVE_TRAIL", "crypto_majors": "bogus"}
    assert ep.group_default_for("forex_majors", gmap) == ep.EXIT_MODE_ADAPTIVE
    assert ep.group_default_for("crypto_majors", gmap) is None  # invalid value
    assert ep.group_default_for("missing", gmap) is None
    assert ep.group_default_for(None, gmap) is None
    assert ep.group_default_for("forex_majors", None) is None


def test_resolve_precedence_per_trade_wins():
    assert (
        ep.resolve_exit_mode(
            per_trade="manual",
            group_default="adaptive_trail",
            global_default="traditional_static",
        )
        == ep.EXIT_MODE_MANUAL
    )


def test_resolve_falls_through_invalid_to_group_then_global():
    # invalid per-trade -> use group
    assert (
        ep.resolve_exit_mode(per_trade="junk", group_default="time_based")
        == ep.EXIT_MODE_TIME
    )
    # invalid per-trade and group -> use global
    assert (
        ep.resolve_exit_mode(
            per_trade="junk", group_default="junk", global_default="adaptive_trail"
        )
        == ep.EXIT_MODE_ADAPTIVE
    )
    # nothing valid anywhere -> DEFAULT_EXIT_MODE
    assert (
        ep.resolve_exit_mode(per_trade="junk", group_default="junk", global_default="junk")
        == ep.DEFAULT_EXIT_MODE
    )


def test_resolve_defaults_to_static_when_all_none():
    assert ep.resolve_exit_mode() == ep.EXIT_MODE_STATIC


import math


def _close(a, b, tol=1e-9):
    return math.isclose(a, b, rel_tol=0, abs_tol=tol)


def test_clamp_noop_when_no_bounds():
    r = ep.clamp_to_advisable_pip("LONG", 100.0, 99.0, 102.0, 103.0, pip_size=0.01)
    assert r["clamped"] is False
    assert (r["sl"], r["tp1"], r["tp2"]) == (99.0, 102.0, 103.0)


def test_clamp_noop_when_pip_size_nonpositive():
    r = ep.clamp_to_advisable_pip("LONG", 100.0, 99.0, 102.0, 103.0, pip_size=0.0, min_pip=200)
    assert r["clamped"] is False


def test_clamp_min_widens_long_and_preserves_rr():
    # entry 100, sl 99.5 -> sl_dist 0.5; pip_size 0.01 -> 50 pips.
    # min_pip 100 -> min_dist 1.0 -> SL widens to 99.0.
    # original rr1 = (102-100)/0.5 = 4.0 -> new tp1 = 100 + 4*1.0 = 104.0
    r = ep.clamp_to_advisable_pip("LONG", 100.0, 99.5, 102.0, 103.0, pip_size=0.01, min_pip=100)
    assert r["clamped"] is True
    assert _close(r["sl"], 99.0)
    assert _close(r["tp1"], 104.0)        # rr1 4.0 preserved
    assert _close(r["tp2"], 100.0 + 6.0)  # rr2 = (103-100)/0.5 = 6.0 -> 106.0


def test_clamp_max_tightens_short_and_preserves_rr():
    # SHORT entry 100, sl 103 -> sl_dist 3.0; pip_size 0.01 -> 300 pips.
    # max_pip 100 -> max_dist 1.0 -> SL tightens to 101.0.
    # rr1 = (100-98)/3.0 -> new tp1 = 100 - rr1*1.0
    rr1 = (100.0 - 98.0) / 3.0
    r = ep.clamp_to_advisable_pip("SHORT", 100.0, 103.0, 98.0, 97.0, pip_size=0.01, max_pip=100)
    assert r["clamped"] is True
    assert _close(r["sl"], 101.0)
    assert _close(r["tp1"], 100.0 - rr1 * 1.0)


def test_clamp_noop_when_within_band():
    # sl_dist 0.5 = 50 pips, band [10, 100] pips -> within -> no change.
    r = ep.clamp_to_advisable_pip("LONG", 100.0, 99.5, 102.0, 103.0, pip_size=0.01, min_pip=10, max_pip=100)
    assert r["clamped"] is False


def test_clamp_noop_when_sl_dist_zero():
    r = ep.clamp_to_advisable_pip("LONG", 100.0, 100.0, 102.0, 103.0, pip_size=0.01, min_pip=10)
    assert r["clamped"] is False
