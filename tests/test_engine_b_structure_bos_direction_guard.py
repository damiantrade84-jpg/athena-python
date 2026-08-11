"""Fail-closed structure-TF BOS direction agreement for Engine B.

Regression: lagging HH_HL sequence must not pass LONG when structure BOS is
exclusively bearish (AUDNZD/EURNZD BUY-on-SELL class of bug).
"""

from __future__ import annotations

import config
from market_structure import (
    ENGINE_B_REASON_STRUCTURE_BOS_DIRECTION_CONFLICT,
    NakedEngine,
)


def _base_res(**overrides):
    res = {
        "atr": 1.0,
        "asset_type": "forex",
        "current_swing_sequence": "HH_HL",
        "macro_swing_sequence": "HH_HL",
        "bos_confirmed": False,
        "bos_mtf_confirmed": False,
        "bos_data": {"bos_bull": False, "bos_bear": True},
        "liquidity_sweep": False,
        "sweep_direction": None,
        "choch_confirmed": False,
        "zone_touched": True,
        "near_active_zone": True,
        "trigger_ok": True,
        "bos_volume_confirmed": True,
        "strong_close": True,
        "inside_break_candle": False,
        "engulfing_candle": False,
        "ob_at_zone": False,
        "breaker_block": False,
        "distance_to_res": 5.0,
        "distance_to_sup": 5.0,
        "recommended_stop_loss": 99.0,
        "recommended_take_profit": 105.0,
    }
    res.update(overrides)
    return res


def _style(**overrides):
    style = {
        "style": "intraday",
        "min_score": 4.0,
        "min_room_atr": 0.35,
        "min_rr": 1.0,
        "fallback_rr": 2.0,
        "require_macro_align": False,
    }
    style.update(overrides)
    return style


def test_long_blocked_when_structure_bos_exclusively_bearish(monkeypatch):
    """AUDNZD-class: HH_HL sequence + exclusive bos_bear must fail LONG structure."""
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_BOS_DIRECTION_GUARD_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})
    out = NakedEngine().calculate_confidence(
        _base_res(),
        100.0,
        "LONG",
        style_profile=_style(),
    )
    assert out["structure_ok"] is False
    assert out["structure_bos_direction_conflict"] is True
    assert out["structure_direction_sources"] == []
    assert out["structure_bos_opposes"] is True
    diag = out.get("engine_b_diagnostics") or {}
    assert ENGINE_B_REASON_STRUCTURE_BOS_DIRECTION_CONFLICT in (
        diag.get("reason_codes") or []
    )


def test_short_blocked_when_structure_bos_exclusively_bullish(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_BOS_DIRECTION_GUARD_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})
    out = NakedEngine().calculate_confidence(
        _base_res(
            current_swing_sequence="LH_LL",
            macro_swing_sequence="LH_LL",
            bos_data={"bos_bull": True, "bos_bear": False},
            recommended_stop_loss=101.0,
            recommended_take_profit=95.0,
        ),
        100.0,
        "SHORT",
        style_profile=_style(),
    )
    assert out["structure_ok"] is False
    assert out["structure_bos_direction_conflict"] is True


def test_long_allowed_with_aligned_bos(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_BOS_DIRECTION_GUARD_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})
    out = NakedEngine().calculate_confidence(
        _base_res(
            bos_confirmed=True,
            bos_data={"bos_bull": True, "bos_bear": False},
        ),
        100.0,
        "LONG",
        style_profile=_style(),
    )
    assert out["structure_ok"] is True
    assert out["structure_bos_direction_conflict"] is False


def test_choch_reversal_escape_allows_counter_bos(monkeypatch):
    """Aligned CHoCH may pass against exclusive opposing BOS (true reversal)."""
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_BOS_DIRECTION_GUARD_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})
    out = NakedEngine().calculate_confidence(
        _base_res(choch_confirmed=True),
        100.0,
        "LONG",
        style_profile=_style(),
    )
    assert out["structure_ok"] is True
    assert out["structure_bos_direction_conflict"] is False
    assert out["structure_bos_opposes"] is True


def test_guard_disabled_restores_sequence_pass(monkeypatch):
    """Legacy: with sequence direction ON and BOS guard OFF, HH_HL can pass."""
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_BOS_DIRECTION_GUARD_ENABLED", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_REQUIRE_ALIGN_OR_BOS_MTF", False)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})
    out = NakedEngine().calculate_confidence(
        _base_res(),
        100.0,
        "LONG",
        style_profile=_style(),
    )
    assert out["structure_ok"] is True
    assert out["structure_bos_direction_conflict"] is False


