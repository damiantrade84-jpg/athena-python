import sys
from types import SimpleNamespace

import pytest

from ai_context import (
    build_ai_calibration_context,
    build_ai_review_packet,
    build_engine_d_context,
)
from ai_contracts import AIReviewPacket


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
