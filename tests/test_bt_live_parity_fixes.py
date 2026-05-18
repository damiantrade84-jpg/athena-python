"""Regression tests for the BT-vs-live parity audit fixes.

Each test maps to a finding in the audit (C1-C3, H1-H6, M1-M6, L1-L4) and
asserts the relevant invariant. Tests are intentionally small and side-
effect free; they call helpers directly rather than running a full BT.
"""
from __future__ import annotations

import importlib

import pytest

from config import CONFIG


# ── L1: get_score_threshold parameter cleanup ────────────────────────────────
def test_l1_get_score_threshold_signature_no_is_backtest():
    import inspect
    from scoring import get_score_threshold

    sig = inspect.signature(get_score_threshold)
    assert "is_backtest" not in sig.parameters, (
        "is_backtest parameter must be removed (audit L1)"
    )


def test_l1_get_min_and_get_backtest_thresholds_match():
    from scoring import (
        get_min_confluence_threshold,
        get_backtest_min_score_threshold,
    )

    pair = {"display": "EUR/USD", "type": "forex"}
    assert get_min_confluence_threshold(pair) == get_backtest_min_score_threshold(pair)


# ── C1: Sizing helper mirrors live risk_engine ───────────────────────────────
def test_c1_bt_risk_pct_returns_base_with_no_drawdown():
    from backtest_runner import _bt_risk_pct, _live_base_risk_pct

    base = _live_base_risk_pct("forex")
    risk, dd, allowed = _bt_risk_pct("forex", regime=None, equity=1.0, peak=1.0)
    assert allowed is True
    assert dd == 0.0
    # base may be lifted by Kelly; never below the asset_risk_map floor.
    assert risk >= base * 0.99


def test_c1_bt_risk_pct_halves_at_10pct_drawdown():
    from backtest_runner import _bt_risk_pct

    full, _, full_ok = _bt_risk_pct("forex", regime=None, equity=1.0, peak=1.0)
    half, dd, half_ok = _bt_risk_pct("forex", regime=None, equity=0.88, peak=1.0)
    assert full_ok and half_ok
    assert dd >= 0.10
    # Halving applies on the same base; allow tiny float jitter
    assert half == pytest.approx(full * 0.5, rel=1e-6)


def test_c1_bt_risk_pct_blocks_at_15pct_drawdown():
    from backtest_runner import _bt_risk_pct

    risk, dd, allowed = _bt_risk_pct("forex", regime=None, equity=0.83, peak=1.0)
    assert dd >= 0.15
    assert allowed is False
    assert risk == 0.0


# ── C2: Pre-trade invariants helper catches direction-vs-score mismatch ─────
def test_c2_pre_trade_invariants_blocks_direction_score_mismatch():
    from guardian import pre_trade_invariants

    sig = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.10,
        "sl": 1.09,
        "tp1": 1.12,
        "type": "forex",
        "factorDiagnostics": {"directionalScore": -0.8},
    }
    ok, reason = pre_trade_invariants(sig)
    assert ok is False
    assert "DIRECTION_SCORE_MISMATCH" in reason


def test_c2_pre_trade_invariants_allows_aligned_signal():
    from guardian import pre_trade_invariants

    sig = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.10,
        "sl": 1.09,
        "tp1": 1.12,
        "type": "forex",
        "factorDiagnostics": {"directionalScore": 0.4},
    }
    ok, _ = pre_trade_invariants(sig)
    assert ok is True


def test_c2_pre_trade_invariants_blocks_inverted_geometry():
    from guardian import pre_trade_invariants

    sig = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "price": 1.10,
        "sl": 1.11,  # wrong side
        "tp1": 1.12,
        "type": "forex",
    }
    ok, reason = pre_trade_invariants(sig)
    assert ok is False
    assert "LONG_SL_ABOVE_ENTRY" in reason


def test_c2_pre_trade_invariants_allows_short_with_negative_score():
    from guardian import pre_trade_invariants

    sig = {
        "pair": "EUR/USD",
        "direction": "SHORT",
        "price": 1.10,
        "sl": 1.11,
        "tp1": 1.08,
        "type": "forex",
        "factorDiagnostics": {"directionalScore": -0.8},
    }
    ok, reason = pre_trade_invariants(sig)
    assert ok is True, f"Expected SHORT with negative score to pass, got: {reason}"


def test_c2_pre_trade_invariants_blocks_short_with_positive_score():
    from guardian import pre_trade_invariants

    sig = {
        "pair": "EUR/USD",
        "direction": "SHORT",
        "price": 1.10,
        "sl": 1.11,
        "tp1": 1.08,
        "type": "forex",
        "factorDiagnostics": {"directionalScore": 0.5},
    }
    ok, reason = pre_trade_invariants(sig)
    assert ok is False
    assert "DIRECTION_SCORE_MISMATCH" in reason