def test_guard_not_bypassed_by_disable_structure_gate(monkeypatch):
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_BOS_DIRECTION_GUARD_ENABLED", True)
    monkeypatch.setitem(config.CONFIG, "ENGINE_B_WEIGHTED_SCORING", {"ENABLED": False})
    out = NakedEngine().calculate_confidence(
        _base_res(),
        100.0,
        "LONG",
        style_profile=_style(disable_structure_gate=True),
    )
    assert out["structure_ok"] is False
    assert out["structure_bos_direction_conflict"] is True


def test_snapshot_selects_structure_bos_side_not_counter_or_blank(monkeypatch):
    """Exclusive bear BOS: show SHORT (even if gates pending), never LONG/blank."""
    from engine_b_snapshot import evaluate_engine_b_snapshot

    monkeypatch.setitem(config.CONFIG, "ENGINE_B_STRUCTURE_BOS_DIRECTION_GUARD_ENABLED", True)

    class _FakeEngine:
        def precompute_structure_data(self, *a, **k):
            return {"_ok": True, "atr": 1.0}

        def analyze_structure_direction(self, precompute, current_price, direction):
            return {
                "structural_verdict": "CLEAR",
                "atr": 1.0,
                "asset_type": "forex",
                "current_swing_sequence": "HH_HL",
                "macro_swing_sequence": "HH_HL",
                "bos_confirmed": direction == "SHORT",
                "bos_data": {"bos_bull": False, "bos_bear": True},
                "bos_mtf_confirmed": False,
                "choch_confirmed": False,
                "liquidity_sweep": False,
                "zone_touched": True,
                "near_active_zone": True,
                "trigger_ok": True,
                "recommended_stop_loss": 99.0 if direction == "LONG" else 101.0,
                "recommended_take_profit": 105.0 if direction == "LONG" else 95.0,
                "distance_to_res": 5.0,
                "distance_to_sup": 5.0,
            }

        def calculate_confidence(self, res, current_price, direction, **kwargs):
            # LONG fails structure (BOS guard path); SHORT fails other gates.
            # Score LONG higher so the old fallback would wrongly pick LONG.
            if direction == "LONG":
                return {
                    "score": 5.0,
                    "passed": False,
                    "structure_ok": False,
                    "reason": "structure_bos_direction_conflict",
                    "lifecycle_state": "forming",
                }
            return {
                "score": 2.0,
                "passed": False,
                "structure_ok": True,
                "reason": "location_failed",
                "lifecycle_state": "forming",
            }

    pair = {"display": "AUD/NZD", "symbol": "AUDNZD", "type": "forex"}
    role = {
        "D1": [{"close": 1.0, "high": 1.1, "low": 0.9, "open": 1.0, "vol": 1}],
        "H4": [{"close": 1.0, "high": 1.1, "low": 0.9, "open": 1.0, "vol": 1}],
        "H1": [{"close": 1.0, "high": 1.1, "low": 0.9, "open": 1.0, "vol": 1}],
        "M15": [{"close": 1.0, "high": 1.1, "low": 0.9, "open": 1.0, "vol": 1}],
    }
    from unittest.mock import patch

    with patch(
        "engine_b_snapshot.resolve_engine_b_style_profile",
        return_value=(
            "intraday",
            {
                "style": "intraday",
                "entry_tf": "M15",
                "structure_tf": "H4",
                "fallback_rr": 2.0,
                "min_score": 3.0,
            },
        ),
    ):
        result = evaluate_engine_b_snapshot(
            pair,
            role,
            current_price=1.0,
            style="intraday",
            score_group="forex_crosses",
            style_profile={
                "style": "intraday",
                "entry_tf": "M15",
                "structure_tf": "H4",
                "fallback_rr": 2.0,
                "min_score": 3.0,
            },
            atr_override=0.01,
            regime_label="TRENDING",
            engine=_FakeEngine(),
            context_mode="historical",
            gate_fn=lambda conf, *a, **k: (bool(conf.get("passed")), 3.0),
        )
    assert result.selected is not None
    assert result.selected["direction"] == "SHORT"
    assert result.selected["passed"] is False
    assert result.rejection_reason == "structure_direction_gates_pending"


