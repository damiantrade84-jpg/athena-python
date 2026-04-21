from engine_c import (
    ENGINE_C_AB_WEIGHTS,
    _blend_consensus_weights,
    _engine_c_meta_blend,
    compute_consensus,
    normalise_engine_a,
)


def test_blended_ab_weights_fall_back_to_base_and_move_within_bound():
    base = ENGINE_C_AB_WEIGHTS["TRENDING"]

    no_data = _blend_consensus_weights(base, None)
    assert no_data == base

    strongly_favor_a = _blend_consensus_weights(
        base,
        {
            "engine_a": 1.0,
            "engine_b": 0.0,
            "engine_c": 0.0,
            "scalp": 0.0,
        },
    )

    mb = _engine_c_meta_blend()
    expected_a = round((base["A"] * (1.0 - mb)) + mb, 4)
    expected_b = round(base["B"] * (1.0 - mb), 4)

    assert strongly_favor_a["A"] == expected_a
    assert strongly_favor_a["B"] == expected_b
    assert strongly_favor_a["A"] > base["A"]
    assert strongly_favor_a["B"] < base["B"]
    assert abs(strongly_favor_a["A"] - base["A"]) <= 0.08


def test_a_only_watchlist_preserves_watchlist_tier():
    """Regression test: A_ONLY watchlist must preserve WATCHLIST tier, not collapse to SKIP.

    A_ONLY conviction = score_norm * 0.6. For watchlist: conviction >= 0.40.
    Need score_norm >= 0.667, so confluenceScore/maxScore >= 0.667.
    Using confluenceScore=1.5, maxScore=2.0 → score_norm=0.75 → conviction=0.45.
    """
    signal_a = {
        "confluenceScore": 1.5,
        "maxScore": 2.0,
        "direction": "LONG",
        "confidenceDetail": {"confidence": 0.5},
        "sl": 1.0900,
        "tp1": 1.1100,
        "regime": {"label": "TRENDING"},
    }
    signal_b = {}
    confidence_b = {"score": 0, "max_possible": 5.0, "passed": False}

    result = compute_consensus(
        signal_a=signal_a,
        signal_b=signal_b,
        confidence_b=confidence_b,
        regime="TRENDING",
        entry_price=1.1000,
        atr=0.0020,
        asset_type="forex",
    )

    # A_ONLY with conviction ~0.45 should be watchlist (>= 0.40, < 0.50)
    assert result["verdict"] == "A_ONLY"
    assert result["trade"] is False
    assert result["sizing_override"] == 0.0
    assert result["decision_state"] == "watchlist"
    assert result["tier"] == "WATCHLIST", "watchlist must not collapse to SKIP"
    assert result["signalTier"] == "watchlist"
    assert result["watchlistReason"] != ""


def test_blocked_remains_skip():
    """Blocked decision_state must remain SKIP tier."""
    signal_a = {
        "confluenceScore": 0.3,
        "maxScore": 2.0,
        "direction": "LONG",
        "confidenceDetail": {"confidence": 0.2},
        "sl": 1.0900,
        "tp1": 1.1100,
        "regime": {"label": "RANGING"},
    }
    signal_b = {}
    confidence_b = {"score": 0, "max_possible": 5.0, "passed": False}

    result = compute_consensus(
        signal_a=signal_a,
        signal_b=signal_b,
        confidence_b=confidence_b,
        regime="RANGING",
        entry_price=1.1000,
        atr=0.0020,
        asset_type="forex",
    )

    # Very low conviction should be blocked/SKIP
    assert result["trade"] is False
    assert result["tier"] == "SKIP"
    assert result["sizing_override"] == 0.0


def test_normalise_engine_a_uses_raw_ratio_no_forex_rescale():
    """Verify normalise_engine_a() uses raw bounded ratio without forex rescale.

    After Phase 2B fix: no forex-specific 1.5x rescale. Both forex (0-2.0) and
    non-forex (0-3.0) use the same raw bounded ratio: norm = score / max_score.
    """
    # Forex signal with max_score 2.0
    signal_forex = {
        "confluenceScore": 0.95,
        "maxScore": 2.0,
        "direction": "LONG",
    }
    norm = normalise_engine_a(signal_forex)

    # Raw norm = 0.95/2.0 = 0.475 (no rescale)
    # Floor = 0.30, so has_signal = True
    assert norm["score_norm"] == 0.475
    assert norm["has_signal"] is True

    # Non-forex signal with max_score 3.0
    signal_non_forex = {
        "confluenceScore": 1.0,
        "maxScore": 3.0,
        "direction": "LONG",
    }
    norm2 = normalise_engine_a(signal_non_forex)

    # Raw norm = 1.0/3.0 = 0.333...
    # Floor = 0.30, so has_signal = True
    assert round(norm2["score_norm"], 4) == 0.3333
    assert norm2["has_signal"] is True

    # Forex signal below floor (no rescale)
    signal_forex_low = {
        "confluenceScore": 0.5,
        "maxScore": 2.0,
        "direction": "LONG",
    }
    norm3 = normalise_engine_a(signal_forex_low)

    # Raw norm = 0.5/2.0 = 0.25 (no rescale)
    # Floor = 0.30, so has_signal = False
    assert norm3["score_norm"] == 0.25
    assert norm3["has_signal"] is False
