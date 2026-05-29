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
