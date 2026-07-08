import sys
from types import SimpleNamespace

import pytest

from ai_context import (
    build_ai_calibration_context,
    build_ai_calibration_context_string,
    build_ai_review_packet,
    build_engine_d_context,
    derive_engine_b_score_pct,
    resolve_ai_review_min_rr,
)
from ai_contracts import AIReviewPacket
from ai_schemas import EngineAResponse
from prompt_store import _strip_frontmatter


@pytest.fixture(autouse=True)
def _unit_config(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "config",
        SimpleNamespace(
            CONFIG={
                "AI_MARKET_INTELLIGENCE_ENABLED": True,
                "AI_MARKET_INTELLIGENCE_TTL_SECONDS": 1800,
                "AI_SIMILAR_SETUPS_ENABLED": False,
                "AI_REVIEW_MIN_RR_BY_STYLE": {
                    "scalp": 1.5,
                    "intraday": 1.5,
                    "swing": 2.0,
                },
            }
        ),
    )


def test_build_engine_d_context_extracts_actual_scalp_payload_keys():
    signal = {
        "pair": "BTCUSDT",
        "type": "crypto",
        "direction": "LONG",
        "zone_type": "mean_reversion",
        "gate_result": "WATCHLIST",
        "executable": False,
        "candidate_status": "rr_below_min",
        "ai_score": 72,
        "ai_grade": "B",
        "ai_reasons": ["at_val", "absorption"],
        "market_state": "balance",
        "session": "London",
        "execution_tf": "M5",
        "structure_tf": "M15",
        "context_tf": "M5",
        "vp_poc": 100.0,
        "vp_vah": 105.0,
        "vp_val": 95.0,
        "vp_lvn_count": 2,
        "vp_volume_source": "binance_aggtrade",
        "vp_fidelity": "real_trade_bucket",
        "vp_is_proxy": False,
        "vp_uses_real_trade_buckets": True,
        "vwap": 101.0,
        "absorption_count": 1,
        "absorption_source": "binance_aggtrade",
        "absorption_fidelity": "real_trade_bucket",
        "absorption_is_proxy": False,
        "cvd_direction": "LONG",
        "cvd_slope": 4.2,
        "cvd_source": "binance_aggtrade",
        "cvd_bucket_count": 10,
        "cvd_fidelity": "real_trade_bucket",
        "cvd_is_proxy": False,
        "aaa_complete": True,
        "aggression_source": "binance_aggtrade",
        "aggression_confirmed": True,
        "aggression_uses_real_order_flow": True,
        "rr1": 1.8,
        "spread_pips": 0.2,
        "fee_guard": {"engine_d_reject_reason": None},
        "sl_method": "atr_m15",
        "sl_distance": 5.0,
        "rr_ok": True,
        "strict_fabio_pass": False,
        "strict_fabio_reason": "missing_aggression",
        "strict_fabio_missing_pillars": ["aggression"],
        "strict_fabio_pillars": {"market_state": True, "location": True, "aggression": False},
    }

    ctx = build_engine_d_context(signal)

    assert ctx["gate_result"] == "WATCHLIST"
    assert ctx["executable"] is False
    assert ctx["quality_score"] == 72
    assert ctx["quality_grade"] == "B"
    assert ctx["vp_uses_real_trade_buckets"] is True
    assert ctx["strict_fabio_pass"] is False
    assert "strict_fabio_pass" not in ctx["missing_fields"]


def test_build_engine_d_context_normalizes_session_object_for_review_packet():
    signal = {
        "pair": "BTCUSDT",
        "type": "crypto",
        "direction": "LONG",
        "engine_d": {
            "session": {
                "name": "New York",
                "quality": "medium",
                "color": "#3b82f6",
            },
            "executable": False,
            "rr_ok": True,
        },
    }

    packet = build_ai_review_packet(signal)

    assert packet["engine_d"]["session"] == "New York"
    AIReviewPacket.model_validate(packet)


def test_build_ai_review_packet_records_missing_fields_and_completeness():
    packet = build_ai_review_packet({"pair": "EUR/USD", "direction": "LONG", "type": "forex"})

    assert packet["symbol"] == "EUR/USD"
    assert packet["engine_a"]["missing_fields"]
    assert packet["engine_d"]["missing_fields"]
    assert "overall_complete" in packet["context_completeness"]
    assert 0.0 <= packet["context_completeness"]["overall_complete"] <= 1.0


