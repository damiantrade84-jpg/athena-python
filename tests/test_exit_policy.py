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
