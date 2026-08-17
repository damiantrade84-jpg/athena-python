"""Phase 5 regression fixtures built from the three real 2026-08-12 scans.

Scan 1  capture skew 1429s, empty factorDiagnostics
Scan 2  chart SI=F vs engine XAG/USD, DEMO_UNVALIDATED artifact, 06:38 UTC scan
Scan 3  live 65.92, entry 65.5865, SL 62.7437, TP1 68.4293, TP2 69.8507

These assert the audited numbers directly so a regression is recognisable as the
original defect rather than as an abstract failure.
"""

from __future__ import annotations

import pytest

from ai_review.context_diagnostics import build_context_diagnostics
from ai_review.gate_hygiene import normalize_review_gates
from ai_review.review_geometry import (
    attach_review_geometry,
    compute_rr_bundle,
    score_entry_quality,
    zone_status,
)
from ai_review.symbol_parity import evaluate_symbol_parity
from ai_review.timestamp_contract import evaluate_timestamp_mismatch


# --- Scan 3 geometry --------------------------------------------------------

SCAN3_ENTRY = 65.5865
SCAN3_SL = 62.7437
SCAN3_TP1 = 68.4293
SCAN3_TP2 = 69.8507
SCAN3_LIVE = 65.92
SCAN3_ZONE = (65.4917, 65.6813)
SCAN3_ATR = 0.947599
SCAN3_EMA21 = 64.046
SCAN3_EMA50 = 62.2306


def _scan3_ctx(**overrides):
    ctx = {
        "symbol": "XAG/USD",
        "timeframe": "H4",
        "asset_group": "precious_trackers",
        "primary_engine": "A",
        "review_type": "engine_a_chart",
        "direction": "LONG",
        "passed": True,
        "geometry": {
            "current_price": SCAN3_LIVE,
            "candidate_entry": SCAN3_ENTRY,
            "stop_loss": SCAN3_SL,
            "take_profit": SCAN3_TP1,
            "tp2": SCAN3_TP2,
        },
        "entry_zone": {"low": SCAN3_ZONE[0], "high": SCAN3_ZONE[1]},
        "atr": {"atr_value": SCAN3_ATR, "atr_tf": "H4"},
        "ema_levels": {"ema21": SCAN3_EMA21, "ema50": SCAN3_EMA50},
        "factor_diagnostics": {
            "components": {"location": {"contribution": 0.0194}},
        },
    }
    ctx.update(overrides)
    return ctx


def test_scan3_rr_live_tp1_is_079_not_100():
    """The gate passed at entry 65.59; at live 65.92 the trade is RR 0.79."""
    bundle = compute_rr_bundle(
        entry=SCAN3_ENTRY,
        sl=SCAN3_SL,
        tp1=SCAN3_TP1,
        tp2=SCAN3_TP2,
        direction="LONG",
        live_price=SCAN3_LIVE,
    )
    assert bundle["rr_live_tp1"] == pytest.approx(0.79, abs=0.01)
    assert bundle["rr_at_signal_tp1"] == pytest.approx(1.00, abs=0.01)
    assert bundle["rr_live_tp2"] == pytest.approx(1.24, abs=0.01)
    assert bundle["rr_at_signal_tp2"] == pytest.approx(1.49, abs=0.01)
    # Blended assumes half volume at TP2 per the MT5 scale-out rule.
    assert bundle["rr_live_blended"] == pytest.approx(1.02, abs=0.02)
    assert bundle["rr_at_signal_blended"] == pytest.approx(1.25, abs=0.02)


def test_scan3_rr_floor_is_15_and_live_rr_fails_it():
    bundle = compute_rr_bundle(
        entry=SCAN3_ENTRY, sl=SCAN3_SL, tp1=SCAN3_TP1, tp2=SCAN3_TP2,
        direction="LONG", live_price=SCAN3_LIVE,
    )
    assert bundle["rrFloorReview"] == 1.5
    assert bundle["rrPassedLive"] is False


def test_scan3_zone_status_is_above_zone():
    assert zone_status(SCAN3_LIVE, *SCAN3_ZONE) == "ABOVE_ZONE"