def test_engine_a_only_signal_does_not_populate_engine_b_from_root_fields():
    packet = build_ai_review_packet(
        {
            "pair": "EUR/USD",
            "type": "forex",
            "engine_source": "engine_a",
            "direction": "LONG",
            "rr1": 2.0,
            "style": "intraday",
            "engine_a": {"direction": "LONG", "score": 2.1, "max_score": 3.0},
        }
    )

    assert packet["engine_a"]["direction"] == "LONG"
    assert packet["engine_b"]["direction"] is None
    assert packet["engine_b"]["rr"] is None
    assert "direction" in packet["engine_b"]["missing_fields"]


def test_data_freshness_allowed_false_overrides_fresh_text():
    packet = build_ai_review_packet(
        {
            "pair": "BTCUSDT",
            "direction": "LONG",
            "type": "crypto",
            "freshnessStatus": "fresh",
            "dataFreshness": {"allowed": False, "reason": "market_closed"},
        }
    )

    assert packet["data_quality"]["freshness_status"] == "allowed_false:market_closed"


def test_review_packet_carries_explicit_deterministic_trade_blocks():
    packet = build_ai_review_packet(
        {
            "pair": "BTCUSDT",
            "direction": "LONG",
            "type": "crypto",
            "trade": False,
            "advisory_rule_trade_allowed": False,
            "signalClass": "WATCHLIST",
        }
    )

    assert packet["deterministic_gates"]["trade"] is False
    assert packet["deterministic_gates"]["advisory_rule_trade_allowed"] is False
    assert packet["deterministic_gates"]["signal_class"] == "WATCHLIST"


def test_review_packet_fetches_market_intelligence_when_enabled(monkeypatch):
    calls = []

    def fake_market_intelligence(symbol, asset_type):
        calls.append((symbol, asset_type))
        return {
            "schema_version": "market_intelligence.v1",
            "freshness_status": "partial",
            "warnings": ["macro partial"],
            "macro_regime": {"risk_regime": "risk_off"},
        }

    monkeypatch.setattr("market_intelligence.get_market_intelligence", fake_market_intelligence)

    packet = build_ai_review_packet({"pair": "GBP/USD", "direction": "SHORT", "type": "forex"})

    assert calls == [("GBP/USD", "forex")]
    assert packet["market_intelligence"]["freshness_status"] == "partial"
    assert packet["market_intelligence"]["macro_regime"]["risk_regime"] == "risk_off"


def test_review_packet_preserves_explicit_market_intelligence_without_refetch(monkeypatch):
    def fail_fetch(_symbol, _asset_type):
        raise AssertionError("explicit market_intelligence should not be refetched")

    monkeypatch.setattr("market_intelligence.get_market_intelligence", fail_fetch)

    packet = build_ai_review_packet(
        {
            "pair": "BTCUSDT",
            "direction": "LONG",
            "type": "crypto",
            "market_intelligence": {
                "schema_version": "market_intelligence.v1",
                "freshness_status": "fresh",
                "warnings": [],
                "pair_context": {"symbol": "BTCUSDT"},
            },
        }
    )

    assert packet["market_intelligence"]["freshness_status"] == "fresh"
    assert packet["market_intelligence"]["pair_context"]["symbol"] == "BTCUSDT"


def test_existing_ai_calibration_context_still_builds():
    ctx = build_ai_calibration_context(
        {"pair": "EUR/USD", "symbol": "EURUSD", "type": "forex", "confluenceScore": 1.2, "maxScore": 3.0},
        "engine_a",
    )

    assert ctx["identity"]["pair"] == "EUR/USD"
    assert ctx["engine_a"]["confluenceScore"] == 1.2


def test_deterministic_gate_context_exposes_engine_a_trade_gate_fields():
    packet = build_ai_review_packet(
        {
            "pair": "EUR/USD",
            "direction": "LONG",
            "type": "forex",
            "engineATradeEnabled": False,
            "engineATradeGate": {
                "enabled": False,
                "research_only": True,
                "source": "class:forex",
                "reason": "Engine A research-only for EUR/USD",
            },
        }
    )

    gates = packet["deterministic_gates"]
    assert gates["engine_a_trade_enabled"] is False
    assert gates["engine_a_trade_gate"]["source"] == "class:forex"
    assert gates["engine_a_trade_gate_reason"] == "Engine A research-only for EUR/USD"


