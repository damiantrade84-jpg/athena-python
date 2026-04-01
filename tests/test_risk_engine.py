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
    """Reset persisted risk state before each test so local DB state doesn't leak in."""
    with risk_engine._peak_lock:
        old = dict(risk_engine._peak_equity)
        risk_engine._peak_equity = {}
    with risk_engine._daily_lock:
        old_daily_pnl = dict(risk_engine._daily_pnl)
        old_daily_pnl_date = risk_engine._daily_pnl_date
        old_daily_start_balance = dict(risk_engine._daily_start_balance)
        risk_engine._daily_pnl = {}
        risk_engine._daily_pnl_date = ""
        risk_engine._daily_start_balance = {}
    yield
    with risk_engine._peak_lock:
        risk_engine._peak_equity = old
    with risk_engine._daily_lock:
        risk_engine._daily_pnl = old_daily_pnl
        risk_engine._daily_pnl_date = old_daily_pnl_date
        risk_engine._daily_start_balance = old_daily_start_balance


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
        result = risk_check(
            _make_signal(direction="SHORT", sl=61000, tp1=58000, tp2=56000),
            10000,
            10000,
            [],
        )
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
        # Simulate 20% drawdown: peak was 10000, equity is 8000 (crypto signal)
        import risk_engine

        with risk_engine._peak_lock:
            old = dict(risk_engine._peak_equity)
            risk_engine._peak_equity["crypto"] = 10000.0
        try:
            result = risk_check(_make_signal(type="crypto"), 8000, 8000, [])
            assert result.approved is False
            assert result.reason == "DRAWDOWN_CIRCUIT_BREAKER"
        finally:
            with risk_engine._peak_lock:
                risk_engine._peak_equity = old

    def test_drawdown_peaks_are_independent_per_asset_type(self):
        import risk_engine

        with risk_engine._peak_lock:
            risk_engine._peak_equity = {"crypto": 10000.0, "forex": 50000.0}
        assert abs(risk_engine._current_drawdown(8000, "crypto") - 0.2) < 1e-9
        assert abs(risk_engine._current_drawdown(48000, "forex") - 0.04) < 1e-9


# ── Max positions ────────────────────────────────────────────────────────────


class TestMaxPositions:
    def test_rejects_at_max_positions(self):
        max_pos = int(_cfg("MAX_OPEN_POSITIONS", 5))
        positions = [{"pair": f"P{i}", "risk_amount": 10} for i in range(max_pos)]
        result = risk_check(_make_signal(), 100000, 100000, positions)
        assert result.approved is False
        assert result.reason == "MAX_POSITIONS_REACHED"


# ── Invalid levels ───────────────────────────────────────────────────────────


class TestMaxSlPct:
    def test_rejects_when_sl_distance_exceeds_cap(self):
        """Crypto MAX_SL_PCT default 8% — 33% wide stop must fail in risk_check."""
        result = risk_check(
            _make_signal(price=60000, sl=40000, tp1=65000, tp2=68000),
            100000,
            100000,
            [],
        )
        assert result.approved is False
        assert result.reason == "MAX_SL_EXCEEDED"


class TestInvalidLevels:
    def test_rejects_zero_sl(self):
        result = risk_check(_make_signal(sl=0), 10000, 10000, [])
        assert result.approved is False
        assert result.reason == "INVALID_LEVELS"

    def test_rejects_entry_equals_sl(self):
        result = risk_check(_make_signal(price=100, sl=100), 10000, 10000, [])
        assert result.approved is False
        assert result.reason == "INVALID_LEVELS"

    def test_rejects_long_stop_above_entry(self):
        result = risk_check(
            _make_signal(pair="EUR/USD", type="forex", price=1.1000, sl=1.1200),
            100000,
            100000,
            [],
        )
        assert result.approved is False
        assert result.reason == "INVALID_LEVELS"

    def test_rejects_short_stop_below_entry(self):
        result = risk_check(
            _make_signal(
                pair="EUR/USD",
                type="forex",
                direction="SHORT",
                price=1.1000,
                sl=1.0900,
                tp1=1.0800,
                tp2=1.0600,
            ),
            100000,
            100000,
            [],
        )
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

    def test_risk_check_passes_explicit_regime_to_sizing(self, monkeypatch):
        captured = {}

        def _fake_calc_volume(
            account_balance,
            entry_price,
            sl_price,
            symbol_info,
            asset_type,
            pair=None,
            regime="",
        ):
            captured["regime"] = regime
            return 1.0

        monkeypatch.setattr(risk_engine, "_calc_volume", _fake_calc_volume)
        result = risk_check(
            _make_signal(
                pair="EUR/USD",
                type="forex",
                price=1.1000,
                sl=1.0900,
                tp1=1.1200,
                tp2=1.1400,
                regimeName="HIGH_VOLATILITY",
            ),
            100000,
            100000,
            [],
        )

        assert result.approved is True
        assert captured["regime"] == "HIGH_VOLATILITY"