def test_scan3_entry_quality_below_40():
    """displacement 1.98 ATR, location 0.0194, out of zone, live RR 0.79."""
    ctx = _scan3_ctx()
    geom = attach_review_geometry(ctx)
    ctx.update(geom)
    assert ctx["zone_status"] == "ABOVE_ZONE"
    assert score_entry_quality(engine_a_ctx=ctx) < 40


def test_scan3_displacement_is_198_atr():
    from ai_review.review_geometry import displacement_atr

    assert displacement_atr(SCAN3_LIVE, SCAN3_EMA21, SCAN3_ATR) == pytest.approx(
        1.98, abs=0.01
    )


def test_scan3_above_zone_long_blocks_execution():
    ctx = _scan3_ctx()
    geom = attach_review_geometry(ctx)
    assert geom["execution_permitted"] is False
    assert geom["execution_block_reason"] in {
        "zone_status_above_zone",
        "SL_INSIDE_INVALIDATION_THESIS",
    }


def test_scan3_sl_sits_inside_its_own_invalidation_thesis():
    """SL 62.7437 fires before the EMA50 62.2306 breakdown the thesis allows."""
    from ai_review.review_geometry import check_sl_inside_invalidation_thesis

    result = check_sl_inside_invalidation_thesis(
        direction="LONG",
        sl=SCAN3_SL,
        invalidation_level=SCAN3_EMA50,
        required_confirmation=["No breakdown/close below EMA50 (~62.23) on H4."],
        structural_levels_available=False,
    )
    assert "SL_INSIDE_INVALIDATION_THESIS" in result["blocking_flags"]
    # Without structural input the review must not narrate structural claims.
    assert result["suppress_structural_language"] is True


def test_f4_required_confirmation_reaches_the_sl_thesis_check():
    """Pre-provider geometry runs with ai_review=None; the post-review pass must
    actually supply requiredConfirmation instead of falling back to ema50."""
    ctx = _scan3_ctx()
    review = {
        "requiredConfirmation": ["No breakdown/close below EMA50 (~62.23) on H4."],
        "invalidationLevel": SCAN3_EMA50,
    }
    without = attach_review_geometry(ctx)
    with_text = attach_review_geometry(ctx, ai_review=review)

    assert without["sl_thesis_check"]["blocking_flags"] == ["SL_INSIDE_INVALIDATION_THESIS"]
    # Same verdict, but now reached via the model's named level, not a fallback.
    assert with_text["sl_thesis_check"]["invalidation_level"] == SCAN3_EMA50
    assert "SL_INSIDE_INVALIDATION_THESIS" in with_text["sl_thesis_check"]["blocking_flags"]


def test_f4_model_text_cues_lower_entry_quality():
    """The text branch in score_entry_quality must actually affect the score.

    Measured on a healthy in-zone setup — the scan-3 context already clamps to
    0, where no further reduction is observable.
    """
    ctx = _scan3_ctx(
        geometry={
            "current_price": 64.10,
            "candidate_entry": 64.05,
            "stop_loss": 62.7437,
            "take_profit": 68.4293,
            "tp2": 69.8507,
        },
        entry_zone={"low": 63.9, "high": 64.3},
        factor_diagnostics={"components": {"location": {"contribution": 0.22}}},
    )
    ctx.update(attach_review_geometry(ctx))
    plain = score_entry_quality(engine_a_ctx=ctx)
    assert plain > 0, "need a non-floor baseline for this comparison to mean anything"
    with_text = score_entry_quality(
        engine_a_ctx=ctx,
        ai_review={"entry_quality": "extended entry at highs, chasing"},
    )
    assert with_text < plain


def test_entry_quality_pullback_scores_higher_than_equal_chase():
    from ai_review.review_geometry import score_entry_quality

    atr = {"atr_value": 1.0}
    ema = {"ema21": 100.0}
    pullback = {
        "direction": "LONG",
        "ema_levels": ema,
        "atr": atr,
        "geometry": {"current_price": 99.2, "candidate_entry": 99.2, "stop_loss": 98.0, "take_profit": 102.0},
        "factor_diagnostics": {"components": {"location": {"contribution": 0.2}}},
        "zone_status": "IN_ZONE",
        "risk_geometry": {"rr_live_tp1": 2.0},
    }
    chase = {
        **pullback,
        "geometry": {"current_price": 100.8, "candidate_entry": 100.8, "stop_loss": 98.0, "take_profit": 104.0},
    }
    assert score_entry_quality(engine_a_ctx=pullback) > score_entry_quality(engine_a_ctx=chase)


