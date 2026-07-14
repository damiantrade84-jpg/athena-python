"""Tests for auto_trader.py — covers both the gate logic and the execution path.

Critical path coverage:
  - _can_execute gates (conviction, regime, sessions, SL pre-check, conductor, sentiment, event risk, debate)
  - _execute_signal (broker connect, risk_check, guardian, managed execution, audit)
  - _write_audit / _write_error SQLite persistence
  - _get_cached_open_positions error handling
  - _run_auto_scan scan lifecycle
  - Module-level helper functions
"""

import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import ANY

import pytest

from auto_trader import (
    AutoTrader,
    _a_only_reject_reason,
    _auto_trade_min_conviction,
    _auto_trade_min_conviction_config,
    _current_combined_conviction,
    _normalize_pair_key,
    _signal_engine,
    _signal_expected_prob,
    _signal_matches_position,
)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _base_cfg():
    return {
        "AUTO_TRADE_MIN_SCORE": {"crypto": 1.5, "forex": 1.6},
        "AUTO_TRADE_MIN_CONVICTION": {"default": 0.55},
        "AUTO_TRADE_MAX_DAILY": 3,
        "AUTO_TRADE_SCHEDULER_MODE": "interval",
        "EXECUTION_ENABLED": True,
        "MT5_EXECUTION_ENABLED": True,
        "BYBIT_EXECUTION_ENABLED": True,
        "AUTO_TRADE_SESSIONS": {"crypto": ["always"]},
        "AUTO_TRADE_BLOCKED_TREND_STATES": {"default": [], "crypto": []},
        "AUTO_TRADE_BLOCKED_REGIMES": {"default": [], "crypto": []},
        "SENTIMENT_GATE_ENABLED": False,
        "EVENT_RISK_ENABLED": False,
        "SIGNAL_DEBATE_ENABLED": False,
        "ENGINE_A_TRADE_ELIGIBILITY_ENABLED": False,
    }


def _passing_signal(**overrides):
    """Return a minimal signal that should pass _can_execute.

    Defaults represent an Engine A signal; tests can override ``scoreNorm``
    (Engine A's pure conviction proxy) to drive the conviction gate. The
    legacy ``combinedConviction`` field is also set so Engine C/B test
    overrides keep working when they flip ``engine``.
    """
    base = {
        "pair": "BTC/USDT",
        "type": "crypto",
        "direction": "LONG",
        "trendState": "TRENDING",
        "regimeName": "TRENDING",
        "scoreNorm": 0.90,
        "combinedConviction": 0.90,
        "confluenceScore": 2.7,
        "maxScore": 3.0,
        "price": 50000.0,
        "sl": 49000.0,
        "tp1": 52000.0,
    }
    base.update(overrides)
    return base


def _temp_audit_db():
    """Create a temporary audit DB with the expected schema."""
    tmp = tempfile.mktemp(suffix=".db")
    con = sqlite3.connect(tmp, timeout=15.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS audit_log ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "ts TEXT, pair TEXT, score REAL, engine TEXT, direction TEXT, "
        "asset_class TEXT, regime TEXT, entry_price REAL, sl REAL, tp REAL, "
        "volume REAL, risk_amount REAL, risk_pct REAL, ticket TEXT, "
        "grade TEXT, signal_price_ref REAL, slippage_bps REAL, "
        "max_score REAL, score_pct REAL, factors_json TEXT, "
        "edge_prob REAL, style TEXT, fee_cost REAL, error_tag TEXT, exit_mode TEXT"
        ")"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS execution_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "ts TEXT, engine TEXT, success INTEGER, score REAL, "
        "max_score REAL, expected_prob REAL, slippage_bps REAL, "
        "meta_json TEXT"
        ")"
    )
    con.commit()
    con.close()
    return tmp


# ═══════════════════════════════════════════════════════════════════════════
# Module-level helpers
# ═══════════════════════════════════════════════════════════════════════════

class TestSignalEngine:
    def test_engine_scalp(self):
        assert _signal_engine({"engine": "scalp"}) == "scalp"
        assert _signal_engine({"engine": "Scalp"}) == "scalp"

    def test_engine_consensus(self):
        assert _signal_engine({"engine": "engine_c"}) == "engine_c"
        assert _signal_engine({"engine": "consensus"}) == "engine_c"

    def test_engine_fallback_to_a(self):
        assert _signal_engine({"engine": "engine_a"}) == "engine_a"
        assert _signal_engine({}) == "engine_a"
        assert _signal_engine({"engine": "unknown"}) == "engine_a"


class TestSignalExpectedProb:
    def test_prefers_calibrated_probability(self):
        assert _signal_expected_prob({"calibratedProbability": 0.41}) == 0.41

    def test_falls_back_to_combined_conviction(self):
        assert _signal_expected_prob({"combinedConviction": 0.72}) == 0.72

    def test_falls_back_to_score_norm(self):
        assert _signal_expected_prob({"scoreNorm": 0.85}) == 0.85

    def test_clamps_to_0_1(self):
        assert _signal_expected_prob({"calibratedProbability": 1.5}) == 1.0
        assert _signal_expected_prob({"calibratedProbability": -0.5}) == 0.0

    def test_falls_back_to_confluence_ratio(self):
        assert _signal_expected_prob({"confluenceScore": 2.0, "maxScore": 4.0}) == 0.5

    def test_confluence_score_direct_when_0_1_scale(self):
        assert _signal_expected_prob({"confluenceScore": 0.7, "maxScore": 0}) == 0.7


class TestCurrentCombinedConviction:
    def test_a_only_weight_when_not_aligned(self):
        s = {"confluenceScore": 2.0, "maxScore": 3.0, "enginesAligned": False}
        assert _current_combined_conviction(s) == pytest.approx(0.4, abs=1e-4)

    def test_a_plus_b_weight_when_aligned(self):
        s = {
            "confluenceScore": 2.0, "maxScore": 3.0,
            "enginesAligned": True,
            "engine_b_score": 4.0, "engine_b_max": 5.0,
        }
        result = _current_combined_conviction(s)
        # A_norm = 2/3 = 0.6667 * 0.6 = 0.4
        # B_norm = 4/5 = 0.8 * 0.4 = 0.32
        # total = 0.72
        assert result == pytest.approx(0.72, abs=1e-4)

    def test_zero_when_no_scores(self):
        assert _current_combined_conviction({}) == 0.0

    def test_handles_none_scores(self):
        s = {"confluenceScore": None, "maxScore": None}
        assert _current_combined_conviction(s) == 0.0