def test_independent_direction_follows_exclusive_bos():
    """Lagging HH_HL must not keep independent direction LONG vs exclusive bear BOS."""
    out = NakedEngine()._determine_independent_direction(
        "HH_HL",
        "HH_HL",
        {"bos_bull": False, "bos_bear": True},
        {},
        {},
        {},
    )
    assert out["direction"] == "SHORT"
    assert out["confidence"] == "HIGH"


def test_snapshot_surfaces_dual_clear_side_with_gates_not_blank(monkeypatch):
    """Dual CLEAR without exclusive BOS still returns a diagnostic direction + fails."""
    from engine_b_snapshot import evaluate_engine_b_snapshot
    from unittest.mock import patch

    class _FakeEngine:
        def precompute_structure_data(self, *a, **k):
            return {"_ok": True, "atr": 1.0}

        def analyze_structure_direction(self, precompute, current_price, direction):
            return {
                "structural_verdict": "CLEAR",
                "atr": 1.0,
                "asset_type": "crypto",
                "bos_confirmed": False,
                "bos_data": {"bos_bull": True, "bos_bear": True},
                "choch_confirmed": False,
                "liquidity_sweep": False,
                "zone_touched": False,
                "near_active_zone": False,
                "trigger_ok": False,
                "recommended_stop_loss": 99.0 if direction == "LONG" else 101.0,
                "recommended_take_profit": 105.0 if direction == "LONG" else 95.0,
                "distance_to_res": 1.0,
                "distance_to_sup": 1.0,
            }

        def calculate_confidence(self, res, current_price, direction, **kwargs):
            return {
                "score": 4.0 if direction == "LONG" else 3.0,
                "passed": False,
                "structure_ok": True,
                "location_ok": False,
                "entry_ok": False,
                "failed_gate_names": ["loc", "trigger"],
                "lifecycle_state": "forming",
            }

    pair = {"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"}
    role = {
        tf: [{"close": 1.0, "high": 1.1, "low": 0.9, "open": 1.0, "vol": 1}]
        for tf in ("D1", "H4", "H1", "M15", "M30")
    }
    with patch(
        "engine_b_snapshot.resolve_engine_b_style_profile",
        return_value=(
            "intraday",
            {
                "style": "intraday",
                "entry_tf": "M15",
                "structure_tf": "H4",
                "fallback_rr": 2.0,
                "min_score": 3.0,
            },
        ),
    ):
        result = evaluate_engine_b_snapshot(
            pair,
            role,
            current_price=1.0,
            style="intraday",
            score_group="crypto_btc",
            style_profile={
                "style": "intraday",
                "entry_tf": "M15",
                "structure_tf": "H4",
                "fallback_rr": 2.0,
                "min_score": 3.0,
            },
            atr_override=0.01,
            regime_label="TRENDING",
            engine=_FakeEngine(),
            context_mode="historical",
            gate_fn=lambda conf, *a, **k: (bool(conf.get("passed")), 3.0),
        )
    assert result.selected is not None
    assert result.selected["direction"] == "LONG"
    assert result.selected["passed"] is False
    assert result.rejection_reason == "structure_direction_gates_pending"
    fails = (result.selected.get("confidence") or {}).get("failed_gate_names") or []
    assert "loc" in fails
    # The direction label is a selector status, never a failed gate name.
    assert "structure_direction_ambiguous" not in fails
    assert (
        result.selected["confidence"]["structure_direction_status"]
        == "structure_direction_ambiguous"
    )