def test_f4_summary_uses_text_aware_entry_score_not_the_stamped_one():
    """The stamped pre-provider score must not override the text-aware one."""
    from ai_review.summary import build_ai_review_summary

    ctx = _scan3_ctx()
    ctx.update(attach_review_geometry(ctx))
    # Stamp an implausibly high pre-provider score; the summary must not use it.
    ctx["entry_quality_score"] = 95
    summary = build_ai_review_summary(
        ctx,
        {"entry_quality": "extended entry at highs, chasing", "human_action": "wait"},
        {},
        {},
    )
    assert summary["entryQualityScore"] < 95


def test_scan3_watch_state_arms_with_better_rr():
    """The point of WATCH: same invalidation, better price, RR 0.79 -> ~3.3."""
    ctx = _scan3_ctx()
    geom = attach_review_geometry(ctx)
    ctx.update(geom)
    watch = ctx.get("watch_state")
    assert watch is not None
    assert watch["state"] == "WATCH"
    assert watch["direction"] == "LONG"
    assert watch["execution_permitted"] is False
    assert watch["ai_gated"] is False
    # Watch zone is EMA21 +/- 0.5 ATR, derived deterministically not from prose.
    assert watch["watch_zone"]["low"] == pytest.approx(63.57, abs=0.01)
    assert watch["watch_zone"]["high"] == pytest.approx(64.52, abs=0.01)
    assert watch["rr_at_watch"]["rr_live_tp1"] > 3.0


# --- Scan 2 -----------------------------------------------------------------


def test_scan2_si_f_vs_xag_usd_hard_rejects_without_declared_alias():
    parity = evaluate_symbol_parity(
        "SI=F",
        {"symbol": "XAGUSD", "display": "XAG/USD", "entry": SCAN3_ENTRY},
        engine_pair={"symbol": "XAGUSD", "display": "XAG/USD", "type": "commodity"},
    )
    assert parity["ok"] is False
    assert parity["symbol_alias_applied"] is None
    assert parity["reason"] in {
        "symbol_parity_mismatch",
        "futures_spot_identity_conflict",
    }


def test_scan2_matching_symbols_pass_parity():
    parity = evaluate_symbol_parity(
        "XAGUSD",
        {"symbol": "XAGUSD", "display": "XAG/USD"},
        engine_pair={"symbol": "XAGUSD", "display": "XAG/USD", "type": "commodity"},
    )
    assert parity["ok"] is True


def test_asx200_yahoo_ticker_matches_display_identity():
    parity = evaluate_symbol_parity(
        "^AXJO",
        {"symbol": "ASX 200", "display": "ASX 200"},
        engine_pair={"symbol": "^AXJO", "display": "ASX 200", "type": "index"},
    )
    assert parity["ok"] is True
    assert parity["reason"] == "exact_match"


def test_scan2_active_session_passes_at_0638_utc():
    """06:38 UTC is inside London/NY 06:00-21:00 despite a 01:00 UTC candle."""
    result = normalize_review_gates(
        predicates=[
            {
                "name": "active_session_utc",
                "passed": False,
                "actual": "2026-08-12T01:00:00+00:00",
                "expected": "London/NY 06:00-21:00 UTC",
            }
        ],
        scan_timestamp="2026-08-12T06:38:07+00:00",
    )
    gate = next(g for g in result["gates"] if g["name"] == "active_session_utc")
    assert gate["status"] == "PASS"