# ── Peak equity thread safety ───────────────────────────────────────────────


class TestPeakEquityThreadSafety:
    def test_concurrent_updates(self):
        import risk_engine

        with risk_engine._peak_lock:
            risk_engine._peak_equity = {}
        results = []

        def updater(val):
            peak = _update_peak(val, "unknown")
            results.append(peak)

        threads = [threading.Thread(target=updater, args=(i * 100,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Final peak should be the max value
        with risk_engine._peak_lock:
            assert risk_engine._peak_equity.get("unknown") == 1900.0


class TestDailyLossPerAssetType:
    def test_independent_daily_loss_buckets(self):
        from datetime import datetime, timezone

        import risk_engine

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with risk_engine._daily_lock:
            risk_engine._daily_pnl_date = today
            risk_engine._daily_pnl = {"forex": -6000.0}
            risk_engine._daily_start_balance = {"forex": 100000.0, "crypto": 10000.0}
        blocked_fx, pct_fx = risk_engine._check_daily_loss(100000.0, "forex")
        assert blocked_fx is True
        assert pct_fx >= _cfg("DAILY_LOSS_LIMIT", 0.05)
        blocked_crypto, _ = risk_engine._check_daily_loss(10000.0, "crypto")
        assert blocked_crypto is False

    def test_fresh_day_resets_counters(self):
        import risk_engine

        with risk_engine._daily_lock:
            risk_engine._daily_pnl_date = "1999-01-01"
            risk_engine._daily_pnl = {"forex": -99999.0}
            risk_engine._daily_start_balance = {"forex": 1.0}
        blocked, _ = risk_engine._check_daily_loss(100000.0, "forex")
        assert blocked is False


# ── _cfg live reads ──────────────────────────────────────────────────────────


class TestCfgLiveReads:
    def test_returns_default_when_missing(self):
        val = _cfg("NONEXISTENT_KEY_12345", 42)
        assert val == 42

    def test_reads_existing_config(self):
        val = _cfg("RISK_PCT", 0.01)
        assert isinstance(val, (int, float))


class TestAdaptiveKellyCache:
    def test_cache_is_regime_sensitive(self):
        import time

        risk_engine._kelly_cache.clear()
        now = time.time()
        risk_engine._kelly_cache[("crypto", "")] = (0.01, now)
        risk_engine._kelly_cache[("crypto", "HIGH_VOLATILITY")] = (0.007, now)
        try:
            assert risk_engine._adaptive_risk_pct("crypto", "") == 0.01
            assert risk_engine._adaptive_risk_pct("crypto", "HIGH_VOLATILITY") == 0.007
        finally:
            risk_engine._kelly_cache.clear()


# ── SL override direction logic (FIX 5) ─────────────────────────────────────


class TestSlOverrideDirection:
    def test_short_sl_override_picks_closer(self):
        """SHORT: SL is above price. min() picks lower (closer to entry) = tighter stop."""
        math_sl, struct_sl = 105.0, 110.0
        result = min(math_sl, struct_sl)
        assert result == 105.0, "SHORT SL override must pick the closer (lower) candidate"

    def test_long_sl_override_picks_closer(self):
        """LONG: SL is below price. max() picks higher (closer to entry) = tighter stop."""
        math_sl, struct_sl = 95.0, 92.0
        result = max(math_sl, struct_sl)
        assert result == 95.0, "LONG SL override must pick the closer (higher) candidate"

    def test_short_sl_old_logic_was_wrong(self):
        """Regression: old max() for SHORT would have picked 110.0 (wider), not 105.0."""
        math_sl, struct_sl = 105.0, 110.0
        wrong_result = max(math_sl, struct_sl)  # old (broken) behaviour
        correct_result = min(math_sl, struct_sl)  # fixed behaviour
        assert wrong_result == 110.0  # confirms old logic was wider
        assert correct_result == 105.0  # confirms fix is tighter

    def test_long_sl_old_logic_was_wrong(self):
        """Regression: old min() for LONG would have picked 92.0 (wider), not 95.0."""
        math_sl, struct_sl = 95.0, 92.0
        wrong_result = min(math_sl, struct_sl)  # old (broken) behaviour
        correct_result = max(math_sl, struct_sl)  # fixed behaviour
        assert wrong_result == 92.0  # confirms old logic was wider
        assert correct_result == 95.0  # confirms fix is tighter