def test_snapshot_uses_unique_choch_side_without_false_ambiguity(monkeypatch):
    """A score-floor failure must not relabel unique CHoCH as ambiguous."""
    from engine_b_snapshot import evaluate_engine_b_snapshot
    from unittest.mock import patch

    class _FakeEngine:
        def precompute_structure_data(self, *a, **k):
            return {"_ok": True, "atr": 1.0}

        def analyze_structure_direction(self, precompute, current_price, direction):
            return {
                "structural_verdict": "CLEAR",
                "atr": 1.0,
                "asset_type": "forex",
                "bos_confirmed": False,
                "bos_data": {"bos_bull": False, "bos_bear": False},
                "choch_confirmed": direction == "SHORT",
                "choch_data": {"choch_bull": False, "choch_bear": True},
                "liquidity_sweep": False,
                "recommended_stop_loss": 99.0 if direction == "LONG" else 101.0,
                "recommended_take_profit": 105.0 if direction == "LONG" else 95.0,
            }

        def calculate_confidence(self, res, current_price, direction, **kwargs):
            if direction == "SHORT":
                return {
                    "score": 4.8,
                    "passed": False,
                    "structure_ok": True,
                    "failed_gate_names": ["min_score<5.00"],
                    "lifecycle_state": "forming",
                }
            return {
                "score": 4.9,
                "passed": False,
                "structure_ok": False,
                "failed_gate_names": ["struct", "min_score<5.00"],
                "lifecycle_state": "forming",
            }

    pair = {"display": "EUR/CAD", "symbol": "EURCAD", "type": "forex"}
    role = {
        tf: [{"close": 1.0, "high": 1.1, "low": 0.9, "open": 1.0, "vol": 1}]
        for tf in ("D1", "H4", "H1", "M15")
    }
    with patch(
        "engine_b_snapshot.resolve_engine_b_style_profile",
        return_value=(
            "intraday",
            {
                "style": "intraday",
                "entry_tf": "M15",
                "structure_tf": "H4",
                "fallback_rr": 2.0,
                "min_score": 5.0,
            },
        ),
    ):
        result = evaluate_engine_b_snapshot(
            pair,
            role,
            current_price=1.0,
            style="intraday",
            score_group="forex_crosses",
            atr_override=0.01,
            regime_label="TRENDING",
            engine=_FakeEngine(),
            context_mode="historical",
            gate_fn=lambda conf, *a, **k: (bool(conf.get("passed")), 5.0),
        )

    assert result.selected is not None
    assert result.selected["direction"] == "SHORT"
    fails = (result.selected.get("confidence") or {}).get("failed_gate_names") or []
    assert "min_score<5.00" in fails
    assert "structure_direction_ambiguous" not in fails
    assert "structure_direction_unresolved" not in fails
    assert result.selected["confidence"]["structure_direction_status"] == "resolved"


def test_snapshot_labels_no_directional_structure_as_unresolved(monkeypatch):
    """Dual analysis success without structure evidence is unresolved, not ambiguous."""
    from engine_b_snapshot import evaluate_engine_b_snapshot
    from unittest.mock import patch

    class _FakeEngine:
        def precompute_structure_data(self, *a, **k):
            return {"_ok": True, "atr": 1.0}

        def analyze_structure_direction(self, precompute, current_price, direction):
            return {
                "structural_verdict": "CLEAR",
                "atr": 1.0,
                "asset_type": "crypto",
                "bos_confirmed": False,
                "bos_data": {"bos_bull": False, "bos_bear": False},
                "choch_confirmed": False,
                "liquidity_sweep": False,
                "recommended_stop_loss": 99.0 if direction == "LONG" else 101.0,
                "recommended_take_profit": 105.0 if direction == "LONG" else 95.0,
            }

        def calculate_confidence(self, res, current_price, direction, **kwargs):
            return {
                "score": 4.0 if direction == "LONG" else 3.0,
                "passed": False,
                "structure_ok": False,
                "failed_gate_names": ["struct"],
                "lifecycle_state": "forming",
            }

    pair = {"display": "ALT/USDT", "symbol": "ALTUSDT", "type": "crypto"}
    role = {
        tf: [{"close": 1.0, "high": 1.1, "low": 0.9, "open": 1.0, "vol": 1}]
        for tf in ("D1", "H4", "H1", "M15")
    }
    with patch(
        "engine_b_snapshot.resolve_engine_b_style_profile",
        return_value=(
            "intraday",
            {
                "style": "intraday",
                "entry_tf": "M15",
                "structure_tf": "H4",
                "fallback_rr": 2.0,
                "min_score": 3.0,
            },
        ),
    ):
        result = evaluate_engine_b_snapshot(
            pair,
            role,
            current_price=1.0,
            style="intraday",
            score_group="crypto_other",
            atr_override=0.01,
            regime_label="TRENDING",
            engine=_FakeEngine(),
            context_mode="historical",
            gate_fn=lambda conf, *a, **k: (bool(conf.get("passed")), 3.0),
        )

    assert result.selected is not None
    fails = (result.selected.get("confidence") or {}).get("failed_gate_names") or []
    assert "structure_direction_unresolved" not in fails
    assert "structure_direction_ambiguous" not in fails
    assert (
        result.selected["confidence"]["structure_direction_status"]
        == "structure_direction_unresolved"
    )