class TestAOnlyRejectReason:
    def test_returns_none_when_aligned(self):
        assert _a_only_reject_reason({"enginesAligned": True}, 0.5, 0.5) is None

    def test_returns_none_when_not_a_only(self):
        assert _a_only_reject_reason({"enginesAligned": False}, 0.9, 0.5) is None

    def test_returns_reason_when_a_only_below_min(self):
        reason = _a_only_reject_reason(
            {"confluenceScore": 2.38, "maxScore": 3.0, "enginesAligned": False},
            0.476, 0.50,
        )
        assert reason is not None
        assert "A-only conviction 0.476 < min 0.500" in reason
        assert "Engine A score 2.38/3.0" in reason


class TestNormalizePairKey:
    def test_removes_non_alphanumeric(self):
        assert _normalize_pair_key("BTC/USDT") == "BTCUSDT"
        assert _normalize_pair_key("eur-usd") == "EURUSD"

    def test_empty_on_none(self):
        assert _normalize_pair_key(None) == ""


class TestSignalMatchesPosition:
    def test_matches_by_pair(self):
        sig = {"pair": "BTC/USDT"}
        pos = {"pair": "BTC/USDT"}
        assert _signal_matches_position(sig, pos) is True

    def test_matches_by_symbol(self):
        sig = {"symbol": "BTCUSDT"}
        pos = {"symbol": "BTC/USDT"}
        assert _signal_matches_position(sig, pos) is True

    def test_no_match(self):
        sig = {"pair": "ETH/USDT"}
        pos = {"pair": "BTC/USDT"}
        assert _signal_matches_position(sig, pos) is False

    def test_no_match_on_empty(self):
        assert _signal_matches_position({}, {}) is False


class TestAutoTradeMinConvictionConfig:
    def test_dict_normalized(self):
        cfg = {"AUTO_TRADE_MIN_CONVICTION": {"default": 0.5, "crypto": 0.7}}
        result = _auto_trade_min_conviction_config(cfg)
        assert result == {"default": 0.5, "crypto": 0.7}

    def test_scalar_converted(self):
        cfg = {"AUTO_TRADE_MIN_CONVICTION": 0.6}
        result = _auto_trade_min_conviction_config(cfg)
        assert result == {"default": 0.6}

    def test_missing_default_added(self):
        cfg = {"AUTO_TRADE_MIN_CONVICTION": {"crypto": 0.7}}
        result = _auto_trade_min_conviction_config(cfg)
        assert result["default"] == 0.5

    def test_fallback_when_empty(self):
        result = _auto_trade_min_conviction_config({})
        assert result == {"default": 0.5}


# ═══════════════════════════════════════════════════════════════════════════
# _can_execute — gate tests
# ═══════════════════════════════════════════════════════════════════════════

class TestTimeframePolicyExecutionGate:
    def test_config_conflict_fails_closed_even_in_shadow(self):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg.update({"TF_POLICY_MODE": "shadow", "TF_POLICY_AUTOTRADE_ENABLED": False})
        sig = _passing_signal(policySource="CONFIG_CONFLICT", entryReadiness="CONFIG_ERROR")

        ok, reason = trader._can_execute(sig, cfg)

        assert not ok
        assert reason == "TF_POLICY_CONFIG_CONFLICT"

    def test_enforced_mode_requires_separate_autotrade_enablement(self):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg.update({"TF_POLICY_MODE": "enforced", "TF_POLICY_AUTOTRADE_ENABLED": False})

        ok, reason = trader._can_execute(_passing_signal(), cfg)

        assert not ok
        assert reason == "TF_POLICY_AUTOTRADE_DISABLED"


class TestCanExecuteConvictionGate:
    def test_rejects_below_min_conviction(self):
        trader = AutoTrader()
        sig = _passing_signal(scoreNorm=0.54, combinedConviction=0.54)
        ok, reason = trader._can_execute(sig, _base_cfg())
        assert not ok
        assert "0.540 < min 0.550" in reason

    def test_passes_above_min_conviction(self):
        trader = AutoTrader()
        sig = _passing_signal(scoreNorm=0.56, combinedConviction=0.56)
        ok, reason = trader._can_execute(sig, _base_cfg())
        assert ok
        assert reason == ""

    def test_aligned_signals_get_discount(self):
        trader = AutoTrader()
        cfg = _base_cfg()
        sig = _passing_signal(
            scoreNorm=0.50,
            combinedConviction=0.50,
            enginesAligned=True,
            engine_b_score=3,
            engine_b_max=5,
        )
        ok, reason = trader._can_execute(sig, cfg)
        assert ok, f"Aligned signal should pass at discounted threshold: {reason}"

    def test_asset_type_specific_conviction(self):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["AUTO_TRADE_MIN_CONVICTION"] = {"default": 0.5, "forex": 0.8}
        sig = _passing_signal(
            type="forex",
            scoreNorm=0.75,
            combinedConviction=0.75,
            pair="EUR/USD",
        )
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "0.750 < min 0.800" in reason


class TestCanExecuteRegimeFilter:
    def test_blocks_ranging_by_default(self):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["AUTO_TRADE_BLOCKED_REGIMES"] = {"default": ["RANGING"]}
        sig = _passing_signal(regimeName="RANGING")
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "RANGING blocked" in reason

    def test_blocks_by_trend_state(self):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["AUTO_TRADE_BLOCKED_TREND_STATES"] = {"default": ["DEAD RANGING"]}
        sig = _passing_signal(trendState="DEAD RANGING")
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "DEAD RANGING" in reason

    def test_allows_non_blocked_regime(self):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["AUTO_TRADE_BLOCKED_REGIMES"] = {"default": ["RANGING"]}
        sig = _passing_signal(regimeName="TRENDING")
        ok, reason = trader._can_execute(sig, cfg)
        assert ok