def test_calibration_context_string_injects_style_min_rr():
    signal = {
        "pair": "BTCUSDT",
        "symbol": "BTCUSDT",
        "type": "crypto",
        "style": "swing",
        "confluenceScore": 2.1,
        "maxScore": 3.0,
        "price": 52855,
        "sl": 52114,
        "tp1": 54100,
        "tp2": 54500,
        "rr1": 1.67,
        "rr2": 2.67,
    }
    text = build_ai_calibration_context_string(signal, "Engine A factor/confluence signal", "swing")

    assert "Style min RR (config): 2.0" in text
    assert "TP1/RR1 is partial exit target" in text


def test_calibration_context_string_injects_engine_c_consensus_fields():
    signal = {
        "pair": "DOGE/USDT",
        "symbol": "DOGEUSDT",
        "type": "crypto",
        "style": "intraday",
        "engine_source": "engine_c",
        "direction": "LONG",
        "price": 0.07915,
        "sl": 0.077086,
        "tp1": 0.08334,
        "tp2": 0.08334,
        "rr1": 2.03,
        "rr2": 2.03,
        "confluenceScore": 0.78,
        "maxScore": 1.0,
        "engine_c": {
            "decision_state": "execute",
            "conviction": 0.78,
            "tier": "HIGH",
            "sizing_override": 1.0,
            "components": {
                "a_norm": 0.86,
                "b_norm": 0.5,
                "a_has_signal": True,
                "b_has_signal": True,
            },
        },
    }

    text = build_ai_calibration_context_string(signal, "Engine C consensus", "intraday")

    assert "Engine source: Engine C consensus" in text
    assert "Engine C decision_state: execute" in text
    assert "Engine C tier: HIGH" in text
    assert "Engine C sizing_override: 1.0" in text
    assert "Engine C components: a_norm=0.86 b_norm=0.5" in text


def test_derive_engine_b_score_pct_prefers_gate_pct():
    assert derive_engine_b_score_pct({"gate_pct": 100, "score": 5.0, "max_possible": 11.5}) == 100.0


def test_derive_engine_b_score_pct_recomputes_stale_zero_percent():
    assert derive_engine_b_score_pct({"score": 4.75, "max_possible": 9.5, "score_pct": 0.0}) == 50.0


def test_calibration_context_string_injects_engine_b_native_diagnostics():
    signal = {
        "pair": "GBP/JPY",
        "symbol": "GBPJPY",
        "type": "forex",
        "style": "intraday",
        "engine_source": "engine_b",
        "is_naked": True,
        "direction": "LONG",
        "price": 216.781,
        "sl": 215.3986,
        "tp1": 221.4372,
        "tp2": 221.4372,
        "rr1": 3.37,
        "rr2": 3.37,
        "confluenceScore": 6.0,
        "maxScore": 8.0,
        "naked_data": {
            "structural_verdict": "CLEAR",
            "current_swing_sequence": "HH_HL",
            "macro_swing_sequence": "HH_HL",
            "score": 6.0,
            "max_possible": 8.0,
            "engine_b_canonical_actionable": False,
            "engine_b_canonical_status": "REJECT_NO_ROOM",
            "engine_b_rejection_reasons": ["ROOM_OK_FALSE"],
            "is_actionable": False,
            "passed": True,
            "rr_used_for_gate": 3.37,
            "room_ok": False,
            "d1_adx": 22.4,
            "h4_adx": 28.7,
            "_adx_derived_regime": "NORMAL",
            "engine_b_diagnostics": {
                "reason_codes": [
                    "bos_without_volume",
                    "resistance_too_close",
                    "structural_tp_too_close",
                ]
            },
        },
    }

    text = build_ai_calibration_context_string(
        signal,
        "Engine B naked market structure",
        "intraday",
    )

    assert "Engine B structural verdict: CLEAR" in text
    assert "Engine B swing sequence: HH_HL | Macro: HH_HL" in text
    assert "Engine B score: 6.0 / 8.0 (75.0%)" in text
    assert "Engine B actionable: False | RR gate: 3.37 | room_ok: False" in text
    assert "Engine B canonical status: REJECT_NO_ROOM" in text