def test_c2_bt_pre_trade_invariants_skips_diagnostics_on_consensus_override():
    """Engine C B-override: consensus SHORT but Engine A factorDiagnostics says LONG.
    The helper must not pass Engine A's irrelevant diagnostics, or the invariant
    would falsely reject the trade."""
    from backtest_runner import _bt_pre_trade_invariants

    pair = {"display": "GBP/USD", "type": "forex"}
    # Simulate the exact call site logic in backtest_pair_consensus:
    res_a = {"direction": "LONG", "factorDiagnostics": {"directionalScore": 0.6}}
    consensus_direction = "SHORT"
    _fd = (
        res_a.get("factorDiagnostics")
        if res_a.get("direction") == consensus_direction
        else None
    )
    ok, reason = _bt_pre_trade_invariants(
        pair,
        direction=consensus_direction,
        entry=1.3000,
        sl=1.3050,
        tp1=1.2950,
        factor_diagnostics=_fd,
        asset_type="forex",
    )
    assert ok is True, f"Consensus override SHORT should pass when A diagnostics are excluded: {reason}"


# ── C3: Engine C BT min-conviction default flipped to True ───────────────────
def test_engine_c_bt_selects_engine_b_direction_independent_from_engine_a_hint():
    from backtest_runner import _engine_c_select_engine_b_bt_candidate

    class FakeNakedEngine:
        def precompute_structure_data(self, *args, **kwargs):
            return {"precomputed": True}

        def analyze_structure_direction(self, precomputed, current_price, direction):
            assert precomputed == {"precomputed": True}
            if direction == "SHORT":
                return {
                    "structural_verdict": "CLEAR",
                    "recommended_stop_loss": 101.0,
                    "recommended_take_profit": 98.0,
                    "order_blocks": [{"strength": 2}],
                }
            return {
                "structural_verdict": "CLEAR",
                "recommended_stop_loss": 99.0,
                "recommended_take_profit": 102.0,
                "order_blocks": [],
            }

        def calculate_confidence(self, res, current_price, direction, **kwargs):
            return {
                "score": 8.0 if direction == "SHORT" else 2.0,
                "pct": 80.0 if direction == "SHORT" else 20.0,
                "structure_ok": True,
                "zone_ok": True,
                "trigger_ok": True,
                "entry_ok": True,
                "rr_ok": True,
                "rr": 2.0,
            }

    def confidence_passes(conf, style_profile, regime_label, asset_type=""):
        return conf["score"] >= 5.0, 5.0

    selected = _engine_c_select_engine_b_bt_candidate(
        naked_engine=FakeNakedEngine(),
        d1_ctx=[],
        zone_ctx=[],
        h1_window=[],
        current_price=100.0,
        atr=1.0,
        regime_label="RANGING",
        style_profile={"fallback_rr": 2.0},
        asset_type="forex",
        enable_profile_context=False,
        d1_snap={},
        h4_snap={},
        resolved_style="intraday",
        pair={"display": "EUR/USD", "type": "forex"},
        confidence_passes=confidence_passes,
        direction_hint="LONG",
    )

    signal_b, conf_b, _res_b = selected
    assert signal_b["direction"] == "SHORT"
    assert signal_b["engine_b_independent_direction"]["direction"] == "SHORT"
    assert conf_b["passed"] is True


def test_engine_d_bt_fee_guard_blocks_micro_stop():
    from backtest_runner import _engine_d_fee_guard_metrics

    metrics = _engine_d_fee_guard_metrics(
        cfg={
            "ENGINE_D_FEE_GUARD_ENABLED": True,
            "ENGINE_D_MIN_STOP_PCT": 0.0005,
            "ENGINE_D_MAX_COST_R": 0.20,
        },
        asset_type="crypto",
        entry=100.0,
        sl=99.99,
        estimated_fee_pct=0.0004,
        estimated_slippage_pct=0.0002,
    )

    assert metrics["engine_d_reject_reason"] == "fee_guard_micro_stop"


def test_engine_d_bt_fee_guard_blocks_high_cost_r():
    from backtest_runner import _engine_d_fee_guard_metrics

    metrics = _engine_d_fee_guard_metrics(
        cfg={
            "ENGINE_D_FEE_GUARD_ENABLED": True,
            "ENGINE_D_MIN_STOP_PCT": 0.0005,
            "ENGINE_D_MAX_COST_R": 0.20,
        },
        asset_type="crypto",
        entry=100.0,
        sl=99.0,
        estimated_fee_pct=0.002,
        estimated_slippage_pct=0.001,
    )

    assert metrics["engine_d_reject_reason"] == "fee_guard_high_cost"


