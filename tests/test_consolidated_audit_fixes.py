"""Regression tests for the consolidated mathematical/logical audit fixes.

Covers the mechanical execution-safety findings that were verified against
current source and implemented:

  C1 - Pure Engine A signals must honour the RR minimum gate.
  C2 - Daily loss limit must persist across process restart.
  C3 - Bybit SL cap must use resolve_max_sl_pct (symbol/score-group overrides).
  C4 - MT5 positions with sl<=0 must contribute conservative portfolio heat.
  H2 - Guardian SL-cap / drawdown checks must fail closed on a real breach.
  H7 - Fixed-range volume-profile LVN threshold must be config-driven.
"""

import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import risk_engine
from risk_engine import risk_check


@pytest.fixture(autouse=True)
def _isolate_risk_drawdown(monkeypatch):
    monkeypatch.setattr(risk_engine, "_current_drawdown", lambda *_a, **_k: 0.0)


def _make_signal(**overrides):
    base = {
        "pair": "BTCUSDT",
        "direction": "LONG",
        "price": 100.0,
        "sl": 95.0,
        "tp1": 110.0,
        "tp2": 120.0,
        "type": "crypto",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confluenceScore": 5.0,
        "candleConsistency": {"H4": {"status": "OK"}},
    }
    base.update(overrides)
    return base


# ── C1 ──────────────────────────────────────────────────────────────────────
def test_pure_engine_a_signal_rejects_rr_below_minimum():
    """A pure Engine A signal (no verdict, no components, not naked) with
    RR < 1.0 must be rejected. Before the fix the RR gate only ran for
    consensus/Engine B signals, so this geometry slipped through."""
    # RR = |102 - 100| / |100 - 95| = 2 / 5 = 0.4
    result = risk_check(
        _make_signal(price=100.0, sl=95.0, tp1=102.0),
        10000.0,
        10000.0,
        [],
    )
    assert result.approved is False
    assert result.reason == "RR_BELOW_MINIMUM"


def test_pure_engine_a_signal_with_valid_rr_still_approved():
    """Guard: the broadened RR gate must not reject healthy RR>=1 pure signals."""
    result = risk_check(
        _make_signal(price=100.0, sl=95.0, tp1=110.0),
        10000.0,
        10000.0,
        [],
    )
    assert result.approved is True


# ── C3 ──────────────────────────────────────────────────────────────────────
def test_resolve_max_sl_pct_honours_crypto_symbol_override():
    """The override mechanism the Bybit cap must now use: a narrower per-symbol
    override beats the crypto default."""
    from risk_engine import resolve_max_sl_pct

    cfg = {
        "MAX_SL_PCT": {"crypto": 0.08, "default": 0.05},
        "MAX_SL_PCT_SYMBOL_OVERRIDES": {"FARTCOIN/USDT": 0.03},
    }
    cap, source = resolve_max_sl_pct(
        {"pair": "FARTCOIN/USDT", "type": "crypto"}, "crypto", cfg
    )
    assert cap == pytest.approx(0.03)
    assert source == "symbol:FARTCOIN/USDT"


def test_bybit_sl_cap_uses_resolve_max_sl_pct(monkeypatch):
    """Bybit must reject when resolve_max_sl_pct reports SL too wide."""
    from risk_engine import RiskApproval
    import bybit_executor

    class _FakeExchange:
        def fetch_ticker(self, symbol):
            return {"ask": 50000.0, "bid": 49999.0, "last": 50000.0}

    approval = RiskApproval(True, 0.01, 100.0, 0.01, 0.01, 0.0, "OK")
    signal = {
        "pair": "BTCUSDT",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "price": 50000.0,
        "sl": 40000.0,
        "tp1": 52000.0,
        "type": "crypto",
    }

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _FakeExchange())
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "risk_engine.resolve_max_sl_pct",
        lambda *args, **kwargs: (0.05, "crypto_default"),
    )

    out = bybit_executor.bybit_execute(signal, approval)
    assert out.get("success") is False
    assert "SL_TOO_WIDE" in (out.get("error") or "")


# ── C4 ──────────────────────────────────────────────────────────────────────
def test_mt5_position_risk_nonzero_without_sl():
    """An MT5 position with no SL (sl<=0) must contribute a conservative,
    non-zero risk to portfolio heat instead of 0 (which allowed unlimited
    stacking of SL-less positions)."""
    from mt5_executor import _mt5_position_risk_amount

    risk = _mt5_position_risk_amount(
        price_open=2000.0,
        sl=0.0,
        volume=1.0,
        tick_size=0.01,
        tick_value=1.0,
        fallback_sl_pct=0.05,
    )
    # Assumed SL distance = 2000 * 0.05 = 100 → (100 / 0.01) * 1.0 * 1.0
    assert risk == pytest.approx(10000.0)
    assert risk > 0


