"""Shared style resolution regressions for Engine A/B surfaces."""

from style_resolver import normalize_style, resolve_auto_style


def test_explicit_style_is_never_rewritten_by_pair_group() -> None:
    pair = {"type": "forex", "score_group": "forex_exotics"}
    assert resolve_auto_style("intraday", pair) == "intraday"
    assert resolve_auto_style("swing", pair) == "swing"
    assert resolve_auto_style("scalp", pair) == "scalp"


def test_auto_uses_existing_pair_class_policy() -> None:
    assert resolve_auto_style("auto", {"type": "forex"}, score_group="forex_majors") == "intraday"
    assert resolve_auto_style("auto", {"type": "crypto"}, score_group="crypto_btc") == "intraday"
    assert resolve_auto_style("auto", {"type": "forex"}, score_group="forex_exotics") == "swing"
    assert resolve_auto_style("auto", {"type": "crypto"}, score_group="crypto_other") == "swing"
    # Session assets use the intraday role ladder under auto (not swing D1).
    assert resolve_auto_style("auto", {"type": "stock"}, score_group="us_stock_single") == "intraday"
    assert resolve_auto_style("auto", {"type": "index"}, score_group="us_indices_trackers") == "intraday"
    assert resolve_auto_style("auto", {"type": "commodity"}, score_group="precious_trackers") == "intraday"
    # Bonds / energy stay swing under auto.
    assert resolve_auto_style("auto", {"type": "stock"}, score_group="bond_tlt") == "swing"
    assert resolve_auto_style("auto", {"type": "commodity"}, score_group="energy_oil") == "swing"


def test_invalid_style_falls_back_to_auto_policy() -> None:
    assert normalize_style("position") == "auto"
    assert resolve_auto_style("position", {"type": "forex"}, score_group="forex_majors") == "intraday"