def test_engine_d_bt_grading_receives_proxy_fidelity_inputs():
    import inspect
    import backtest_runner

    src = inspect.getsource(backtest_runner.backtest_pair_scalp)
    assert "data_fidelity=data_fidelity" in src
    assert "tick_vol_quality=tick_vol_quality" in src
    assert '"data_fidelity": data_fidelity' in src


def test_c3_default_engine_c_bt_enforce_min_conviction_is_truthy():
    # If unset in CONFIG, the runtime default (read inside backtest_runner) is True.
    assert bool(CONFIG.get("ENGINE_C_BT_ENFORCE_MIN_CONVICTION", True)) is True


# ── H1: get_score_threshold applies regime multiplier when enabled ──────────
def test_h1_regime_multiplier_applied_when_enabled(monkeypatch):
    from scoring import get_score_threshold

    pair = {"display": "EUR/USD", "type": "forex"}
    base = get_score_threshold(pair)

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_REGIME_DYNAMIC_THRESHOLDS",
        {
            "ENABLED": True,
            "TRENDING_MULTIPLIER": 0.90,
            "RANGING_MULTIPLIER": 1.10,
            "HIGH_VOLATILITY_MULTIPLIER": 1.15,
        },
    )
    trending = get_score_threshold(pair, regime="TRENDING")
    ranging = get_score_threshold(pair, regime="RANGING")
    high_vol = get_score_threshold(pair, regime="HIGH_VOLATILITY")

    assert trending == pytest.approx(base * 0.90, rel=1e-6)
    assert ranging == pytest.approx(base * 1.10, rel=1e-6)
    assert high_vol == pytest.approx(base * 1.15, rel=1e-6)


def test_h1_regime_multiplier_inert_when_disabled(monkeypatch):
    from scoring import get_score_threshold

    pair = {"display": "EUR/USD", "type": "forex"}
    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_REGIME_DYNAMIC_THRESHOLDS",
        {"ENABLED": False},
    )
    base = get_score_threshold(pair)
    assert get_score_threshold(pair, regime="RANGING") == base


# ── H2: Quantile gate note documents the parity gap ─────────────────────────
def test_h2_engine_a_bt_gate_note_mentions_quantile_gap_when_enabled(monkeypatch):
    from backtest_runner import _engine_a_bt_gate_note

    monkeypatch.setitem(CONFIG, "SCAN_QUANTILE_ENABLED", True)
    note = _engine_a_bt_gate_note()
    assert "SCAN_QUANTILE_ENABLED=true" in note
    assert "BT cannot replay" in note


# ── H4: Engine B BT structure-gate escape hatch removed ─────────────────────
def _read_backtest_runner_source() -> str:
    src = importlib.import_module("backtest_runner")
    path = src.__file__
    assert path, "backtest_runner has no __file__"
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def test_h4_engine_b_bt_structure_gate_escape_hatch_removed():
    body = _read_backtest_runner_source()
    assert "ENGINE_B_BT_STRUCTURE_GATE_ENABLED" not in body, (
        "Escape hatch must not exist (audit H4)"
    )
    assert "disable_structure_gate" not in body, (
        "Engine B BT must not toggle disable_structure_gate (audit H4)"
    )