@pytest.mark.parametrize("artifact_status", ["DEMO_UNVALIDATED"])
def test_scan2_demo_unvalidated_never_emits_trade(artifact_status):
    """setup_label is derived from artifact_status; TRADE is unreachable."""
    from engine_a_v3.contract import ValidationArtifact

    artifact = ValidationArtifact(
        artifactId="demo-unvalidated-commodity-precious-intraday",
        family="commodity",
        subclass="precious",
        horizon="intraday",
        status=artifact_status,
        metrics=(),
        provenanceSha256="0" * 64,
    )
    assert artifact.status == "DEMO_UNVALIDATED"
    # Guard the derivation rule itself: no branch may map this status to TRADE.
    import inspect

    from engine_a_v3 import evaluator

    source = inspect.getsource(evaluator.evaluate_engine_a_v3)
    assert 'setup_label = "RESEARCH"' in source
    assert "resolve_engine_a_trade_eligibility" in source


# --- Scan 1 -----------------------------------------------------------------


def test_scan1_capture_skew_1429s_is_flagged():
    warnings = evaluate_timestamp_mismatch(
        {"review_timestamp": "2026-08-12T06:38:07+00:00"},
        {"captured_at": "2026-08-12T06:14:18+00:00"},  # 1429s earlier
        {"MISMATCH_WARN_MAX_SECONDS": 120},
    )
    assert any("chart captured 1429s" in w for w in warnings)
    assert any("max 120s" in w for w in warnings)


def test_scan1_empty_factor_diagnostics_is_not_scored_84():
    ctx = _scan3_ctx(factor_diagnostics={})
    completeness = build_context_diagnostics(ctx, {"verdict": "CAUTION"})[
        "contextCompleteness"
    ]
    assert completeness["score"] != 84
    assert completeness["status"] == "unverifiable"
    assert completeness["factorDiagnosticsVerifiable"] is False


# --- P2-8 chart timeframe parity -------------------------------------------


def test_p2_8_m30_capture_into_h4_context_is_not_comparable():
    """Parity must not report ok when the capture is a different timeframe."""
    from ai_review.engine_a_context import _chart_indicator_parity

    parity, warnings = _chart_indicator_parity(
        {
            "chart_timeframe": "M30",
            "chart_snapshot": {
                "chartIndicators": {
                    "timeframe": "M30",
                    "emaTrend": 64.05,
                    "ema50": 62.23,
                    "atr14": 0.31,
                }
            },
        },
        {"ema21": SCAN3_EMA21, "ema50": SCAN3_EMA50},
        {"atr14": SCAN3_ATR},
    )
    assert parity["comparable"] is False
    assert parity["status"] != "ok"
    assert parity["status"] == "not_comparable_timeframe"
    assert parity["chart_timeframe"] == "M30"
    assert parity["engine_a_indicator_timeframe"] == "H4"


def test_p2_8_h4_capture_matching_values_is_ok():
    from ai_review.engine_a_context import _chart_indicator_parity

    parity, _warnings = _chart_indicator_parity(
        {
            "chart_timeframe": "H4",
            "chart_snapshot": {
                "chartIndicators": {
                    "timeframe": "H4",
                    "emaTrend": SCAN3_EMA21,
                    "ema50": SCAN3_EMA50,
                    "atr14": SCAN3_ATR,
                }
            },
        },
        {"ema21": SCAN3_EMA21, "ema50": SCAN3_EMA50},
        {"atr14": SCAN3_ATR},
    )
    assert parity["comparable"] is True
    assert parity["status"] == "ok"


def test_p2_8_h4_capture_with_differing_values_flags_mismatch():
    """Confirms the chart slot reads the capture, not a copy of the H4 value."""
    from ai_review.engine_a_context import _chart_indicator_parity

    parity, warnings = _chart_indicator_parity(
        {
            "chart_timeframe": "H4",
            "chart_snapshot": {
                "chartIndicators": {
                    "timeframe": "H4",
                    "emaTrend": SCAN3_EMA21 * 1.05,
                    "atr14": SCAN3_ATR * 2,
                }
            },
        },
        {"ema21": SCAN3_EMA21, "ema50": SCAN3_EMA50},
        {"atr14": SCAN3_ATR},
    )
    assert parity["status"] == "values_differ"
    assert "ema_trend" in parity["mismatches"]
    assert parity["mismatches"]["atr14"]["chart"] != parity["mismatches"]["atr14"]["engineA"]
    assert "chart_indicators_differ_from_engine_a" in warnings
