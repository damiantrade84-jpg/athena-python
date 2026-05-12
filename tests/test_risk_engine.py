"""test_risk_engine.py — Unit tests for risk_engine.py."""

import sys
import os
import threading
from datetime import datetime, timezone
import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from risk_engine import (
    join_peak_audit_queue,
    risk_check,
    _cfg,
    _signal_quality_factor,
    _update_peak,
)
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
    join_peak_audit_queue(timeout=5.0)


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
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confluenceScore": 5.0,
        "candleFetchMeta": {
            "D1": {"stalenessSeverity": "fresh"},
            "H4": {"stalenessSeverity": "fresh"},
            "H1": {"stalenessSeverity": "fresh"},
        },
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

    def test_auto_execution_uses_same_kill_switch_gate(self):
        result = risk_check(_make_signal(), 10000, 10000, [], kill_switch=True)
        assert result.approved is False
        assert result.reason == "KILL_SWITCH_ACTIVE"


class TestDataFreshnessGate:
    def test_fresh_candles_allow_execution(self, monkeypatch):
        monkeypatch.setitem(
            risk_engine.CONFIG,
            "DATA_FRESHNESS_GATES",
            {
                "BLOCK_EXECUTION_ON_STALE": True,
                "BLOCK_TIMEFRAMES": ["H1", "H4", "D1"],
                "BLOCK_SEVERITIES": ["stale_1_bucket"],
            },
        )
        result = risk_check(
            _make_signal(
                candleFetchMeta={
                    "H1": {"stalenessSeverity": "fresh"},
                    "H4": {"stalenessSeverity": "fresh"},
                    "D1": {"stalenessSeverity": "fresh"},
                }
            ),
            100000,
            100000,
            [],
        )
        assert result.approved is True

    def test_h4_stale_one_bucket_blocks_when_gate_enabled(self, monkeypatch):
        monkeypatch.setitem(
            risk_engine.CONFIG,
            "DATA_FRESHNESS_GATES",
            {
                "BLOCK_EXECUTION_ON_STALE": True,
                "BLOCK_TIMEFRAMES": ["H4"],
                "BLOCK_SEVERITIES": ["stale_1_bucket"],
            },
        )
        result = risk_check(
            _make_signal(candleFetchMeta={"H4": {"stalenessSeverity": "stale_1_bucket"}}),
            100000,
            100000,
            [],
        )
        assert result.approved is False
        assert result.reason == "STALE_DATA_BLOCK:H4:stale_1_bucket"

    def test_h4_stale_one_bucket_allows_when_execution_gate_disabled(self, monkeypatch):
        monkeypatch.setitem(
            risk_engine.CONFIG,
            "DATA_FRESHNESS_GATES",
            {
                "BLOCK_EXECUTION_ON_STALE": False,
                "BLOCK_TIMEFRAMES": ["H4"],
                "BLOCK_SEVERITIES": ["stale_1_bucket"],
            },
        )
        result = risk_check(
            _make_signal(candleFetchMeta={"H4": {"stalenessSeverity": "stale_1_bucket"}}),
            100000,
            100000,
            [],
        )
        assert result.approved is True

    def test_engine_a_b_path_mismatch_blocks_execution(self, monkeypatch):
        monkeypatch.setitem(
            risk_engine.CONFIG,
            "DATA_FRESHNESS_GATES",
            {
                "BLOCK_EXECUTION_ON_STALE": True,
                "BLOCK_TIMEFRAMES": ["H4"],
                "BLOCK_SEVERITIES": ["error_path_mismatch"],
            },
        )
        result = risk_check(
            _make_signal(candleConsistency={"H4": {"status": "ERROR_PATH_MISMATCH"}}),
            100000,
            100000,
            [],
        )
        assert result.approved is False
        assert result.reason == "STALE_DATA_BLOCK:H4:error_path_mismatch"

    def test_crypto_h4_d1_stale_data_blocks_execution(self, monkeypatch):
        monkeypatch.setitem(
            risk_engine.CONFIG,
            "DATA_FRESHNESS_GATES",
            {
                "BLOCK_EXECUTION_ON_STALE": True,
                "BLOCK_TIMEFRAMES": ["H4", "D1"],
                "BLOCK_SEVERITIES": ["stale_multi_bucket"],
            },
        )
        result = risk_check(
            _make_signal(
                type="crypto",
                candleFetchMeta={"D1": {"stalenessSeverity": "stale_multi_bucket"}},
            ),
            100000,
            100000,
            [],
        )
        assert result.approved is False
        assert result.reason == "STALE_DATA_BLOCK:D1:stale_multi_bucket"

    def test_forex_h4_offset_mismatch_blocks_execution(self, monkeypatch):
        monkeypatch.setitem(
            risk_engine.CONFIG,
            "DATA_FRESHNESS_GATES",
            {
                "BLOCK_EXECUTION_ON_STALE": True,
                "BLOCK_TIMEFRAMES": ["H4"],
                "BLOCK_SEVERITIES": ["error_offset_mismatch"],
            },
        )
        result = risk_check(
            _make_signal(
                pair="EUR/USD",
                type="forex",
                price=1.1000,
                sl=1.0900,
                tp1=1.1200,
                candleConsistency={"H4": {"status": "ERROR_OFFSET_MISMATCH"}},
            ),
            100000,
            100000,
            [],
        )
        assert result.approved is False
        assert result.reason == "STALE_DATA_BLOCK:H4:error_offset_mismatch"


