from engine_c import (
    ENGINE_C_AB_WEIGHTS,
    _blend_consensus_weights,
    _engine_c_meta_blend,
    compute_consensus,
    normalise_engine_a,
    normalise_engine_b,
)


def _engine_b_signal(verdict="CLEAR", direction="LONG"):
    return {
        "structural_verdict": verdict,
        "direction": direction,
        "recommended_stop_loss": 1.0900,
        "recommended_take_profit": 1.1300,
        "bos_confirmed": True,
        "bos_mtf_confirmed": True,
        "ob_at_zone": True,
        "order_blocks": [{"strength": 80}],
        "current_swing_sequence": "HH-HL",
        "macro_swing_sequence": "HH-HL",
    }


def _engine_b_confidence(*, passed=True):
    return {
        "score": 5.0,
        "max_possible": 5.0,
        "passed": passed,
        "rr": 2.0,
        "structure_ok": True,
        "zone_ok": True,
        "trigger_ok": True,
    }


def _engine_a_signal(score=0.0, direction="LONG"):
    return {
        "confluenceScore": score,
        "maxScore": 3.0,
        "direction": direction,
        "confidenceDetail": {"confidence": 0.8},
        "sl": 1.0900,
        "tp1": 1.1300,
        "regime": {"label": "TRENDING"},
    }


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
    # Delta = mb * (1 - base["A"]) — proportional to how far base["A"] is from 1.0.
    # Use dynamic bound so the assertion stays valid when ENGINE_C_AB_WEIGHTS is retuned.
    max_delta = round(mb * (1.0 - base["A"]), 4) + 0.01
    assert abs(strongly_favor_a["A"] - base["A"]) <= max_delta


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

    After Phase 2B fix: no forex-specific 1.5x rescale. All asset classes use
    unified 0-3.0 scale (Engine A v2): norm = score / max_score.
    """
    # Forex signal with max_score 3.0 (Engine A v2 unified scale)
    signal_forex = {
        "confluenceScore": 0.95,
        "maxScore": 3.0,
        "direction": "LONG",
    }
    norm = normalise_engine_a(signal_forex)

    # Raw norm = 0.95/3.0 ≈ 0.3167 (no rescale)
    # Floor = 0.30, so has_signal = True
    assert round(norm["score_norm"], 4) == 0.3167
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
        "maxScore": 3.0,
        "direction": "LONG",
    }
    norm3 = normalise_engine_a(signal_forex_low)

    # Raw norm = 0.5/3.0 ≈ 0.1667 (no rescale)
    # Floor = 0.30, so has_signal = False
    assert round(norm3["score_norm"], 4) == 0.1667
    assert norm3["has_signal"] is False


def test_normalise_engine_b_clear_passed_high_score_has_signal():
    norm = normalise_engine_b(
        _engine_b_signal(),
        _engine_b_confidence(passed=True),
    )

    assert norm["has_signal"] is True
    assert norm["passed"] is True
    assert norm["signal_diagnostic"] == ""


def test_normalise_engine_b_clear_failed_checklist_has_no_signal():
    norm = normalise_engine_b(
        _engine_b_signal(),
        _engine_b_confidence(passed=False),
    )

    assert norm["score_norm"] == 1.0
    assert norm["has_signal"] is False
    assert norm["passed"] is False
    assert norm["signal_diagnostic"] == "engine_b_checklist_failed"


def test_normalise_engine_b_clear_missing_passed_has_no_signal():
    confidence = _engine_b_confidence(passed=True)
    confidence.pop("passed")

    norm = normalise_engine_b(_engine_b_signal(), confidence)

    assert norm["score_norm"] == 1.0
    assert norm["has_signal"] is False
    assert norm["passed"] is False
    assert norm["signal_diagnostic"] == "engine_b_checklist_missing_passed"


def test_normalise_engine_b_error_verdict_has_no_signal_even_if_passed():
    norm = normalise_engine_b(
        _engine_b_signal(verdict="ERROR"),
        _engine_b_confidence(passed=True),
    )

    assert norm["has_signal"] is False
    assert norm["passed"] is True
    assert norm["signal_diagnostic"] == "engine_b_verdict_not_clear"


def test_b_only_consensus_does_not_execute_when_engine_b_checklist_failed():
    result = compute_consensus(
        signal_a=_engine_a_signal(score=0.1),
        signal_b=_engine_b_signal(),
        confidence_b=_engine_b_confidence(passed=False),
        regime="TRENDING",
        entry_price=1.1000,
        atr=0.0020,
        asset_type="forex",
    )

    assert result["trade"] is False
    assert result["verdict"] == "NO_SIGNAL"
    assert result["components"]["b_has_signal"] is False
    assert result["components"]["b_checklist_passed"] is False
    assert result["components"]["b_signal_diagnostic"] == "engine_b_checklist_failed"


def test_aligned_consensus_does_not_use_engine_b_when_checklist_failed():
    result = compute_consensus(
        signal_a=_engine_a_signal(score=2.4),
        signal_b=_engine_b_signal(),
        confidence_b=_engine_b_confidence(passed=False),
        regime="TRENDING",
        entry_price=1.1000,
        atr=0.0020,
        asset_type="forex",
    )

    assert result["verdict"] != "ALIGNED"
    assert result["components"]["a_has_signal"] is True
    assert result["components"]["b_has_signal"] is False
    assert result["components"]["b_checklist_passed"] is False