class TestCanExecuteMarketHours:
    def test_blocks_forex_on_weekend(self, monkeypatch):
        """Simulate Saturday UTC — forex market closed."""
        trader = AutoTrader()
        cfg = _base_cfg()
        # Force datetime.now to return Saturday at 12:00 UTC
        saturday = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)  # 2026-05-09 is Saturday
        monkeypatch.setattr("auto_trader.datetime", _MockDatetime(saturday))
        sig = _passing_signal(type="forex", pair="EUR/USD")
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "MARKET_CLOSED" in reason

    def test_allows_forex_on_weekday(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        monday = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr("auto_trader.datetime", _MockDatetime(monday))
        sig = _passing_signal(type="forex", pair="EUR/USD")
        ok, reason = trader._can_execute(sig, cfg)
        # Should pass the market hours check (may fail on other gates, but not MARKET_CLOSED)
        if not ok:
            assert "MARKET_CLOSED" not in reason

    def test_blocks_stock_outside_equity_hours(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        # 05:00 UTC = outside equity hours (07:00-21:00)
        early = datetime(2026, 5, 11, 5, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr("auto_trader.datetime", _MockDatetime(early))
        sig = _passing_signal(type="stock", pair="AAPL")
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "MARKET_CLOSED" in reason

    def test_blocks_stock_on_weekend(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        sunday = datetime(2026, 5, 10, 14, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr("auto_trader.datetime", _MockDatetime(sunday))
        sig = _passing_signal(type="stock", pair="AAPL")
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "MARKET_CLOSED" in reason

    def test_crypto_always_open(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        saturday = datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr("auto_trader.datetime", _MockDatetime(saturday))
        sig = _passing_signal(type="crypto")
        ok, reason = trader._can_execute(sig, cfg)
        assert ok or "MARKET_CLOSED" not in (reason or "")


class _MockDatetime:
    """Replace datetime in auto_trader module with a frozen time."""

    def __init__(self, frozen_now):
        self._frozen = frozen_now

    def now(self, tz=None):
        if tz is None:
            tz = timezone.utc
        return self._frozen.astimezone(tz)

    def fromisoformat(self, s):
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    def fromtimestamp(self, ts, tz=None):
        return datetime.fromtimestamp(ts, tz or timezone.utc)

    def __getattr__(self, name):
        return getattr(datetime, name)


class TestCanExecuteSessionFilter:
    def test_blocks_when_outside_allowed_session(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["AUTO_TRADE_SESSIONS"] = {"forex": ["london"]}
        # 03:00 UTC = outside London (07:00-16:00)
        early = datetime(2026, 5, 11, 3, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr("auto_trader.datetime", _MockDatetime(early))
        sig = _passing_signal(type="forex", pair="EUR/USD")
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "outside trading session" in reason

    def test_allows_when_session_open(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["AUTO_TRADE_SESSIONS"] = {"forex": ["london"]}
        # 10:00 UTC = inside London (07:00-16:00)
        london_time = datetime(2026, 5, 11, 10, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr("auto_trader.datetime", _MockDatetime(london_time))
        sig = _passing_signal(type="forex", pair="EUR/USD")
        ok, reason = trader._can_execute(sig, cfg)
        assert ok or "outside trading session" not in (reason or "")

    def test_always_allows_when_always_configured(self):
        trader = AutoTrader()
        cfg = _base_cfg()
        # crypto has ["always"] in base cfg, should pass
        sig = _passing_signal(type="crypto")
        ok, reason = trader._can_execute(sig, cfg)
        assert ok or "outside trading session" not in (reason or "")


class TestCanExecuteSLPrecheck:
    def test_blocks_when_sl_too_wide(self):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["MAX_SL_PCT"] = {"crypto": 0.05}
        sig = _passing_signal(price=100.0, sl=50.0, tp1=150.0)  # 50% SL distance
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "SL distance" in reason

    def test_passes_when_sl_within_max(self):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["MAX_SL_PCT"] = {"crypto": 0.05}
        sig = _passing_signal(price=100.0, sl=98.0, tp1=105.0)  # 2% SL distance
        ok, reason = trader._can_execute(sig, cfg)
        assert ok

    def test_handles_missing_entry_or_sl(self):
        trader = AutoTrader()
        sig = _passing_signal(price=0, sl=0)
        ok, reason = trader._can_execute(sig, _base_cfg())
        # Should not crash; if entry=0 the SL pre-check is skipped
        # But it may fail on other gates
        assert "SL distance" not in (reason or "")


class TestCanExecuteConductorSkip:
    def test_blocked_by_conductor(self):
        trader = AutoTrader()
        cfg = _base_cfg()
        sig = _passing_signal(conductor={"skip_signal": True, "reasons": ["no_trade_today"]})
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "CONDUCTOR_SKIP" in reason

    def test_allowed_when_conductor_does_not_skip(self):
        trader = AutoTrader()
        cfg = _base_cfg()
        sig = _passing_signal(conductor={"skip_signal": False, "reasons": []})
        ok, reason = trader._can_execute(sig, cfg)
        assert ok

    def test_skip_passes_no_conductor(self):
        # When conductor is missing entirely, the hydrate sets it but doesn't block
        trader = AutoTrader()
        cfg = _base_cfg()
        sig = _passing_signal()
        # Remove conductor key
        sig.pop("conductor", None)
        ok, reason = trader._can_execute(sig, cfg)
        # Hydration will fail silently since no audit_db, then normal path
        assert ok or "CONDUCTOR_SKIP" not in (reason or "")


class TestCanExecuteSentimentGate:
    def test_blocks_on_sentiment_block(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["SENTIMENT_GATE_ENABLED"] = True
        cfg["AUTO_TRADE_SENTIMENT_GATE_MODE"] = "always_when_enabled"
        cfg["EVENT_RISK_ENABLED"] = False
        cfg["SIGNAL_DEBATE_ENABLED"] = False
        monkeypatch.setattr(
            "sentiment_gate.check_sentiment",
            lambda pair, direction, asset_type: {"allowed": False, "reason": "Sentiment block"},
        )
        sig = _passing_signal()
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "Sentiment block" in reason

    def test_blocks_when_sentiment_missing_allowed_key(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["SENTIMENT_GATE_ENABLED"] = True
        cfg["AUTO_TRADE_SENTIMENT_GATE_MODE"] = "always_when_enabled"
        cfg["EVENT_RISK_ENABLED"] = False
        cfg["SIGNAL_DEBATE_ENABLED"] = False
        monkeypatch.setattr(
            "sentiment_gate.check_sentiment",
            lambda pair, direction, asset_type: {},
        )
        sig = _passing_signal()
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "Sentiment" in reason

    def test_passes_on_good_sentiment(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["SENTIMENT_GATE_ENABLED"] = True
        cfg["AUTO_TRADE_SENTIMENT_GATE_MODE"] = "always_when_enabled"
        cfg["EVENT_RISK_ENABLED"] = False
        cfg["SIGNAL_DEBATE_ENABLED"] = False
        monkeypatch.setattr(
            "sentiment_gate.check_sentiment",
            lambda pair, direction, asset_type: {"allowed": True, "reason": "OK"},
        )
        sig = _passing_signal()
        ok, reason = trader._can_execute(sig, cfg)
        assert ok

    def test_blocks_on_sentiment_import_error(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["SENTIMENT_GATE_ENABLED"] = True
        cfg["AUTO_TRADE_SENTIMENT_GATE_MODE"] = "always_when_enabled"
        cfg["EVENT_RISK_ENABLED"] = False
        cfg["SIGNAL_DEBATE_ENABLED"] = False
        # Remove the module so the local import inside _can_execute triggers ImportError
        monkeypatch.setattr("sentiment_gate.check_sentiment", _raise_import_error)
        sig = _passing_signal()
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "Sentiment gate unavailable" in reason

    def test_skips_legacy_sentiment_when_news_already_applied(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["SENTIMENT_GATE_ENABLED"] = True
        cfg["AUTO_TRADE_SENTIMENT_GATE_MODE"] = "always_when_enabled"
        cfg["NEWS_SENTIMENT_CONFLUENCE_ENABLED"] = True
        cfg["SENTIMENT_GATE_SKIP_WHEN_NEWS_APPLIED"] = True

        def _unexpected(*_args, **_kwargs):
            raise AssertionError("check_sentiment should be skipped")

        monkeypatch.setattr("sentiment_gate.check_sentiment", _unexpected)
        sig = _passing_signal()
        sig["newsSentimentDelta"] = 0.05
        ok, reason = trader._sentiment_gate_allowed(sig, cfg, {}, {}, "forex")
        assert ok
        assert reason == ""
        assert sig["sentimentGate"]["skippedReason"] == "news_sentiment_already_applied"


class TestCanExecuteEventRiskGate:
    def test_blocks_on_event_risk_block(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["SENTIMENT_GATE_ENABLED"] = False
        cfg["EVENT_RISK_ENABLED"] = True
        cfg["SIGNAL_DEBATE_ENABLED"] = False
        monkeypatch.setattr(
            "event_risk.check_event_risk",
            lambda pair, asset_type, **kw: {"allowed": False, "reason": "Event risk block"},
        )
        sig = _passing_signal()
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "Event risk block" in reason

    def test_blocks_when_event_risk_missing_allowed_key(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["SENTIMENT_GATE_ENABLED"] = False
        cfg["EVENT_RISK_ENABLED"] = True
        cfg["SIGNAL_DEBATE_ENABLED"] = False
        monkeypatch.setattr(
            "event_risk.check_event_risk",
            lambda pair, asset_type, **kw: {},
        )
        sig = _passing_signal()
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "Event risk" in reason

    def test_blocks_on_import_error(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["SENTIMENT_GATE_ENABLED"] = False
        cfg["EVENT_RISK_ENABLED"] = True
        cfg["SIGNAL_DEBATE_ENABLED"] = False
        monkeypatch.setattr("event_risk.check_event_risk", _raise_import_error)
        sig = _passing_signal()
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "Event risk gate unavailable" in reason


def _raise_import_error(*args, **kwargs):
    raise ImportError("Simulated import error")


class TestCanExecuteDebateGate:
    def test_debate_allows_good_signal(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["SIGNAL_DEBATE_ENABLED"] = True
        monkeypatch.setattr(
            "signal_debate.run_signal_debate",
            lambda signal: {
                "trace_id": "test-1",
                "grade": "GOOD",
                "allowed": True,
                "reasoning": "Looks good",
                "score_adjustment": 0.0,
                "score_adjustment_raw": 0.0,
            },
        )
        monkeypatch.setattr(
            "ai_reconciliation.arbitrate_ai",
            lambda *a, **kw: {"execution_allowed": True, "reason": "OK"},
        )
        sig = _passing_signal()
        ok, reason = trader._can_execute(sig, cfg)
        assert ok

    def test_debate_blocks_when_arbitration_rejects(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["SIGNAL_DEBATE_ENABLED"] = True
        monkeypatch.setattr(
            "signal_debate.run_signal_debate",
            lambda signal: {
                "trace_id": "test-2",
                "grade": "WEAK",
                "allowed": False,
                "reasoning": "Not convinced",
                "score_adjustment": 0.0,
            },
        )
        monkeypatch.setattr(
            "ai_reconciliation.arbitrate_ai",
            lambda *a, **kw: {"execution_allowed": False, "reason": "Debate rejected"},
        )
        sig = _passing_signal()
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "AI arbitration" in reason

    def test_debate_blocks_when_conductor_skips_debate_rejected(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["SIGNAL_DEBATE_ENABLED"] = True
        monkeypatch.setattr(
            "ai_reconciliation.arbitrate_ai",
            lambda *a, **kw: {"execution_allowed": False, "reason": "Debate skipped"},
        )
        sig = _passing_signal(conductor={"run_debate": False, "skip_signal": False})
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok

    def test_debate_clamps_positive_adjustment(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["SIGNAL_DEBATE_ENABLED"] = True
        monkeypatch.setattr(
            "signal_debate.run_signal_debate",
            lambda signal: {
                "trace_id": "test-3",
                "grade": "GOOD",
                "allowed": True,
                "reasoning": "positive adj clamped",
                "score_adjustment": 0.30,
                "score_adjustment_raw": 0.30,
            },
        )
        monkeypatch.setattr(
            "ai_reconciliation.arbitrate_ai",
            lambda *a, **kw: {"execution_allowed": True, "reason": "OK"},
        )
        sig = _passing_signal(combinedConviction=0.9, confluenceScore=2.7)
        ok, reason = trader._can_execute(sig, cfg)
        # Positive adjustment should be clamped to 0, signal should pass unchanged
        assert ok
        assert sig.get("_debate_adjustment_clamped") is True

    def test_debate_import_error_blocks(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        cfg["SIGNAL_DEBATE_ENABLED"] = True
        monkeypatch.setattr("signal_debate.run_signal_debate", _raise_import_error)
        sig = _passing_signal()
        ok, reason = trader._can_execute(sig, cfg)
        assert not ok
        assert "AI debate unavailable" in reason


# ═══════════════════════════════════════════════════════════════════════════
# _execute_signal — execution path tests
# ═══════════════════════════════════════════════════════════════════════════

class _MockApproval:
    """Emulates a RiskApproval namedtuple/dataclass."""
    approved = True
    volume = 0.5
    risk_amount = 100.0
    risk_pct = 0.01
    portfolio_heat = 0.02
    drawdown_pct = 0.0
    reason = "OK"

    def __init__(self, approved=True):
        self.approved = approved


class TestExecuteSignal:
    """Test the full _execute_signal path with broker mocks."""

    def _setup_trader(self, monkeypatch, audit_db="", cfg=None):
        trader = AutoTrader()
        trader.configure(
            lambda style="auto": {"success": True, "tradeSignals": []},
            lambda: False,
            lambda: False,
            audit_db,
            lambda: cfg or _base_cfg(),
        )
        return trader

    def _mock_bybit(self, monkeypatch, account_ok=True, positions_ok=True, symbol_ok=True):
        monkeypatch.setattr(
            "bybit_executor.bybit_get_account",
            lambda: {"balance": 10000, "equity": 10000} if account_ok else None,
        )
        monkeypatch.setattr(
            "bybit_executor.bybit_get_positions",
            lambda: {"positions": [], "error": None} if positions_ok else {"error": "API error", "detail": "timeout"},
        )
        monkeypatch.setattr(
            "bybit_executor.bybit_get_symbol_info",
            lambda pair: {"trade_contract_size": 1, "trade_tick_size": 0.01, "trade_tick_value": 0.01,
                          "volume_min": 0.001, "volume_max": 100, "volume_step": 0.001} if symbol_ok else None,
        )

    def _mock_mt5(self, monkeypatch, account_ok=True, positions_ok=True, symbol_ok=True):
        monkeypatch.setattr(
            "mt5_executor.mt5_get_account",
            lambda: {"balance": 10000, "equity": 10000, "error": None} if account_ok else {"error": "not connected"},
        )
        monkeypatch.setattr(
            "mt5_executor.mt5_get_positions",
            lambda: {"positions": [], "error": None} if positions_ok else {"error": "API error"},
        )
        monkeypatch.setattr(
            "mt5_executor.mt5_get_symbol_info",
            lambda pair: {"trade_contract_size": 100000, "trade_tick_size": 0.00001, "trade_tick_value": 1.0,
                          "volume_min": 0.01, "volume_max": 100, "volume_step": 0.01} if symbol_ok else None,
        )

    def _mock_risk_and_execution(self, monkeypatch, risk_ok=True, guardian_ok=True, exec_ok=True):
        monkeypatch.setattr(
            "risk_engine.risk_check",
            lambda *a, **kw: _MockApproval(approved=risk_ok),
        )
        monkeypatch.setattr(
            "guardian.pre_trade_check",
            lambda *a, **kw: (guardian_ok, "OK" if guardian_ok else "Guardian blocked"),
        )
        monkeypatch.setattr(
            "execution_lifecycle.run_managed_execution",
            lambda venue, signal, approval: (
                {"success": True, "ticket": 12345, "volume": 0.5, "entryPrice": 50000,
                 "slippageBps": 0.5, "feeCost": 1.5, "signalPriceRef": 50000}
                if exec_ok
                else {"success": False, "error": "BROKER_REJECTED"}
            ),
        )

    def test_bybit_success_path(self, monkeypatch):
        self._mock_bybit(monkeypatch)
        self._mock_risk_and_execution(monkeypatch)
        trader = self._setup_trader(monkeypatch)
        sig = _passing_signal(type="crypto", price=50000, sl=49000, tp1=52000)
        result = trader._execute_signal(sig, _base_cfg())
        assert result is True

    def test_mt5_success_path(self, monkeypatch):
        self._mock_mt5(monkeypatch)
        self._mock_risk_and_execution(monkeypatch)
        trader = self._setup_trader(monkeypatch)
        sig = _passing_signal(type="forex", pair="EUR/USD", price=1.10, sl=1.09, tp1=1.12)
        result = trader._execute_signal(sig, _base_cfg())
        assert result is True

    def test_bybit_not_connected(self, monkeypatch):
        self._mock_bybit(monkeypatch, account_ok=False)
        trader = self._setup_trader(monkeypatch)
        sig = _passing_signal(type="crypto")
        result = trader._execute_signal(sig, _base_cfg())
        assert result is False

    def test_bybit_positions_unavailable(self, monkeypatch):
        self._mock_bybit(monkeypatch, positions_ok=False)
        trader = self._setup_trader(monkeypatch)
        sig = _passing_signal(type="crypto")
        result = trader._execute_signal(sig, _base_cfg())
        assert result is False

    def test_bybit_symbol_unavailable(self, monkeypatch):
        self._mock_bybit(monkeypatch, symbol_ok=False)
        trader = self._setup_trader(monkeypatch)
        sig = _passing_signal(type="crypto")
        result = trader._execute_signal(sig, _base_cfg())
        assert result is False

    def test_mt5_not_connected(self, monkeypatch):
        self._mock_mt5(monkeypatch, account_ok=False)
        trader = self._setup_trader(monkeypatch)
        sig = _passing_signal(type="forex", pair="EUR/USD")
        result = trader._execute_signal(sig, _base_cfg())
        assert result is False

    def test_mt5_positions_unavailable(self, monkeypatch):
        self._mock_mt5(monkeypatch, positions_ok=False)
        trader = self._setup_trader(monkeypatch)
        sig = _passing_signal(type="forex", pair="EUR/USD")
        result = trader._execute_signal(sig, _base_cfg())
        assert result is False

    def test_mt5_symbol_unavailable(self, monkeypatch):
        self._mock_mt5(monkeypatch, symbol_ok=False)
        trader = self._setup_trader(monkeypatch)
        sig = _passing_signal(type="forex", pair="EUR/USD")
        result = trader._execute_signal(sig, _base_cfg())
        assert result is False

    def test_risk_check_rejects(self, monkeypatch):
        self._mock_bybit(monkeypatch)
        self._mock_risk_and_execution(monkeypatch, risk_ok=False)
        trader = self._setup_trader(monkeypatch)
        sig = _passing_signal(type="crypto")
        result = trader._execute_signal(sig, _base_cfg())
        assert result is False

    def test_guardian_blocks(self, monkeypatch):
        self._mock_bybit(monkeypatch)
        self._mock_risk_and_execution(monkeypatch, guardian_ok=False)
        trader = self._setup_trader(monkeypatch)
        sig = _passing_signal(type="crypto")
        result = trader._execute_signal(sig, _base_cfg())
        assert result is False

    def test_execution_fails(self, monkeypatch):
        self._mock_bybit(monkeypatch)
        self._mock_risk_and_execution(monkeypatch, exec_ok=False)
        trader = self._setup_trader(monkeypatch)
        sig = _passing_signal(type="crypto")
        result = trader._execute_signal(sig, _base_cfg())
        assert result is False

    def test_exception_during_execution(self, monkeypatch):
        self._mock_bybit(monkeypatch)
        monkeypatch.setattr(
            "risk_engine.risk_check",
            lambda *a, **kw: (_raise_execution_exception()),
        )
        trader = self._setup_trader(monkeypatch)
        sig = _passing_signal(type="crypto")
        result = trader._execute_signal(sig, _base_cfg())
        assert result is False

    def test_increments_trades_today_on_success(self, monkeypatch):
        self._mock_bybit(monkeypatch)
        self._mock_risk_and_execution(monkeypatch)
        trader = self._setup_trader(monkeypatch)
        sig = _passing_signal(type="crypto")
        assert trader._trades_today == 0
        trader._execute_signal(sig, _base_cfg())
        assert trader._trades_today == 1

    def test_sets_last_exec_fields_on_success(self, monkeypatch):
        self._mock_bybit(monkeypatch)
        self._mock_risk_and_execution(monkeypatch)
        trader = self._setup_trader(monkeypatch)
        sig = _passing_signal(type="crypto", pair="BTC/USDT", direction="LONG")
        trader._execute_signal(sig, _base_cfg())
        assert trader._last_exec_pair == "BTC/USDT"
        assert trader._last_exec_dir == "LONG"
        assert trader._last_exec_at is not None

    def test_writes_audit_on_success(self, monkeypatch):
        audit_db = _temp_audit_db()
        try:
            self._mock_bybit(monkeypatch)
            self._mock_risk_and_execution(monkeypatch)
            trader = self._setup_trader(monkeypatch, audit_db=audit_db)
            sig = _passing_signal(type="crypto", pair="BTC/USDT")
            trader._execute_signal(sig, _base_cfg())
            # Verify audit row was written
            con = sqlite3.connect(audit_db, timeout=15.0)
            try:
                rows = con.execute("SELECT pair, grade FROM audit_log WHERE grade LIKE 'AUTO%'").fetchall()
                assert len(rows) >= 1
                assert rows[0][0] == "BTC/USDT"
            finally:
                con.close()
        finally:
            import gc; gc.collect()  # force close any lingering WAL connections
            for _retry in range(3):
                try:
                    os.unlink(audit_db)
                    break
                except PermissionError:
                    time.sleep(0.1)


def _raise_execution_exception():
    raise RuntimeError("Simulated execution error")


# ═══════════════════════════════════════════════════════════════════════════
# _write_audit and _write_error
# ═══════════════════════════════════════════════════════════════════════════

def _cleanup_db(db_path):
    """Close all connections and delete a temp SQLite DB file."""
    import gc
    gc.collect()
    for _retry in range(5):
        try:
            if os.path.exists(db_path):
                os.unlink(db_path)
            # Also clean up WAL/SHM files
            for ext in ("-wal", "-shm"):
                wal = db_path + ext
                if os.path.exists(wal):
                    os.unlink(wal)
            break
        except PermissionError:
            time.sleep(0.1)


class TestWriteAudit:
    def test_writes_audit_row(self, monkeypatch):
        audit_db = _temp_audit_db()
        try:
            trader = AutoTrader()
            trader.configure(None, None, None, audit_db, lambda: {})
            sig = _passing_signal(type="crypto", pair="BTC/USDT")

            class _MockApprovalFull:
                approved = True
                volume = 0.5
                risk_amount = 100.0
                risk_pct = 0.01
                portfolio_heat = 0.02
                drawdown_pct = 0.0
                reason = "OK"

            result = {
                "success": True,
                "ticket": 12345,
                "volume": 0.5,
                "entryPrice": 50000,
                "slippageBps": 0.5,
                "feeCost": 1.5,
                "signalPriceRef": 50000,
            }
            trader._write_audit(sig, _MockApprovalFull(), result, {})
            con = sqlite3.connect(audit_db, timeout=15.0)
            try:
                rows = con.execute("SELECT pair, score, engine, direction, grade FROM audit_log").fetchall()
                assert len(rows) == 1
                assert rows[0][0] == "BTC/USDT"
                assert "AUTO" in rows[0][4]
            finally:
                con.close()
        finally:
            _cleanup_db(audit_db)

    def test_writes_multi_leg_audit(self, monkeypatch):
        audit_db = _temp_audit_db()
        try:
            trader = AutoTrader()
            trader.configure(None, None, None, audit_db, lambda: {})
            sig = _passing_signal(type="crypto", pair="BTC/USDT")

            class _MockApprovalFull:
                approved = True
                volume = 0.5
                risk_amount = 100.0
                risk_pct = 0.01
                portfolio_heat = 0.02
                drawdown_pct = 0.0
                reason = "OK"

            result = {
                "success": True,
                "ticket": 12345,
                "volume": 0.5,
                "entryPrice": 50000,
                "slippageBps": 0.5,
                "feeCost": 1.5,
                "signalPriceRef": 50000,
                "legs": [
                    {"ticket": 12345, "volume": 0.3, "entryPrice": 50000, "tp": 52000},
                    {"ticket": 12346, "volume": 0.2, "entryPrice": 50000, "tp": 53000},
                ],
            }
            trader._write_audit(sig, _MockApprovalFull(), result, {})
            con = sqlite3.connect(audit_db, timeout=15.0)
            try:
                rows = con.execute("SELECT pair, volume FROM audit_log ORDER BY id").fetchall()
                assert len(rows) == 2
                vol_sum = sum(r[1] for r in rows)
                assert vol_sum == pytest.approx(0.5, abs=1e-4)
            finally:
                con.close()
        finally:
            _cleanup_db(audit_db)

    def test_skips_when_no_audit_db(self):
        trader = AutoTrader()
        trader._write_audit({"pair": "BTC/USDT"}, None, {}, {})
        # No crash = success


class TestWriteError:
    def test_writes_error_row(self):
        audit_db = _temp_audit_db()
        try:
            trader = AutoTrader()
            trader.configure(None, None, None, audit_db, lambda: {})
            trader._write_error({"pair": "BTC/USDT", "type": "crypto", "direction": "LONG"}, "TEST_ERROR")
            con = sqlite3.connect(audit_db, timeout=15.0)
            try:
                rows = con.execute("SELECT pair, error_tag, grade FROM audit_log").fetchall()
                assert len(rows) == 1
                assert rows[0][0] == "BTC/USDT"
                assert rows[0][1] == "TEST_ERROR"
                assert "AUTO-ERR" in rows[0][2]
            finally:
                con.close()
        finally:
            _cleanup_db(audit_db)

    def test_writes_multiple_errors(self):
        audit_db = _temp_audit_db()
        try:
            trader = AutoTrader()
            trader.configure(None, None, None, audit_db, lambda: {})
            trader._write_error({"pair": "BTC/USDT", "type": "crypto"}, "ERR_1")
            trader._write_error({"pair": "ETH/USDT", "type": "crypto"}, "ERR_2")
            con = sqlite3.connect(audit_db, timeout=15.0)
            try:
                rows = con.execute("SELECT error_tag FROM audit_log ORDER BY id").fetchall()
                assert [r[0] for r in rows] == ["ERR_1", "ERR_2"]
            finally:
                con.close()
        finally:
            _cleanup_db(audit_db)

    def test_skips_when_no_audit_db(self):
        trader = AutoTrader()
        trader._write_error({"pair": "BTC/USDT"}, "ERR")
        # No crash = success


# ═══════════════════════════════════════════════════════════════════════════
# _run_auto_scan — scan lifecycle tests
# ═══════════════════════════════════════════════════════════════════════════

class TestRunAutoScan:
    def test_skips_when_no_scan_fn_configured(self):
        trader = AutoTrader()
        # configure was never called — run_scan_fn is None
        trader._run_auto_scan()
        # Should log warning but not crash

    def test_skips_when_kill_switch_active(self):
        trader = AutoTrader()
        trader.configure(lambda style="auto": {}, lambda: True, lambda: False, "", lambda: {})
        trader._run_auto_scan()
        # Should return early, no crash

    def test_skips_when_daily_cap_reached(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        trader.configure(lambda style="auto": {}, lambda: False, lambda: False, "", lambda: cfg)
        with trader._lock:
            trader._trades_today = 3  # max_daily = 3
        trader._run_auto_scan()
        # Should return early due to daily cap

    def test_skips_when_scan_fails(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        def failing_scan(style="auto"):
            raise RuntimeError("Scan crash")
        trader.configure(failing_scan, lambda: False, lambda: False, "", lambda: cfg)
        trader._run_auto_scan()
        # Should log error and return

    def test_skips_when_scan_returns_no_success(self, monkeypatch):
        trader = AutoTrader()
        cfg = _base_cfg()
        trader.configure(lambda style="auto": {"success": False, "error": "no data"}, lambda: False, lambda: False, "", lambda: cfg)
        trader._run_auto_scan()
        # Should log info and return

    # (see standalone test below for max_per_scan enforcement)
def test_run_auto_scan_enforces_max_per_scan(monkeypatch):
    trader = AutoTrader()
    cfg = _base_cfg()
    cfg["AUTO_TRADE_MAX_PER_SCAN"] = 1
    cfg["AUTO_TRADE_BLOCKED_TREND_STATES"] = {"default": [], "crypto": []}
    cfg["AUTO_TRADE_BLOCKED_REGIMES"] = {"default": [], "crypto": []}

    sig1 = _passing_signal(pair="BTC/USDT", combinedConviction=0.9, timestamp="2026-05-11T12:00:00+00:00")
    sig2 = _passing_signal(pair="ETH/USDT", combinedConviction=0.8, timestamp="2026-05-11T12:00:00+00:00")

    trader.configure(
        lambda style="auto": {"success": True, "tradeSignals": [sig1, sig2], "watchlist": [], "scanFunnel": {}},
        lambda: False,
        lambda: False,
        "",
        lambda: cfg,
    )
    monkeypatch.setattr(trader, "_can_execute", lambda sig, _cfg: (True, ""))

    executed = []

    def tracking_execute(sig, _cfg):
        executed.append(sig.get("pair"))
        return True

    monkeypatch.setattr(trader, "_execute_signal", tracking_execute)

    trader._run_auto_scan()

    assert len(executed) <= 1


def test_run_auto_scan_stops_at_daily_cap(monkeypatch):
    trader = AutoTrader()
    cfg = _base_cfg()
    cfg["AUTO_TRADE_MAX_PER_SCAN"] = 5
    cfg["AUTO_TRADE_BLOCKED_TREND_STATES"] = {"default": [], "crypto": []}
    cfg["AUTO_TRADE_BLOCKED_REGIMES"] = {"default": [], "crypto": []}

    sig1 = _passing_signal(pair="BTC/USDT", combinedConviction=0.9, timestamp="2026-05-11T12:00:00+00:00")
    sig2 = _passing_signal(pair="ETH/USDT", combinedConviction=0.8, timestamp="2026-05-11T12:00:00+00:00")
    sig3 = _passing_signal(pair="SOL/USDT", combinedConviction=0.7, timestamp="2026-05-11T12:00:00+00:00")

    trader.configure(
        lambda style="auto": {"success": True, "tradeSignals": [sig1, sig2, sig3], "watchlist": [], "scanFunnel": {}},
        lambda: False,
        lambda: False,
        "",
        lambda: cfg,
    )
    monkeypatch.setattr(trader, "_can_execute", lambda sig, _cfg: (True, ""))

    executed = []

    def tracking_execute(sig, _cfg):
        executed.append(sig.get("pair"))
        with trader._lock:
            trader._trades_today += 1
        return True

    monkeypatch.setattr(trader, "_execute_signal", tracking_execute)

    trader._run_auto_scan()

    assert len(executed) <= 3  # max_daily = 3


def test_run_auto_scan_enforces_naked_max_daily_per_pair(monkeypatch):
    trader = AutoTrader()
    cfg = _base_cfg()
    cfg["AUTO_TRADE_MAX_DAILY"] = 5
    cfg["AUTO_TRADE_MAX_PER_SCAN"] = 5
    cfg["NAKED_MAX_DAILY"] = 1
    cfg["AUTO_TRADE_BLOCKED_TREND_STATES"] = {"default": [], "crypto": []}
    cfg["AUTO_TRADE_BLOCKED_REGIMES"] = {"default": [], "crypto": []}

    sig1 = _passing_signal(pair="BTC/USDT", engine="engine_b", is_naked=True)
    sig2 = _passing_signal(pair="BTC/USDT", engine="engine_b", is_naked=True)
    sig3 = _passing_signal(pair="ETH/USDT", engine="engine_b", is_naked=True)

    trader.configure(
        lambda style="auto": {"success": True, "tradeSignals": [sig1, sig2, sig3], "watchlist": [], "scanFunnel": {}},
        lambda: False,
        lambda: False,
        "",
        lambda: cfg,
    )
    monkeypatch.setattr(trader, "_can_execute", lambda sig, _cfg: (True, ""))
    monkeypatch.setattr(trader, "_get_cached_open_positions", lambda *_args, **_kwargs: [])

    executed = []

    def tracking_execute(sig, _cfg):
        executed.append(sig.get("pair"))
        return True

    monkeypatch.setattr(trader, "_execute_signal", tracking_execute)

    trader._run_auto_scan()

    assert executed.count("BTC/USDT") == 1
    assert executed.count("ETH/USDT") == 1


def test_run_auto_scan_logs_near_miss_watchlist(monkeypatch, caplog):
    """When scan returns no signals but has watchlist entries, log the best near-miss."""
    trader = AutoTrader()
    cfg = _base_cfg()
    trader.configure(
        lambda style="auto": {
            "success": True,
            "tradeSignals": [],
            "watchlist": [
                {"pair": "BTC/USDT", "confluenceScore": 1.5, "maxScore": 3.0, "direction": "LONG"},
            ],
            "scanFunnel": {},
        },
        lambda: False,
        lambda: False,
        "",
        lambda: cfg,
    )
    import logging
    caplog.set_level(logging.INFO)
    trader._run_auto_scan()
    assert any("Best near-miss" in r.message for r in caplog.records)


# ═══════════════════════════════════════════════════════════════════════════
# _get_cached_open_positions
# ═══════════════════════════════════════════════════════════════════════════

class TestGetCachedOpenPositions:
    def test_returns_empty_list_on_exception(self, monkeypatch):
        trader = AutoTrader()
        monkeypatch.setattr(
            "bybit_executor.bybit_get_positions",
            lambda: (_ for _ in ()).throw(RuntimeError("API down")),
        )
        result = trader._get_cached_open_positions("crypto", {})
        assert result == []

    def test_returns_empty_list_on_broker_error(self, monkeypatch):
        trader = AutoTrader()
        monkeypatch.setattr(
            "bybit_executor.bybit_get_positions",
            lambda: {"error": "API error", "detail": "timeout"},
        )
        result = trader._get_cached_open_positions("crypto", {})
        assert result == []

    def test_returns_positions_on_success(self, monkeypatch):
        trader = AutoTrader()
        monkeypatch.setattr(
            "bybit_executor.bybit_get_positions",
            lambda: {"positions": [{"pair": "BTC/USDT", "volume": 0.1}]},
        )
        result = trader._get_cached_open_positions("crypto", {})
        assert len(result) == 1
        assert result[0]["pair"] == "BTC/USDT"


# ═══════════════════════════════════════════════════════════════════════════
# configure, enable, disable
# ═══════════════════════════════════════════════════════════════════════════

class TestConfigure:
    def test_sets_dependencies(self):
        trader = AutoTrader()
        fn = lambda: None
        trader.configure(fn, fn, fn, "test.db", fn)
        assert trader._run_scan_fn is fn
        assert trader._kill_switch_fn is fn
        assert trader._test_mode_fn is fn
        assert trader._audit_db == "test.db"
        assert trader._config_fn is fn


class TestEnableDisable:
    def test_enable_sets_flag(self):
        trader = AutoTrader()
        trader.enable()
        assert trader._enabled is True

    def test_disable_clears_flag(self):
        trader = AutoTrader()
        trader.enable()
        trader.disable()
        assert trader._enabled is False

    def test_toggle_switches_state(self):
        trader = AutoTrader()
        trader.enable()
        trader.toggle()
        assert trader._enabled is False
        trader.toggle()
        assert trader._enabled is True


class TestGetStatus:
    def test_returns_status_fields(self):
        trader = AutoTrader()
        cfg = _base_cfg()
        trader.configure(None, None, None, "", lambda: cfg)
        status = trader.get_status()

        assert "enabled" in status
        assert "tradesToday" in status
        assert "maxDaily" in status
        assert "minScore" in status
        assert "minConviction" in status
        assert "liveGateMetric" in status
        assert "liveGateDisplay" in status
        assert status["liveGateMetric"] == "executionConvictionEffective"
        assert status["EXECUTION_ENABLED"] is True
        assert status["MT5_EXECUTION_ENABLED"] is True
        assert status["BYBIT_EXECUTION_ENABLED"] is True
        assert status["aiDebate"]["scoreMutationEnabled"] is False
        assert status["engineDBackgroundAutopilot"]["status"] == "disabled_by_design"
        assert status["minScoreDeprecated"] is True
        assert "DEPRECATED" in status["minScoreExecutionNote"]

    def test_reports_trades_today(self):
        trader = AutoTrader()
        trader.configure(None, None, None, "", lambda: _base_cfg())
        with trader._lock:
            trader._trades_today = 2
        status = trader.get_status()
        assert status["tradesToday"] == 2