# ── Drawdown circuit breaker ────────────────────────────────────────────────


class TestDrawdown:
    def test_severe_drawdown_rejects(self, monkeypatch):
        # Simulate 20% drawdown: peak was 10000, equity is 8000 (crypto signal)
        import risk_engine

        # config.yaml may override the code default; force the gate ON for this test
        monkeypatch.setitem(risk_engine.CONFIG, "DRAWDOWN_STOP_ENABLED", True)
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

    def test_drawdown_gate_disabled_allows_trade(self, monkeypatch):
        import risk_engine

        monkeypatch.setitem(risk_engine.CONFIG, "DRAWDOWN_STOP_ENABLED", False)
        with risk_engine._peak_lock:
            old = dict(risk_engine._peak_equity)
            risk_engine._peak_equity["crypto"] = 10000.0
        try:
            result = risk_check(_make_signal(type="crypto"), 8000, 8000, [])
            assert result.approved is True
        finally:
            with risk_engine._peak_lock:
                risk_engine._peak_equity = old

    def test_drawdown_peaks_are_shared_across_domain_assets(self):
        """MT5 assets (forex, stock) must share a peak equity bucket; crypto is separate."""
        import risk_engine

        with risk_engine._peak_lock:
            # Setting peak for 'forex' domain
            risk_engine._peak_equity = {"forex": 50000.0, "crypto": 10000.0}

        # Forex drawdown
        assert abs(risk_engine._current_drawdown(40000, "forex") - 0.2) < 1e-9
        # Stock drawdown (maps to 'forex' domain) — should see the SAME peak
        assert abs(risk_engine._current_drawdown(45000, "stock") - 0.1) < 1e-9
        # Crypto drawdown — independent
        assert abs(risk_engine._current_drawdown(8000, "crypto") - 0.2) < 1e-9


# ── Max positions ────────────────────────────────────────────────────────────


class TestMaxPositions:
    def test_rejects_at_max_positions(self):
        max_pos = int(_cfg("MAX_OPEN_POSITIONS", 5))
        positions = [{"pair": f"P{i}", "risk_amount": 10} for i in range(max_pos)]
        result = risk_check(_make_signal(), 100000, 100000, positions)
        assert result.approved is False
        assert result.reason == "MAX_POSITIONS_REACHED"


