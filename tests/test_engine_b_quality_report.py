from athena_research.engine_b_quality_report import annotate_engine_b_trade, build_engine_b_quality_rows


def _trade(score: float, max_score: float, **overrides):
    row = {
        "score": score,
        "max_possible": max_score,
        "min_score_used": 4.0,
        "resultR": 1.0,
        "signalTier": "trade",
        "engine": "engine_b",
    }
    row.update(overrides)
    return annotate_engine_b_trade(row)


def test_four_of_eight_has_half_ratio_and_passes_default_conviction():
    row = _trade(4.0, 8.0)

    assert row["engine_b_score_ratio"] == 0.5
    assert row["engine_b_candidate_passed"] is True
    assert row["executionConvictionBase"] == 0.5
    assert row["autoMinConviction"] == 0.5
    assert row["engine_b_default_conviction_passed"] is True


def test_four_of_nine_fails_default_conviction_unless_aligned():
    unaligned = _trade(4.0, 9.0)
    aligned = _trade(4.0, 9.0, enginesAligned=True)

    assert unaligned["engine_b_score_ratio"] == 0.444444
    assert unaligned["engine_b_default_conviction_passed"] is False
    assert aligned["autoMinConviction"] == 0.425
    assert aligned["engine_b_default_conviction_passed"] is True


def test_manual_and_strict_quality_boundaries_are_inclusive():
    absolute = _trade(6.5, 9.0)
    manual_ratio = _trade(6.0, 8.0)
    strict_ratio = _trade(6.4, 8.0)

    assert absolute["engine_b_manual_quality_passed_score_6_5"] is True
    assert manual_ratio["engine_b_manual_quality_passed_ratio_0_75"] is True
    assert strict_ratio["engine_b_autotrade_quality_passed_ratio_0_80"] is True


def test_b_only_watchlist_is_not_scheduler_reachable():
    row = _trade(
        6.5,
        8.0,
        signalTier="watchlist",
        engine_b_confidence_passed=True,
        engine_b_execution_block_reason="engine_b_only_scan_watchlist",
        signalTierReason="Engine B-only watchlist",
    )

    assert row["autotradeIngressEligible"] is False
    assert row["b_only_watchlist_reason"] == "Engine B-only watchlist"


def test_quality_report_caps_small_sample_sqn_for_readability():
    trades = [
        _trade(7.0, 8.0, pair="EUR/USD", direction="SHORT", resultR=-1.08),
        _trade(7.0, 8.0, pair="EUR/USD", direction="SHORT", resultR=-1.10),
    ]

    _, rows = build_engine_b_quality_rows(trades)
    strict = next(row for row in rows if row["quality_layer"] == "strict_autotrade_quality_0_80")
    assert strict["SQN"] == -10.0
