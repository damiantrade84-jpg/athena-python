"""test_risk_engine.py — Unit tests for risk_engine.py."""

import sys
import os
import threading
import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from risk_engine import risk_check, _cfg, _update_peak
import risk_engine


@pytest.fixture(autouse=True)
def _reset_peak_equity():
    """Reset peak equity before each test so drawdown state doesn't leak."""
    with risk_engine._peak_lock:
        old = risk_engine._peak_equity
    yield
    with risk_engine._peak_lock:
        risk_engine._peak_equity = old


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_signal(**overrides):
    """Minimal valid signal dict."""
    base = {
        "pair": "BTCUSDT",
        "direction": "LONG",
        "price": 60000,
        "sl": 59000,
        "tp1": 62000,
        "tp2": 64000,
        "type": "crypto",
        "timestamp": None,
        "confluenceScore": 5.0,
    }
    base.update(overrides)
    return base


# ── Direction validation ─────────────────────────────────────────────────────


class TestDirectionValidation:
    def test_rejects_missing_direction(self):
        sig = _make_signal()
        sig.pop("direction")
        result = risk_check(sig, 10000, 10000, [])
        assert result.approved is False
        assert result.reason == "INVALID_DIRECTION"

    def test_rejects_nonsense_direction(self):
        result = risk_check(_make_signal(direction="SIDEWAYS"), 10000, 10000, [])
        assert result.approved is False
        assert result.reason == "INVALID_DIRECTION"

    def test_accepts_long(self):
        result = risk_check(_make_signal(direction="LONG"), 10000, 10000, [])
        assert result.approved is True

    def test_accepts_short(self):
        result = risk_check(_make_signal(direction="SHORT"), 10000, 10000, [])
        assert result.approved is True

    def test_accepts_lowercase(self):
        result = risk_check(_make_signal(direction="long"), 10000, 10000, [])
        assert result.approved is True


# ── Kill switch ──────────────────────────────────────────────────────────────


class TestKillSwitch:
    def test_kill_switch_rejects(self):
        result = risk_check(_make_signal(), 10000, 10000, [], kill_switch=True)
        assert result.approved is False
        assert result.reason == "KILL_SWITCH_ACTIVE"


# ── Drawdown circuit breaker ────────────────────────────────────────────────


class TestDrawdown:
    def test_severe_drawdown_rejects(self):
        # Simulate 20% drawdown: peak was 10000, equity is 8000
        import risk_engine

        with risk_engine._peak_lock:
            old = risk_engine._peak_equity
            risk_engine._peak_equity = 10000.0
        try:
            result = risk_check(_make_signal(), 8000, 8000, [])
            assert result.approved is False
            assert result.reason == "DRAWDOWN_CIRCUIT_BREAKER"
        finally:
            with risk_engine._peak_lock:
                risk_engine._peak_equity = old


# ── Max positions ────────────────────────────────────────────────────────────


class TestMaxPositions:
    def test_rejects_at_max_positions(self):
        # Default MAX_OPEN_POSITIONS=5
        positions = [{"pair": f"P{i}", "risk_amount": 10} for i in range(5)]
        result = risk_check(_make_signal(), 100000, 100000, positions)
        assert result.approved is False
        assert result.reason == "MAX_POSITIONS_REACHED"


# ── Invalid levels ───────────────────────────────────────────────────────────


class TestInvalidLevels:
    def test_rejects_zero_sl(self):
        result = risk_check(_make_signal(sl=0), 10000, 10000, [])
        assert result.approved is False
        assert result.reason == "INVALID_LEVELS"

    def test_rejects_entry_equals_sl(self):
        result = risk_check(_make_signal(price=100, sl=100), 10000, 10000, [])
        assert result.approved is False
        assert result.reason == "INVALID_LEVELS"


# ── Approval ─────────────────────────────────────────────────────────────────


class TestApproval:
    def test_valid_signal_approved(self):
        result = risk_check(_make_signal(), 100000, 100000, [])
        assert result.approved is True
        assert result.reason == "OK"
        assert result.volume > 0
        assert result.risk_pct > 0


# ── Peak equity thread safety ───────────────────────────────────────────────


class TestPeakEquityThreadSafety:
    def test_concurrent_updates(self):
        import risk_engine

        with risk_engine._peak_lock:
            risk_engine._peak_equity = 0.0
        results = []

        def updater(val):
            peak = _update_peak(val)
            results.append(peak)

        threads = [threading.Thread(target=updater, args=(i * 100,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Final peak should be the max value
        with risk_engine._peak_lock:
            assert risk_engine._peak_equity == 1900.0


# ── _cfg live reads ──────────────────────────────────────────────────────────


class TestCfgLiveReads:
    def test_returns_default_when_missing(self):
        val = _cfg("NONEXISTENT_KEY_12345", 42)
        assert val == 42

    def test_reads_existing_config(self):
        val = _cfg("RISK_PCT", 0.01)
        assert isinstance(val, (int, float))