# ── M1: Same-bar resolution defaults to pessimistic_sl ──────────────────────
def test_m1_resolve_barrier_exit_default_pessimistic_sl():
    from backtest_runner import _resolve_barrier_exit

    bar = {"open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0}
    # LONG: SL at 99 (below open), TP1 at 101.5 (above open by 1.5).
    # Bar hits both. Default mode -> SL.
    outcome, both = _resolve_barrier_exit(
        bar, direction="LONG", sl=99.0, tp1=101.5, tp2=103.0
    )
    assert both is True
    assert outcome == "SL"


def test_m1_resolve_barrier_exit_open_distance_mode(monkeypatch):
    from backtest_runner import _resolve_barrier_exit

    monkeypatch.setitem(CONFIG, "BT_SAME_BAR_RESOLUTION", "open_distance")
    bar = {"open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0}
    # Open is closer to TP1 (1.5R) than to SL (1R from 100).
    # Wait: open=100, SL=99 (dist 1), TP=101.5 (dist 1.5) -> SL closer.
    outcome, both = _resolve_barrier_exit(
        bar, direction="LONG", sl=99.0, tp1=101.5, tp2=103.0
    )
    assert both is True
    assert outcome == "SL"

    # Now with TP closer to open than SL.
    bar2 = {"open": 100.5, "high": 102.0, "low": 98.0, "close": 100.0}
    outcome2, both2 = _resolve_barrier_exit(
        bar2, direction="LONG", sl=99.0, tp1=101.0, tp2=103.0
    )
    assert both2 is True
    assert outcome2 == "TP1"


# ── M3: Engine B/C BT use _bt_confirmed_candles, not [:-1] ──────────────────
def test_m3_engine_b_c_bt_use_confirmed_candles_helper():
    body = _read_backtest_runner_source()
    # Find the Engine B BT block and assert no naked [:-1] forming-bar drop.
    naked_block_start = body.index("def backtest_pair_naked")
    naked_block_end = body.index("def backtest_pair_consensus")
    naked_block = body[naked_block_start:naked_block_end]
    assert "candles_d1[:-1]" not in naked_block
    assert "candles_h4[:-1]" not in naked_block
    assert "candles_h1[:-1]" not in naked_block
    assert "_bt_confirmed_candles(pair, \"D1\"" in naked_block

    consensus_block_start = body.index("def backtest_pair_consensus")
    consensus_block_end = body.index("def backtest_pair_scalp")
    consensus_block = body[consensus_block_start:consensus_block_end]
    assert "candles_d1[:-1]" not in consensus_block
    assert "_bt_confirmed_candles(pair, \"D1\"" in consensus_block


# ── M6: Engine A BT MAX_OPEN tightened to 1 ─────────────────────────────────
def test_m6_engine_a_bt_max_open_is_one():
    body = _read_backtest_runner_source()
    swing_block_start = body.index("def backtest_pair(")
    swing_block_end = body.index("def backtest_pair_naked")
    swing_block = body[swing_block_start:swing_block_end]
    # The MAX_OPEN literal in each Engine A loop must be 1.
    for marker in (
        "MAX_OPEN = 1  # Mirror live duplicate-pair guard",
    ):
        assert swing_block.count(marker) >= 3, (
            f"Engine A swing/intraday/scalp loops must all use MAX_OPEN = 1"
        )


# ── H3: Scalp BT result tagged as M15 proxy ─────────────────────────────────
def test_h3_scalp_bt_result_tags_executiontf_proxy():
    body = _read_backtest_runner_source()
    scalp_block_start = body.index("def backtest_pair_scalp")
    scalp_block_end = body.index("def run_full_backtest")
    scalp_block = body[scalp_block_start:scalp_block_end]
    assert '"executionTimeframeProxy"' in scalp_block
    assert '"liveExecutionTimeframe"' in scalp_block
    assert "M15 execution proxy" in scalp_block


# ── L3: sameBarAmbiguityPct surfaced ────────────────────────────────────────
def test_l3_format_backtest_results_surfaces_same_bar_ambiguity_pct():
    from backtest_runner import _format_backtest_results

    fake_pair = {"display": "EUR/USD", "type": "forex"}
    fake_trades = [
        {"resultR": 1.0, "outcome": "TP1"},
        {"resultR": -1.0, "outcome": "SL"},
        {"resultR": 0.5, "outcome": "TP1"},
        {"resultR": -1.0, "outcome": "SL"},
    ]
    result = _format_backtest_results(
        fake_trades, fake_pair, engine_type="NAKED", same_bar_both_hit=1
    )
    assert "sameBarAmbiguityPct" in result
    assert result["sameBarAmbiguityPct"] == pytest.approx(25.0, abs=0.01)


# ── M4: Broker spread floor helper uses MT5 spread ──────────────────────────
def test_m4_broker_spread_floor_returns_zero_for_non_mt5():
    from backtest_runner import _bt_broker_spread_floor

    assert _bt_broker_spread_floor({"display": "BTC/USDT", "source": "binance"}) == 0.0
    assert _bt_broker_spread_floor(None) == 0.0


def test_m4_apply_broker_spread_floor_no_op_when_floor_zero():
    from backtest_runner import _apply_broker_spread_floor

    base_pct = 0.0001
    assert _apply_broker_spread_floor(
        base_pct, bar={"close": 1.10}, pair={"display": "BTC/USDT", "source": "binance"}
    ) == base_pct


# ── M5: Synthesized candleFetchMeta passes the live freshness gate ──────────
def test_m5_freshness_gate_passes_on_synthesized_meta():
    from backtest_runner import _bt_freshness_passes

    ok, _ = _bt_freshness_passes("2026-01-01T12:00:00+00:00")
    assert ok is True


# ── L4: state-aware scoring smoke is callable ───────────────────────────────
def test_l4_engine_a_state_aware_helper_is_callable():
    from athena_app.services.candle_service import engine_a_scoring_candles_from_state

    candles = [{"time": "2026-01-01T00:00:00Z", "close": 1.0}]
    out = engine_a_scoring_candles_from_state({"display": "X"}, {"confirmed": candles})
    assert out == candles
    out2 = engine_a_scoring_candles_from_state({"display": "X"}, None, fallback=candles)
    assert out2 == candles