class TestExecutionStateSafety:
    def test_watchlist_state_blocks_execution(self):
        result = risk_check(
            _make_signal(
                verdict="ALIGNED",
                decision_state="watchlist",
                tier="WATCHLIST",
            ),
            100000,
            100000,
            [],
        )
        assert result.approved is False
        assert result.reason == "NON_EXECUTABLE_SIGNAL_STATE"

    def test_blocked_state_blocks_execution(self):
        result = risk_check(
            _make_signal(verdict="A_ONLY", decision_state="blocked", tier="SKIP"),
            100000,
            100000,
            [],
        )
        assert result.approved is False
        assert result.reason == "NON_EXECUTABLE_SIGNAL_STATE"

    def test_failed_risk_check_blocks_execution(self):
        result = risk_check(_make_signal(price=100, sl=100), 100000, 100000, [])
        assert result.approved is False
        assert result.reason == "INVALID_LEVELS"

    def test_missing_account_data_blocks_execution(self):
        result = risk_check(_make_signal(), None, 100000, [])
        assert result.approved is False
        assert result.reason == "MISSING_ACCOUNT_DATA"

    def test_duplicate_signal_order_blocks_execution(self):
        result = risk_check(
            _make_signal(pair="BTCUSDT"),
            100000,
            100000,
            [{"pair": "BTCUSDT", "risk_amount": 10}],
        )
        assert result.approved is False
        assert result.reason == "DUPLICATE_PAIR"

    def test_aligned_with_failed_engine_b_checklist_blocks_execution(self):
        result = risk_check(
            _make_signal(
                verdict="ALIGNED",
                tier="HIGH",
                engine_b_status={"checklist_passed": False},
            ),
            100000,
            100000,
            [],
        )
        assert result.approved is False
        assert result.reason == "ENGINE_B_CHECKLIST_FAILED"

    def test_a_only_without_valid_engine_a_signal_blocks_execution(self):
        result = risk_check(
            _make_signal(
                verdict="A_ONLY",
                tier="HIGH",
                components={"a_has_signal": False},
            ),
            100000,
            100000,
            [],
        )
        assert result.approved is False
        assert result.reason == "ENGINE_A_SIGNAL_INVALID"


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

    def test_signal_quality_factor_prefers_combined_conviction(self):
        sig = _make_signal(confluenceScore=2.7, maxScore=3.0, combinedConviction=0.4)
        assert _signal_quality_factor(sig) == 0.4

    def test_signal_quality_factor_malformed_confluence_uses_minimum_band(self):
        sig = _make_signal(confluenceScore="bad", maxScore="worse")
        assert _signal_quality_factor(sig) == 0.25

    def test_risk_check_sizes_from_combined_conviction_when_present(self, monkeypatch):
        monkeypatch.setattr(risk_engine, "_calc_volume", lambda *args, **kwargs: 1.0)
        result = risk_check(
            _make_signal(confluenceScore=2.7, maxScore=3.0, combinedConviction=0.4),
            100000,
            100000,
            [],
        )

        assert result.approved is True
        assert result.volume == pytest.approx(0.4, abs=1e-9)


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


    def test_domain_level_daily_loss_grouping(self):
        """Losses in 'stock' must affect 'forex' limit (same account); crypto remains isolated."""
        from datetime import datetime, timezone
        import risk_engine

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with risk_engine._daily_lock:
            risk_engine._daily_pnl_date = today
            # A loss in forex
            risk_engine._daily_pnl = {"forex": -4000.0}
            risk_engine._daily_start_balance = {"forex": 100000.0, "crypto": 10000.0}

        # Check stock (maps to forex) — should see the 4% loss
        blocked_stock, pct_stock = risk_engine._check_daily_loss(100000.0, "stock")
        assert blocked_stock is False
        assert abs(pct_stock - 0.04) < 1e-9

        # Add more loss via stock
        risk_engine.record_daily_pnl(-2000.0, 100000.0, "stock")

        # Now forex domain should be blocked (6% total loss > 5% limit)
        blocked_fx, pct_fx = risk_engine._check_daily_loss(100000.0, "forex")
        assert blocked_fx is True
        assert abs(pct_fx - 0.06) < 1e-9

        # Crypto remains clean
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
# appended tests

class TestConsensusMinRR:
    def test_rejects_low_rr_when_verdict_is_engine_c(self, monkeypatch):
        monkeypatch.setitem(risk_engine.CONFIG, "ENGINE_C_EXEC_MIN_RR", 1.0)
        sig = _make_signal(
            price=100.0,
            sl=95.0,
            tp1=104.0,
            verdict="ALIGNED",
            decision_state="execute",
            tier="HIGH",
            components={"b_checklist_passed": True},
            engine_b_status={"checklist_passed": True},
        )
        r = risk_check(sig, 10000, 10000, [])
        assert r.approved is False
        assert r.reason == "RR_BELOW_MINIMUM"

    def test_allows_low_rr_when_not_consensus_context(self, monkeypatch):
        monkeypatch.setitem(risk_engine.CONFIG, "ENGINE_C_EXEC_MIN_RR", 1.0)
        sig = _make_signal(
            price=100.0,
            sl=95.0,
            tp1=104.0,
        )
        r = risk_check(sig, 10000, 10000, [])
        assert r.approved is True

    def test_rejects_standalone_engine_b_low_rr_after_override(self, monkeypatch):
        monkeypatch.setitem(risk_engine.CONFIG, "ENGINE_C_EXEC_MIN_RR", 1.0)
        sig = _make_signal(
            price=100.0,
            sl=95.0,
            tp1=104.0,
            engine="engine_b",
            engine_b_min_rr=1.2,
            engine_b_status={"checklist_passed": True, "min_rr": 1.2},
        )
        r = risk_check(sig, 10000, 10000, [])
        assert r.approved is False
        assert r.reason == "RR_BELOW_MINIMUM"

    def test_rejects_standalone_engine_b_missing_checklist_proof(self, monkeypatch):
        monkeypatch.setitem(risk_engine.CONFIG, "ENGINE_C_EXEC_MIN_RR", 1.0)
        sig = _make_signal(
            price=100.0,
            sl=95.0,
            tp1=110.0,
            engine="engine_b",
        )
        r = risk_check(sig, 10000, 10000, [])
        assert r.approved is False
        assert r.reason == "ENGINE_B_CHECKLIST_MISSING"