def test_mt5_position_risk_with_sl_unchanged():
    """The SL-set path must keep the original risk formula."""
    from mt5_executor import _mt5_position_risk_amount

    risk = _mt5_position_risk_amount(
        price_open=2000.0,
        sl=1995.0,
        volume=1.0,
        tick_size=0.01,
        tick_value=1.0,
        fallback_sl_pct=0.05,
    )
    # SL distance = 5 → (5 / 0.01) * 1.0 * 1.0
    assert risk == pytest.approx(500.0)


def test_mt5_position_risk_zero_when_tick_data_missing():
    """Degenerate tick data → 0 (cannot compute), preserving prior safety."""
    from mt5_executor import _mt5_position_risk_amount

    assert _mt5_position_risk_amount(2000.0, 0.0, 1.0, 0.0, 1.0, 0.05) == 0.0
    assert _mt5_position_risk_amount(2000.0, 0.0, 1.0, 0.01, 0.0, 0.05) == 0.0


# ── C2 ──────────────────────────────────────────────────────────────────────
def test_daily_loss_persists_across_restart(monkeypatch):
    """Recorded daily P&L must survive a process restart. Simulated by writing
    via record_daily_pnl, then reloading from the SQLite store as a fresh
    process would on startup."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tmp_dir = tempfile.mkdtemp()
    try:
        db = os.path.join(tmp_dir, "audit.db")
        monkeypatch.setattr(risk_engine, "_RISK_DB", db)
        risk_engine._init_daily_pnl_table()

        # Fresh in-memory state, as at process start.
        monkeypatch.setattr(risk_engine, "_daily_pnl", {}, raising=False)
        monkeypatch.setattr(risk_engine, "_daily_start_balance", {}, raising=False)
        monkeypatch.setattr(risk_engine, "_daily_pnl_date", "", raising=False)

        risk_engine.record_daily_pnl(-300.0, 10000.0, asset_type="forex")

        # Simulate restart: a new process reloads persisted daily P&L.
        pnl, start, date_str = risk_engine._load_daily_pnl()
        assert date_str == today
        assert pnl["forex"] == pytest.approx(-300.0)
        assert start["forex"] == pytest.approx(10000.0)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_daily_loss_does_not_reload_stale_prior_day(monkeypatch):
    """A stored row from a previous day must not be restored as today's loss."""
    tmp_dir = tempfile.mkdtemp()
    try:
        db = os.path.join(tmp_dir, "audit.db")
        monkeypatch.setattr(risk_engine, "_RISK_DB", db)
        risk_engine._init_daily_pnl_table()
        risk_engine._save_daily_pnl("forex", "2000-01-01", -9999.0, 10000.0)

        pnl, start, date_str = risk_engine._load_daily_pnl()
        assert pnl == {}
        assert start == {}
        assert date_str == ""
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── H2 ──────────────────────────────────────────────────────────────────────
class _RaisingConfig(dict):
    """A config whose .get() always raises, simulating config unavailability."""

    def get(self, *_a, **_k):  # noqa: D401
        raise RuntimeError("config unavailable")


def test_guardian_drawdown_fails_closed_when_config_unavailable(monkeypatch):
    """Audit H2: if config access raises inside the drawdown circuit breaker,
    the gate must still BLOCK a real drawdown using the fail-safe default
    threshold — not silently pass."""
    import config as config_module
    import guardian

    monkeypatch.setattr(config_module, "CONFIG", _RaisingConfig())

    signal = {
        "pair": "EURUSD",
        "direction": "LONG",
        "price": 1.10,
        "sl": 1.099,
        "tp1": 1.12,
        "type": "forex",
    }
    # equity 8000 vs balance 10000 → 20% drawdown, exceeds default 15% stop.
    ok, reason = guardian.pre_trade_check(
        signal, [], {"balance": 10000.0, "equity": 8000.0}
    )
    assert ok is False
    assert reason.startswith("DRAWDOWN_EXCEEDED")


# ── H7 ──────────────────────────────────────────────────────────────────────
def _fixed_range_session():
    return [
        {"high": 110.0, "low": 90.0, "vol": 50.0},        # thin spread → low-vol bins
        {"high": 100.05, "low": 99.95, "vol": 100000.0},  # concentrated → POC
    ]


def test_fixed_range_lvn_threshold_is_configurable():
    """H7: the fixed-range LVN threshold must be a parameter, not hardcoded 0.30,
    so it can be harmonised with the bucketed path."""
    from volume_profile import compute_fixed_range_volume_profile

    none_lvn = compute_fixed_range_volume_profile(
        _fixed_range_session(), lvn_threshold=0.0
    )
    many_lvn = compute_fixed_range_volume_profile(
        _fixed_range_session(), lvn_threshold=0.9
    )
    assert none_lvn["lvn_levels"] == []
    assert len(many_lvn["lvn_levels"]) > 0


def test_fixed_range_lvn_default_preserves_030_behavior():
    """The default must remain 0.30 so existing behavior is unchanged."""
    from volume_profile import compute_fixed_range_volume_profile

    default = compute_fixed_range_volume_profile(_fixed_range_session())
    explicit = compute_fixed_range_volume_profile(
        _fixed_range_session(), lvn_threshold=0.30
    )
    assert default["lvn_levels"] == explicit["lvn_levels"]
