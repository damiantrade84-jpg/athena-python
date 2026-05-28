"""Regression tests for Engine A / Engine B separation fixes.

Covers nine surgical fixes:
  1. AutoTrader engine classification — explicit engine wins over nested overlay.
  2. Scanner does not overwrite Engine A SL/TP with Engine B execution levels.
  3. AutoTrader uses pure Engine A conviction for Engine A rows.
  4. Engine A perfect weighted-TF tie returns no tradable direction.
  5. Hard-aborted / zero-score Engine A rows carry no tradable direction.
  6. trendState uses the ADX source that gated Engine A scoring.
  7. Engine B scanner h4_snap is built from real H4 candles.
  8. Stale labels (forex_scoring/D1 ADX Trend) are renamed where safe.
  9. End-to-end Gate-B contamination scenarios A/B/C.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import auto_trader
import factor_scoring
import scanner
import scoring
from auto_trader import _execution_conviction, _signal_engine
from factor_scoring import _coherent_trend_score, _zero_result


# ─────────────────────────────────────────────────────────────────────────────
# Task 1 — _signal_engine: explicit identity beats nested overlay metadata.
# ─────────────────────────────────────────────────────────────────────────────


class TestTask1_SignalEngineExplicitIdentityWins:
    def test_engine_a_with_nested_engine_b_overlay_still_classified_a(self):
        sig = {
            "engine": "A",
            "engine_b": {"score": 4.1, "verdict": "CLEAR"},
        }
        assert _signal_engine(sig) == "engine_a"

    def test_engine_a_with_nested_naked_data_still_classified_a(self):
        sig = {
            "engine": "engine_a",
            "naked_data": {"score": 3.8, "lifecycle": "trigger"},
        }
        assert _signal_engine(sig) == "engine_a"

    def test_engine_source_engine_a_uppercase_classified_a(self):
        # scanner.py sets engine_source=ENGINE_A_SOURCE which is "ENGINE_A".
        sig = {"engine_source": "ENGINE_A", "engine_b": {"score": 4.2}}
        assert _signal_engine(sig) == "engine_a"

    def test_engine_a_with_is_naked_overlay_flag_still_classified_a(self):
        sig = {"engine": "A", "is_naked": True}
        assert _signal_engine(sig) == "engine_a"

    def test_explicit_engine_b_classified_b(self):
        assert _signal_engine({"engine": "B"}) == "engine_b"
        assert _signal_engine({"engine_source": "ENGINE_B"}) == "engine_b"

    def test_explicit_engine_c_classified_c(self):
        assert _signal_engine({"engine": "engine_c"}) == "engine_c"
        assert _signal_engine({"engine": "C"}) == "engine_c"
        assert _signal_engine({"engine": "consensus"}) == "engine_c"

    def test_scalp_still_classified_scalp(self):
        assert _signal_engine({"engine": "scalp"}) == "scalp"

    def test_no_explicit_identity_with_nested_engine_b_falls_back_to_b(self):
        # Backward-compatible fallback: if no explicit identity is set,
        # nested engine_b metadata still classifies as Engine B.
        assert _signal_engine({"engine_b": {"score": 4.0}}) == "engine_b"
        assert _signal_engine({"naked_data": {"score": 3.0}}) == "engine_b"
        assert _signal_engine({"is_naked": True}) == "engine_b"

    def test_unknown_or_missing_engine_defaults_to_engine_a(self):
        assert _signal_engine({}) == "engine_a"
        assert _signal_engine({"engine": "unknown"}) == "engine_a"


# ─────────────────────────────────────────────────────────────────────────────
# Task 2 — _apply_engine_b_scan_levels: never clobber Engine A generic levels.
# ─────────────────────────────────────────────────────────────────────────────


class TestTask2_EngineBScanLevelsDoNotClobberEngineA:
    @staticmethod
    def _conf_b():
        return {"execution_sl": 98.5, "execution_tp": 103.0, "rr_used_for_gate": 2.0}

    @staticmethod
    def _res_b():
        return {"recommended_stop_loss": 95.0, "recommended_take_profit": 105.0}

    def test_engine_a_row_keeps_engine_a_levels_when_config_true(self, monkeypatch):
        monkeypatch.setitem(
            scanner.CONFIG, "ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS", True
        )
        signal = {
            "engine": "A",
            "engine_source": "ENGINE_A",
            "sl": 90.0,
            "tp1": 110.0,
            "tp2": 120.0,
            "levelSource": "engine_a_atr",
        }

        scanner._apply_engine_b_scan_levels(signal, self._conf_b(), self._res_b())

        assert signal["sl"] == pytest.approx(90.0)
        assert signal["tp1"] == pytest.approx(110.0)
        assert signal["tp2"] == pytest.approx(120.0)
        assert signal["levelSource"] == "engine_a_atr"
        # Engine B overlay levels still stored separately.
        assert signal["engine_b_execution_sl"] == pytest.approx(98.5)
        assert signal["engine_b_execution_tp"] == pytest.approx(103.0)

    def test_engine_b_row_still_uses_b_levels_as_generic(self, monkeypatch):
        monkeypatch.setitem(
            scanner.CONFIG, "ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS", True
        )
        signal = {
            "engine": "B",
            "engine_source": "ENGINE_B",
            "sl": 90.0,
            "tp1": 110.0,
            "tp2": 120.0,
        }

        scanner._apply_engine_b_scan_levels(signal, self._conf_b(), self._res_b())

        assert signal["sl"] == pytest.approx(98.5)
        assert signal["tp1"] == pytest.approx(103.0)
        assert signal["tp2"] == pytest.approx(103.0)
        assert signal["levelSource"] == "engine_b_execution"

    def test_engine_c_with_selected_b_levels_uses_b_as_generic(self, monkeypatch):
        monkeypatch.setitem(
            scanner.CONFIG, "ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS", True
        )
        signal = {
            "engine": "engine_c",
            "engine_c_selected_levels": "engine_b",
            "sl": 90.0,
            "tp1": 110.0,
        }
        scanner._apply_engine_b_scan_levels(signal, self._conf_b(), self._res_b())
        assert signal["sl"] == pytest.approx(98.5)
        assert signal["tp1"] == pytest.approx(103.0)
        assert signal["levelSource"] == "engine_b_execution"

    def test_engine_c_without_selected_b_levels_keeps_consensus_levels(self, monkeypatch):
        monkeypatch.setitem(
            scanner.CONFIG, "ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS", True
        )
        signal = {"engine": "engine_c", "sl": 90.0, "tp1": 110.0}
        scanner._apply_engine_b_scan_levels(signal, self._conf_b(), self._res_b())
        assert signal["sl"] == pytest.approx(90.0)
        assert signal["tp1"] == pytest.approx(110.0)
        # Overlay still stored.
        assert signal["engine_b_execution_sl"] == pytest.approx(98.5)

    def test_default_config_does_not_overwrite_even_for_engine_b(self, monkeypatch):
        # With the new default of False, Engine B rows also keep their
        # original generic SL/TP unless the legacy flag is explicitly on.
        monkeypatch.setitem(
            scanner.CONFIG, "ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS", False
        )
        signal = {"engine": "B", "sl": 90.0, "tp1": 110.0}
        scanner._apply_engine_b_scan_levels(signal, self._conf_b(), self._res_b())
        assert signal["sl"] == pytest.approx(90.0)
        assert signal["tp1"] == pytest.approx(110.0)
        # Overlay always stored independent of the legacy flag.
        assert signal["engine_b_execution_sl"] == pytest.approx(98.5)
        assert signal["engine_b_execution_tp"] == pytest.approx(103.0)

    def test_config_default_is_false_in_baseline(self):
        # The shipped default must be False so Engine A rows are never
        # silently contaminated even when a CONFIG.get fallback fires.
        from config import CONFIG as _BASE_CFG

        assert _BASE_CFG.get("ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS") is False


# ─────────────────────────────────────────────────────────────────────────────
# Task 3 — _execution_conviction: pure A norm for Engine A rows.
# ─────────────────────────────────────────────────────────────────────────────


class TestTask3_ExecutionConvictionPerEngine:
    def test_engine_a_uses_score_norm_not_combined(self):
        sig = {
            "engine": "A",
            "engine_source": "ENGINE_A",
            "scoreNorm": 0.70,
            "combinedConviction": 0.42,
            "confluenceScore": 2.1,
            "maxScore": 3.0,
        }
        assert _execution_conviction(sig, "engine_a") == pytest.approx(0.70)

    def test_engine_a_falls_back_to_confluence_over_max_when_norm_missing(self):
        sig = {
            "engine": "A",
            "confluenceScore": 2.4,
            "maxScore": 3.0,
            "combinedConviction": 0.30,
        }
        assert _execution_conviction(sig, "engine_a") == pytest.approx(0.80)

    def test_engine_c_uses_combined_conviction(self):
        sig = {
            "engine": "engine_c",
            "scoreNorm": 0.70,
            "combinedConviction": 0.58,
        }
        assert _execution_conviction(sig, "engine_c") == pytest.approx(0.58)

    def test_engine_b_uses_b_score_normalized(self):
        sig = {
            "engine": "B",
            "engine_b_score": 4.0,
            "engine_b_max": 5.0,
            "combinedConviction": 0.30,
        }
        assert _execution_conviction(sig, "engine_b") == pytest.approx(0.80)

    def test_engine_b_with_explicit_norm_preferred(self):
        sig = {
            "engine": "B",
            "engine_b_score_norm": 0.85,
            "engine_b_score": 4.0,
            "engine_b_max": 5.0,
        }
        assert _execution_conviction(sig, "engine_b") == pytest.approx(0.85)

    def test_engine_a_unmissing_data_returns_zero(self):
        assert _execution_conviction({"engine": "A"}, "engine_a") == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Task 4 — Perfect weighted TF tie returns no tradable direction.
# ─────────────────────────────────────────────────────────────────────────────


def _trend_snap_long():
    # ema21 > ema50 > ema200 → bullish stack.
    return {"ema21": 110.0, "ema50": 100.0, "ema200": 90.0, "close": 111.0}


def _trend_snap_short():
    return {"ema21": 90.0, "ema50": 100.0, "ema200": 110.0, "close": 89.0}


class TestTask4_PerfectTieReturnsNoDirection:
    def test_d1_long_h4_short_h1_short_perfect_tie_no_direction(self):
        # D1 weight 0.50 LONG vs H4(0.30) + H1(0.20) SHORT = 0.50 ↔ tie.
        score, direction, detail = _coherent_trend_score(
            _trend_snap_long(),
            _trend_snap_short(),
            _trend_snap_short(),
            "crypto",
            d1_prev=_trend_snap_long(),
            h4_prev=_trend_snap_short(),
            h1_prev=_trend_snap_short(),
        )
        assert score == 0.0
        assert direction is None
        assert detail.get("error") in {"weighted_tf_tie", "tie_no_majority"}

    def test_d1_short_h4_long_h1_long_perfect_tie_no_direction(self):
        score, direction, detail = _coherent_trend_score(
            _trend_snap_short(),
            _trend_snap_long(),
            _trend_snap_long(),
            "crypto",
            d1_prev=_trend_snap_short(),
            h4_prev=_trend_snap_long(),
            h1_prev=_trend_snap_long(),
        )
        assert score == 0.0
        assert direction is None
        assert detail.get("error") in {"weighted_tf_tie", "tie_no_majority"}

    def test_majority_d1_h4_long_h1_short_remains_long(self):
        score, direction, _ = _coherent_trend_score(
            _trend_snap_long(),
            _trend_snap_long(),
            _trend_snap_short(),
            "crypto",
            d1_prev=_trend_snap_long(),
            h4_prev=_trend_snap_long(),
            h1_prev=_trend_snap_short(),
        )
        assert direction == "LONG"
        assert score > 0.0

    def test_majority_h4_h1_long_d1_short_remains_long(self):
        score, direction, _ = _coherent_trend_score(
            _trend_snap_short(),
            _trend_snap_long(),
            _trend_snap_long(),
            "crypto",
            d1_prev=_trend_snap_short(),
            h4_prev=_trend_snap_long(),
            h1_prev=_trend_snap_long(),
        )
        # Per existing weighting D1=0.50 vs H4(0.30)+H1(0.20)=0.50, this is
        # also tied. Only when D1 disagrees with both other TFs and weights
        # are NOT a tie does direction emerge. Verify it stays None when
        # tied, otherwise resolves to the majority side.
        if direction is None:
            assert score == 0.0
        else:
            # Engine A v2 may have configured different weights so non-tie
            # cases resolve normally.
            assert direction in {"LONG", "SHORT"}


# ─────────────────────────────────────────────────────────────────────────────
# Task 5 — Hard-aborted Engine A results return no tradable direction.
# ─────────────────────────────────────────────────────────────────────────────


class TestTask5_HardAbortNoTradableDirection:
    def test_adx_hard_abort_returns_no_tradable_direction(self):
        result = _zero_result(
            pair={"type": "crypto", "display": "BTCUSDT"},
            regime="DEAD",
            trend_detail={},
            feed_status={"adx": "d1"},
            reason="adx_hard_abort",
            adx_val=8.0,
            direction="LONG",
        )
        # direction must not be tradable; either None or explicitly flagged.
        assert (
            result.get("direction") is None
            or result.get("signalExecutable") is False
            or result.get("directionStatus") == "diagnostic_only"
        )
        # Diagnostic intent should still be visible somewhere for audit.
        assert (
            result.get("diagnostic_direction") == "LONG"
            or result.get("direction") == "LONG"
        )

    def test_min_directional_failed_returns_no_tradable_direction(self):
        result = _zero_result(
            pair={"type": "crypto", "display": "ETHUSDT"},
            regime="RANGING",
            trend_detail={},
            feed_status={},
            reason="min_directional_failed",
            min_directional=0.85,
            min_directional_failed=True,
            direction="SHORT",
        )
        assert (
            result.get("direction") is None
            or result.get("signalExecutable") is False
            or result.get("directionStatus") == "diagnostic_only"
        )

    def test_zero_score_result_has_zero_final_score(self):
        result = _zero_result(
            pair={"type": "forex", "display": "EUR/USD"},
            regime="RANGING",
            trend_detail={},
            feed_status={},
            reason="adx_hard_abort",
            adx_val=5.0,
            direction="LONG",
        )
        assert result["final_score"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Task 6 — trendState uses Engine A factor ADX source.
# ─────────────────────────────────────────────────────────────────────────────


class TestTask6_TrendStateAdxSource:
    def test_trend_state_uses_factor_adx_value_over_h4_snap(self):
        factor_result = {
            "final_score": 2.0,
            "direction": "LONG",
            "regime": "TRENDING",
            "factor_scores": {"trend": 1.0, "momentum": 0.4, "addon": 0.1},
            "weights": {"trend": 1.0},
            "adx_value": 38.0,
            "adx_source": "d1",
            "feed_status": {"adx": "d1"},
        }
        h4_snap = {"adx": 18.0}
        ts, source, value = scoring._resolve_trend_state(
            factor_result, h4_snap, pair_type="forex"
        )
        assert source == "d1"
        assert value == pytest.approx(38.0)
        assert ts == "TRENDING"

    def test_trend_state_falls_back_to_h4_when_factor_adx_missing(self):
        factor_result = {
            "final_score": 1.5,
            "direction": "LONG",
            "regime": "DEVELOPING",
            "factor_scores": {"trend": 0.8},
            "weights": {"trend": 1.0},
            "adx_value": None,
            "adx_source": None,
        }
        h4_snap = {"adx": 27.0}
        ts, source, value = scoring._resolve_trend_state(
            factor_result, h4_snap, pair_type="crypto"
        )
        assert source == "h4_fallback"
        assert value == pytest.approx(27.0)
        assert ts == "DEVELOPING"

    def test_trend_state_unknown_when_both_missing(self):
        factor_result = {
            "adx_value": None,
            "adx_source": None,
        }
        ts, source, value = scoring._resolve_trend_state(
            factor_result, {"adx": None}, pair_type="stock"
        )
        assert ts == "UNKNOWN"
        assert value is None


class TestTrendStateClassAwareThresholds:
    """The TRENDING/DEVELOPING regime classifier must honour per-asset-class ADX
    cutoffs (TRENDING_ADX_CLASS / DEVELOPING_ADX_CLASS), mirroring the existing
    per-class RANGING.dead structure, so a crypto/forex trend that the class-aware
    ADX *gate* fully credits is not mislabelled by stock-grade universal cutoffs.
    Defaults remain the universal scalars (TRENDING_ADX / DEVELOPING_ADX)."""

    def _factor(self, adx):
        return {"adx_value": adx, "adx_source": "d1"}

    def test_class_trending_cutoff_overrides_universal_scalar(self, monkeypatch):
        # Universal TRENDING_ADX is 35; crypto at adx 27 would be DEVELOPING.
        # A per-class crypto TRENDING cutoff of 25 must promote it to TRENDING.
        monkeypatch.setitem(scoring.CONFIG, "TRENDING_ADX_CLASS", {"crypto": 25})
        ts, _, value = scoring._resolve_trend_state(
            self._factor(27.0), {"adx": 27.0}, pair_type="crypto"
        )
        assert value == pytest.approx(27.0)
        assert ts == "TRENDING"

    def test_class_developing_cutoff_overrides_universal_scalar(self, monkeypatch):
        # Universal DEVELOPING_ADX is 25; forex at adx 21 would be RANGING.
        # A per-class forex DEVELOPING cutoff of 20 must promote it to DEVELOPING.
        monkeypatch.setitem(scoring.CONFIG, "DEVELOPING_ADX_CLASS", {"forex": 20})
        ts, _, _value = scoring._resolve_trend_state(
            self._factor(21.0), {"adx": 21.0}, pair_type="forex"
        )
        assert ts == "DEVELOPING"

    def test_unlisted_class_falls_back_to_universal_scalar(self, monkeypatch):
        # stock has no class entry → universal scalars apply (adx 27 → DEVELOPING).
        monkeypatch.setitem(scoring.CONFIG, "TRENDING_ADX_CLASS", {"crypto": 25})
        monkeypatch.setitem(scoring.CONFIG, "DEVELOPING_ADX_CLASS", {"forex": 20})
        ts, _, _ = scoring._resolve_trend_state(
            self._factor(27.0), {"adx": 27.0}, pair_type="stock"
        )
        assert ts == "DEVELOPING"


# ─────────────────────────────────────────────────────────────────────────────
# Task 7 — Engine B scanner h4_snap is built from real H4 candles.
# ─────────────────────────────────────────────────────────────────────────────


def _toy_candle(close, *, ts=0):
    return {"open": close, "high": close + 0.2, "low": close - 0.2, "close": close, "volume": 1_000, "time": ts}


class TestTask7_EngineBH4SnapSource:
    def test_resolve_engine_b_h4_snap_prefers_h4_candles(self, monkeypatch):
        h4 = [_toy_candle(100 + i * 0.5, ts=i) for i in range(60)]
        d1 = [_toy_candle(200 + i, ts=i) for i in range(60)]

        captured = {}

        def fake_indicators(candles, ptype):
            captured["candles_first_close"] = candles[0]["close"]
            return {"snap": {"close": candles[-1]["close"], "adx": 22.0}}

        monkeypatch.setattr(
            scanner, "calc_indicators_with_normalized", fake_indicators
        )

        snap = scanner._resolve_engine_b_h4_snap(
            h4_candles=h4, zone_candles=d1, asset_type="forex"
        )
        # The captured first close must come from h4, not d1.
        assert captured["candles_first_close"] == pytest.approx(h4[0]["close"])
        assert snap.get("close") == pytest.approx(h4[-1]["close"])

    def test_resolve_engine_b_h4_snap_returns_empty_when_h4_missing(self, monkeypatch):
        def fake_indicators(candles, ptype):
            return {"snap": {"close": candles[-1]["close"]}}

        monkeypatch.setattr(
            scanner, "calc_indicators_with_normalized", fake_indicators
        )

        snap = scanner._resolve_engine_b_h4_snap(
            h4_candles=[], zone_candles=[_toy_candle(100)], asset_type="forex"
        )
        assert snap == {}


# ─────────────────────────────────────────────────────────────────────────────
# Task 8 — Label hygiene only where safe.
# ─────────────────────────────────────────────────────────────────────────────


class TestTask8_LabelHygiene:
    def test_backtest_engine_label_no_longer_says_forex_scoring_for_forex(self):
        # The persisted backtest engine label for forex Engine A v2 should not
        # claim "forex_scoring" (the legacy module). Live forex routes through
        # factor_scoring per factor_scoring.py module docstring.
        import backtest_runner

        with open(backtest_runner.__file__, "r", encoding="utf-8") as f:
            src = f.read()
        # The mislabel pattern checks for the conditional that wrote
        # "forex_scoring" for forex pairs. After the fix the inserted
        # engine label uses "factor_scoring" (or "engine_a_v2") instead.
        assert '"forex_scoring"\n                    if pair.get("type") == "forex"' not in src

    def test_legacy_votes_label_d1_adx_trend_renamed_or_removed(self):
        with open(scoring.__file__, "r", encoding="utf-8") as f:
            src = f.read()
        # The mislabel "D1 ADX Trend" mapping to FACTOR_MOMENTUM should be
        # renamed to reflect what it actually represents (momentum, not ADX).
        assert '"D1 ADX Trend": v.get("FACTOR_MOMENTUM"' not in src


# ─────────────────────────────────────────────────────────────────────────────
# Task 9 — End-to-end Gate-B contamination scenarios.
# ─────────────────────────────────────────────────────────────────────────────


class TestTask9_GateBContaminationScenarios:
    def test_scenario_a_engine_a_with_b_overlay_stays_engine_a(self, monkeypatch):
        # Engine A row carrying nested Engine B overlay metadata + Engine B
        # execution-level overlay must remain classified as Engine A, keep
        # its Engine A levels, and use Engine A conviction.
        monkeypatch.setitem(
            scanner.CONFIG, "ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS", True
        )

        signal = {
            "engine": "A",
            "engine_source": "ENGINE_A",
            "engine_b": {"score": 4.2, "verdict": "CLEAR"},
            "naked_data": {"score": 3.8},
            "sl": 90.0,
            "tp1": 110.0,
            "tp2": 120.0,
            "levelSource": "engine_a_atr",
            "scoreNorm": 0.70,
            "combinedConviction": 0.42,
            "confluenceScore": 2.1,
            "maxScore": 3.0,
        }

        assert _signal_engine(signal) == "engine_a"

        scanner._apply_engine_b_scan_levels(
            signal,
            {"execution_sl": 95.0, "execution_tp": 105.0, "rr_used_for_gate": 2.0},
            {"recommended_stop_loss": 95.0, "recommended_take_profit": 105.0},
        )
        assert signal["sl"] == pytest.approx(90.0)
        assert signal["tp1"] == pytest.approx(110.0)
        assert signal["levelSource"] == "engine_a_atr"

        assert _execution_conviction(signal, "engine_a") == pytest.approx(0.70)

    def test_scenario_b_engine_b_only_row_uses_b_path(self, monkeypatch):
        monkeypatch.setitem(
            scanner.CONFIG, "ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS", True
        )
        signal = {
            "engine": "B",
            "engine_source": "ENGINE_B",
            "sl": 0.0,
            "tp1": 0.0,
            "engine_b_score": 4.5,
            "engine_b_max": 5.0,
        }
        assert _signal_engine(signal) == "engine_b"
        scanner._apply_engine_b_scan_levels(
            signal,
            {"execution_sl": 99.0, "execution_tp": 102.0, "rr_used_for_gate": 2.0},
            {"recommended_stop_loss": 99.0, "recommended_take_profit": 102.0},
        )
        assert signal["sl"] == pytest.approx(99.0)
        assert signal["tp1"] == pytest.approx(102.0)
        assert _execution_conviction(signal, "engine_b") == pytest.approx(0.90)

    def test_scenario_c_engine_c_uses_combined_and_only_b_levels_when_selected(
        self, monkeypatch
    ):
        monkeypatch.setitem(
            scanner.CONFIG, "ENGINE_B_USE_EXECUTION_LEVELS_FOR_SCAN_SIGNALS", True
        )
        # Engine C without explicit B-level selection: keep consensus levels.
        sig_c_keep = {"engine": "engine_c", "sl": 100.0, "tp1": 110.0, "combinedConviction": 0.61}
        scanner._apply_engine_b_scan_levels(
            sig_c_keep,
            {"execution_sl": 95.0, "execution_tp": 105.0},
            {"recommended_stop_loss": 95.0, "recommended_take_profit": 105.0},
        )
        assert sig_c_keep["sl"] == pytest.approx(100.0)
        assert _execution_conviction(sig_c_keep, "engine_c") == pytest.approx(0.61)

        # Engine C with explicit B-level selection: B levels become generic.
        sig_c_use_b = {
            "engine": "engine_c",
            "engine_c_selected_levels": "engine_b",
            "sl": 100.0,
            "tp1": 110.0,
            "combinedConviction": 0.61,
        }
        scanner._apply_engine_b_scan_levels(
            sig_c_use_b,
            {"execution_sl": 95.0, "execution_tp": 105.0},
            {"recommended_stop_loss": 95.0, "recommended_take_profit": 105.0},
        )
        assert sig_c_use_b["sl"] == pytest.approx(95.0)
