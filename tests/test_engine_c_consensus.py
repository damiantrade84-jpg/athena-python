"""Tests for Engine C core consensus functions: resolve_sl, resolve_tp,
compute_consensus conflict/aligned paths, and B_ONLY_LEVELS_MISSING contract."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from engine_c import (
    compute_consensus,
    normalise_engine_a,
    normalise_engine_b,
    resolve_sl,
    resolve_tp,
    classify_conviction,
    CONVICTION_TIERS,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _engine_a_signal(score=0.0, direction="LONG", regime="TRENDING"):
    return {
        "confluenceScore": score,
        "maxScore": 3.0,
        "direction": direction,
        "confidenceDetail": {"confidence": 0.8},
        "sl": 1.0900,
        "tp1": 1.1300,
        "regime": {"label": regime},
    }


def _engine_b_signal(verdict="CLEAR", direction="LONG", sl=None, tp=None):
    sig = {
        "structural_verdict": verdict,
        "direction": direction,
        "recommended_stop_loss": sl or 1.0900,
        "recommended_take_profit": tp or 1.1300,
        "bos_confirmed": True,
        "bos_mtf_confirmed": True,
        "ob_at_zone": True,
        "order_blocks": [{"strength": 80}],
        "current_swing_sequence": "HH-HL",
        "macro_swing_sequence": "HH-HL",
    }
    return sig


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


# ── resolve_sl tests ─────────────────────────────────────────────────────────

class TestResolveSL:
    def test_no_entry_returns_no_entry(self):
        r = resolve_sl(0.0, 1.0, 1.1, "LONG", atr=0.01)
        assert r["sl"] is None
        assert r["method"] == "NO_ENTRY"

    def test_negative_entry_returns_no_entry(self):
        r = resolve_sl(-1.0, 1.0, 1.1, "LONG", atr=0.01)
        assert r["sl"] is None
        assert r["method"] == "NO_ENTRY"

    def test_structural_sl_within_atr_band(self):
        r = resolve_sl(100.0, None, 98.0, "LONG", atr=2.0)
        assert r["sl"] == 98.0
        assert r["method"] == "structural"

    def test_structural_sl_too_wide_gets_clamped(self):
        r = resolve_sl(100.0, None, 90.0, "LONG", atr=2.0)
        assert r["method"] == "structural_clamped"
        assert r["sl"] == 95.0

    def test_atr_sl_used_when_no_structural(self):
        r = resolve_sl(100.0, 98.5, None, "LONG", atr=2.0)
        assert r["sl"] == 98.5
        assert r["method"] == "atr"

    def test_picks_tighter_for_long(self):
        r = resolve_sl(100.0, 98.0, 97.0, "LONG", atr=5.0)
        assert r["sl"] == 98.0

    def test_picks_tighter_for_short(self):
        r = resolve_sl(100.0, 102.0, 103.0, "SHORT", atr=5.0)
        assert r["sl"] == 102.0

    def test_sl_at_entry_dropped_with_fallback(self):
        r = resolve_sl(100.0, None, 100.0, "LONG", atr=2.0)
        assert r["method"] == "atr_fallback"
        assert r["sl"] == 97.0

    def test_no_data_returns_none(self):
        r = resolve_sl(100.0, None, None, "LONG", atr=0.0)
        assert r["sl"] is None
        assert r["method"] == "NO_DATA"

    def test_structural_clamped_short(self):
        r = resolve_sl(100.0, None, 115.0, "SHORT", atr=2.0)
        assert r["method"] == "structural_clamped"
        assert r["sl"] == 105.0


# ── resolve_tp tests ─────────────────────────────────────────────────────────

class TestResolveTP:
    def test_no_entry_returns_no_data(self):
        r = resolve_tp(0.0, 98.0, 103.0, 105.0, "LONG")
        assert r["tp"] is None
        assert r["method"] == "NO_DATA"

    def test_no_sl_returns_no_data(self):
        r = resolve_tp(100.0, None, 103.0, 105.0, "LONG")
        assert r["tp"] is None
        assert r["method"] == "NO_DATA"

    def test_zero_sl_returns_zero_risk(self):
        r = resolve_tp(100.0, 100.0, 103.0, 105.0, "LONG")
        assert r["tp"] is None
        assert r["method"] == "ZERO_RISK"

    def test_structural_tp_with_good_rr(self):
        r = resolve_tp(100.0, 98.0, 103.0, 105.0, "LONG")
        assert r["tp"] == 105.0
        assert r["method"] == "structural"
        assert r["rr"] == 2.5

    def test_structural_tp_below_rr_threshold_falls_back(self):
        r = resolve_tp(100.0, 99.0, 103.0, 100.5, "LONG")
        assert r["tp"] == 103.0
        assert r["method"] == "atr"

    def test_no_tp_falls_back_to_2r(self):
        r = resolve_tp(100.0, 98.0, None, None, "LONG")
        assert r["tp"] == 104.0
        assert r["rr"] == 2.0
        assert r["method"] == "fallback_2R"

    def test_short_direction_2r_fallback(self):
        r = resolve_tp(100.0, 102.0, None, None, "SHORT")
        assert r["tp"] == 96.0
        assert r["rr"] == 2.0

    def test_sl_zero_treated_as_no_data(self):
        r = resolve_tp(100.0, 0.0, 103.0, 105.0, "LONG")
        assert r["tp"] is None
        assert r["method"] == "NO_DATA"


# ── classify_conviction tests ────────────────────────────────────────────────

class TestClassifyConviction:
    def test_high_tier(self):
        tier, sizing = classify_conviction(0.75)
        assert tier == "HIGH"
        assert sizing == 1.0

    def test_medium_tier(self):
        tier, sizing = classify_conviction(0.55)
        assert tier == "MEDIUM"
        assert sizing == 0.65

    def test_low_tier(self):
        tier, sizing = classify_conviction(0.40)
        assert tier == "LOW"
        assert sizing == 0.35

    def test_skip_tier(self):
        tier, sizing = classify_conviction(0.20)
        assert tier == "SKIP"
        assert sizing == 0.0

    def test_boundary_high(self):
        tier, sizing = classify_conviction(0.70)
        assert tier == "HIGH"

    def test_boundary_medium(self):
        tier, sizing = classify_conviction(0.50)
        assert tier == "MEDIUM"

    def test_boundary_low(self):
        tier, sizing = classify_conviction(0.35)
        assert tier == "LOW"


# ── compute_consensus: B_ONLY_LEVELS_MISSING contract ────────────────────────

class TestBOnlyLevelsMissing:
    def test_returns_full_contract(self):
        result = compute_consensus(
            {},
            {"direction": "LONG", "structural_verdict": "CLEAR"},
            confidence_b={
                "passed": True,
                "pct": 85,
                "score": 85,
                "max_possible": 100,
                "zone_ok": True,
                "trigger_ok": True,
                "structure_ok": True,
            },
            atr=0.0015,
            entry_price=1.1,
        )
        assert result["trade"] is False
        assert result["verdict"] == "B_ONLY_LEVELS_MISSING"
        assert "conviction" in result
        assert result["conviction"] == 0.0
        assert "entry" in result
        assert "sl" in result
        assert result["sl"] is None
        assert "tp" in result
        assert result["tp"] is None
        assert "rr" in result
        assert result["rr"] == 0.0
        assert "tier" in result
        assert result["tier"] == "SKIP"
        assert "direction" in result
        assert result["direction"] == "LONG"
        assert "components" in result
        assert "disagreement_diagnosis" in result
        assert "decision_state" in result
        assert result["decision_state"] == "blocked"


# ── compute_consensus: direction conflict paths ──────────────────────────────

class TestDirectionConflict:
    def test_opposing_high_confidence_blocks(self):
        result = compute_consensus(
            signal_a=_engine_a_signal(score=2.5, direction="LONG"),
            signal_b=_engine_b_signal(direction="SHORT"),
            confidence_b=_engine_b_confidence(passed=True),
            regime="TRENDING",
            entry_price=1.1000,
            atr=0.0020,
            asset_type="forex",
        )
        assert result["trade"] is False
        assert result["verdict"] in ("OPPOSING_HIGH_CONFIDENCE", "DIRECTION_CONFLICT")
        assert result["tier"] == "SKIP"

    def test_b_override_when_b_strong_a_weak(self):
        result = compute_consensus(
            signal_a=_engine_a_signal(score=1.0, direction="LONG"),
            signal_b=_engine_b_signal(direction="SHORT", sl=1.1100, tp=1.0700),
            confidence_b=_engine_b_confidence(passed=True),
            regime="TRENDING",
            entry_price=1.1000,
            atr=0.0020,
            asset_type="forex",
        )
        assert result["verdict"] == "B_OVERRIDE_CONFLICT"
        assert result["direction"] == "SHORT"

    def test_a_override_disabled_by_default(self):
        result = compute_consensus(
            signal_a=_engine_a_signal(score=2.5, direction="LONG"),
            signal_b=_engine_b_signal(direction="SHORT", sl=1.1100, tp=1.0700),
            confidence_b=_engine_b_confidence(passed=True),
            regime="TRENDING",
            entry_price=1.1000,
            atr=0.0020,
            asset_type="forex",
        )
        assert result["verdict"] != "A_OVERRIDE_CONFLICT"


# ── compute_consensus: aligned path ──────────────────────────────────────────

class TestAlignedConsensus:
    def test_both_engines_aligned_long(self):
        result = compute_consensus(
            signal_a=_engine_a_signal(score=2.0, direction="LONG"),
            signal_b=_engine_b_signal(direction="LONG"),
            confidence_b=_engine_b_confidence(passed=True),
            regime="TRENDING",
            entry_price=1.1000,
            atr=0.0020,
            asset_type="forex",
        )
        assert result["verdict"] == "ALIGNED"
        assert result["direction"] == "LONG"
        assert result["trade"] is True
        assert result["sl"] is not None
        assert result["tp"] is not None
        assert result["rr"] >= 1.0

    def test_both_engines_aligned_short(self):
        result = compute_consensus(
            signal_a=_engine_a_signal(score=2.0, direction="SHORT"),
            signal_b=_engine_b_signal(direction="SHORT", sl=1.1100, tp=1.0700),
            confidence_b=_engine_b_confidence(passed=True),
            regime="RANGING",
            entry_price=1.1000,
            atr=0.0020,
            asset_type="forex",
        )
        assert result["verdict"] == "ALIGNED"
        assert result["direction"] == "SHORT"

    def test_aligned_rr_too_low_blocks(self):
        sig_a = _engine_a_signal(score=2.0, direction="LONG")
        sig_a["tp1"] = 1.1005
        result = compute_consensus(
            signal_a=sig_a,
            signal_b=_engine_b_signal(direction="LONG", sl=1.0950, tp=1.1005),
            confidence_b=_engine_b_confidence(passed=True),
            regime="TRENDING",
            entry_price=1.1000,
            atr=0.0020,
            asset_type="forex",
        )
        assert result["tier"] == "SKIP"
        assert result["trade"] is False


# ── compute_consensus: A_ONLY path ───────────────────────────────────────────

class TestAOnlyConsensus:
    def test_a_only_with_b_floor_met(self):
        result = compute_consensus(
            signal_a=_engine_a_signal(score=2.0, direction="LONG"),
            signal_b={},
            confidence_b={"score": 0, "max_possible": 5.0, "passed": False},
            regime="TRENDING",
            entry_price=1.1000,
            atr=0.0020,
            asset_type="forex",
        )
        assert result["verdict"] == "A_ONLY"
        assert result["direction"] == "LONG"

    def test_a_only_low_score_blocked(self):
        result = compute_consensus(
            signal_a=_engine_a_signal(score=1.5, direction="LONG"),
            signal_b={},
            confidence_b={"score": 0, "max_possible": 5.0, "passed": False},
            regime="TRENDING",
            entry_price=1.1000,
            atr=0.0020,
            asset_type="forex",
        )
        assert result["verdict"] == "A_ONLY"
        assert result["tier"] == "SKIP"


# ── compute_consensus: no signal path ────────────────────────────────────────

class TestNoSignal:
    def test_neither_engine_produces_signal(self):
        result = compute_consensus(
            signal_a={},
            signal_b={},
            confidence_b={"score": 0, "max_possible": 5.0, "passed": False},
            regime="TRENDING",
            entry_price=1.1000,
            atr=0.0020,
            asset_type="forex",
        )
        assert result["verdict"] == "NO_SIGNAL"
        assert result["trade"] is False
        assert result["tier"] == "SKIP"
        assert "conviction" in result
        assert result["conviction"] == 0.0


# ── normalise_engine_a edge cases ────────────────────────────────────────────

class TestNormaliseEngineA:
    def test_missing_maxscore_uses_default(self):
        sig = {"confluenceScore": 1.5, "direction": "LONG"}
        out = normalise_engine_a(sig)
        assert out["max_score"] == 3.0
        assert out["score_norm"] == 0.5

    def test_zero_maxscore_uses_default(self):
        sig = {"confluenceScore": 1.5, "maxScore": 0, "direction": "LONG"}
        out = normalise_engine_a(sig)
        assert out["max_score"] == 3.0

    def test_negative_maxscore_uses_default(self):
        sig = {"confluenceScore": 1.5, "maxScore": -1, "direction": "LONG"}
        out = normalise_engine_a(sig)
        assert out["max_score"] == 3.0

    def test_score_norm_clamped_0_to_1(self):
        sig = {"confluenceScore": 5.0, "maxScore": 3.0, "direction": "LONG"}
        out = normalise_engine_a(sig)
        assert out["score_norm"] == 1.0

    def test_missing_direction_no_signal(self):
        sig = {"confluenceScore": 2.0, "maxScore": 3.0}
        out = normalise_engine_a(sig)
        assert out["has_signal"] is False

    @pytest.mark.parametrize("malformed", [float("nan"), float("inf"), float("-inf"), "bad"])
    def test_legacy_nonfinite_or_malformed_score_fails_closed(self, malformed):
        sig = {
            "confluenceScore": malformed,
            "maxScore": 3.0,
            "direction": "LONG",
            "engineATradeEnabled": True,
        }
        out = normalise_engine_a(sig)
        assert out["score_norm"] == 0.0
        assert out["has_signal"] is False


# ── normalise_engine_b edge cases ────────────────────────────────────────────

class TestNormaliseEngineB:
    def test_quality_pct_over_saturated_total(self):
        """score_norm reads the quality layer, not the total ratio.

        Engine B only sets `passed` when every mandatory gate is true, so
        gate_score always equals gate_max_possible and score/max_possible floors
        near 0.83 — Engine C's A/B blend was reading a near-constant for B. `pct`
        is also rounded to an integer by calculate_confidence, so preferring it
        made Engine C and the scanner blend different numbers for one signal.
        """
        sig = _engine_b_signal()
        conf = _engine_b_confidence(passed=True)
        conf["pct"] = 75
        conf["quality_pct"] = 42.0
        out = normalise_engine_b(sig, conf)
        assert out["score_norm"] == 0.42

    def test_score_norm_falls_back_to_total_without_quality_fields(self):
        sig = _engine_b_signal()
        conf = _engine_b_confidence(passed=True)
        conf.pop("quality_pct", None)
        conf["score"] = 3.0
        conf["max_possible"] = 6.0
        out = normalise_engine_b(sig, conf)
        assert out["score_norm"] == 0.5

    def test_missing_direction_no_signal(self):
        sig = {"structural_verdict": "CLEAR"}
        conf = {"score": 5.0, "max_possible": 5.0, "passed": True}
        out = normalise_engine_b(sig, conf)
        assert out["has_signal"] is False
        assert out["signal_diagnostic"] == "engine_b_direction_missing"

    def test_error_verdict_no_signal(self):
        sig = {"structural_verdict": "ERROR", "direction": "LONG"}
        conf = {"score": 5.0, "max_possible": 5.0, "passed": True}
        out = normalise_engine_b(sig, conf)
        assert out["has_signal"] is False
        assert out["signal_diagnostic"] == "engine_b_verdict_not_clear"