def test_calibration_context_uses_canonical_not_passed_fallback():
    signal = {
        "pair": "EUR/JPY",
        "symbol": "EURJPY",
        "type": "forex",
        "style": "intraday",
        "engine_source": "engine_b",
        "is_naked": True,
        "naked_data": {
            "passed": True,
            "checklist_passed": True,
            "engine_b_canonical_actionable": False,
            "engine_b_canonical_status": "REJECT_CONFLICT",
            "engine_b_rejection_reasons": ["ACTIONABILITY_CONFLICT_ENGINE_B_NO_AI_TRUE"],
            "ai_calibration_actionable_raw": True,
            "rr_used_for_gate": 1.8,
            "room_ok": False,
        },
    }
    ctx = build_ai_calibration_context(signal, "Engine B naked market structure", "intraday")
    assert ctx["engine_b"]["is_actionable"] is False
    text = build_ai_calibration_context_string(signal, "Engine B naked market structure", "intraday")
    assert "Engine B actionable: False" in text
    assert "ACTIONABILITY_CONFLICT_ENGINE_B_NO_AI_TRUE" in text


def test_resolve_ai_review_min_rr_prefers_signal_min_rr():
    signal = {"min_rr": 1.8, "style": "intraday"}
    assert resolve_ai_review_min_rr(signal, "intraday") == 1.8


def _read_repo_prompt(relative_path: str) -> str:
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / relative_path
    return _strip_frontmatter(path.read_text(encoding="utf-8"))


def test_marcus_prompt_uses_config_rr_not_hardcoded_swing_three():
    prompt = _read_repo_prompt("prompts/marcus_v6.md")
    assert "RR >= 3.0" not in prompt
    assert "Style min RR (config)" in prompt
    assert "levelsVerdict" in prompt
    assert "ENGINE C CONSENSUS" in prompt
    assert "sizing_override/conviction for Engine C" in prompt


def test_engine_b_prompt_prefix_uses_config_rr_not_hardcoded_swing_three():
    prompt = _read_repo_prompt("prompts/engine_b_v2.md")
    assert "RR >= 3.0" not in prompt
    assert "Style min RR (config)" in prompt


def test_engine_a_response_parses_with_and_without_levels_fields():
    with_levels = EngineAResponse.model_validate(
        {
            "symbol": "BTCUSDT",
            "timeframe": "H4",
            "bias": "long",
            "setup_type": "breakout_retest",
            "trend_score": 18,
            "structure_score": 17,
            "momentum_score": 13,
            "liquidity_score": 8,
            "risk_score": 12,
            "confirmation_score": 14,
            "total_score": 84,
            "grade": "B",
            "ai_action": "needs_confirmation",
            "blocking_reasons": [],
            "reason": "Aligned structure.",
            "verdict": "LONG with trend factor support.",
            "narrative": "trend_term strong.",
            "entryZone": "52855",
            "invalidation": "52114",
            "keyLevels": "Sup 49565",
            "levelsVerdict": "accept",
            "levelsReason": "SL below nearest support zone.",
            "suggestedSL": None,
            "suggestedTP": None,
            "positionSizing": "Half",
            "tradeStyle": "SWING",
            "tradeStyleReason": "D1 trend aligned",
            "warnings": [],
            "edgeProbability": 68,
            "riskLevel": "Medium",
        }
    )
    assert with_levels.levelsVerdict == "accept"

    without_levels = EngineAResponse.model_validate(
        {
            "symbol": "BTCUSDT",
            "timeframe": "H4",
            "bias": "long",
            "setup_type": "breakout_retest",
            "trend_score": 18,
            "structure_score": 17,
            "momentum_score": 13,
            "liquidity_score": 8,
            "risk_score": 12,
            "confirmation_score": 14,
            "total_score": 84,
            "grade": "B",
            "ai_action": "needs_confirmation",
            "reason": "Aligned structure.",
            "verdict": "LONG with trend factor support.",
            "narrative": "trend_term strong.",
            "entryZone": "52855",
            "invalidation": "52114",
            "keyLevels": "Sup 49565",
            "positionSizing": "Half",
            "tradeStyle": "SWING",
            "tradeStyleReason": "D1 trend aligned",
            "warnings": [],
            "edgeProbability": 68,
            "riskLevel": "Medium",
        }
    )
    assert without_levels.levelsVerdict is None
