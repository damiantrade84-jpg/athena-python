import pytest
import types
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

# Modules under test
import timed_exit_monitor
import backtest_runner
import indicators
import volume_profile
import mt5_executor
import scalp_engine
import bybit_executor
from risk_engine import RiskApproval

# --- 1. Startup Logic Tests ---

def test_startup_does_not_launch_news_polling():
    """Verify that the background news polling is no longer in the startup sequence."""
    athena_path = "athena.py"
    with open(athena_path, "r", encoding="utf-8") as f:
        athena_source = f.read()
    
    if "def _cb_startup():" in athena_source:
        cb_block = athena_source.split("def _cb_startup():")[1].split("def ")[0]
        assert "fetch_news_context(" not in cb_block
    else:
        pytest.fail("_cb_startup not found")

# --- 2. Timed Exit Guard Tests ---

def test_scalp_trades_excluded_from_timed_exit(monkeypatch):
    """Ensure Engine D / scalp trades are ignored by the timed-exit monitor."""
    row = {
        "pair": "EURUSD",
        "direction": "long",
        "entry_price": 1.0500,
        "ticket": 12345,
        "engine": "scalp", 
        "ts": "2026-04-14T10:00:00Z" 
    }
    
    cfg = {"scalp": {"breakeven_min": 5, "close_min": 10}, "intraday": {}, "swing": {}}
    
    with patch("mt5_executor.mt5_get_positions") as mock_get_pos:
        timed_exit_monitor._handle_mt5_row(row, cfg)
        mock_get_pos.assert_not_called()

    with patch("bybit_executor.bybit_get_positions") as mock_get_pos:
        timed_exit_monitor._handle_bybit_row(row, cfg)
        mock_get_pos.assert_not_called()


def test_swing_timed_exit_uses_live_price_for_tp_progress(monkeypatch):
    """Swing timeout should not close when live price is already halfway to TP."""
    timed_exit_monitor._be_done.clear()
    row = {
        "pair": "EUR/USD",
        "direction": "LONG",
        "entry_price": 1.1000,
        "sl": 1.0900,
        "tp": 1.1100,
        "ticket": 12345,
        "engine": "engine_a",
        "style": "swing",
        "ts": "2026-04-01T10:00:00+00:00",
    }
    cfg = {
        "swing": {"breakeven_days": 0, "close_days": 0, "tp_progress_exempt": 0.50},
        "intraday": {"breakeven_min": 15, "close_min": 30},
        "scalp": {"breakeven_min": 5, "close_min": 5},
    }
    monkeypatch.setattr(
        "mt5_executor.mt5_get_positions",
        lambda: {
            "error": False,
            "positions": [
                {
                    "ticket": 12345,
                    "pair": "EUR/USD",
                    "direction": "LONG",
                    "entry": 1.1000,
                    "currentPrice": 1.1060,
                    "sl": 1.0900,
                    "tp": 1.1100,
                    "profit": 10.0,
                    "volume": 0.01,
                }
            ],
        },
    )
    be_calls = []
    close_calls = []
    monkeypatch.setattr(
        "mt5_executor.mt5_move_sl_to_breakeven",
        lambda ticket, entry: be_calls.append((ticket, entry)) or {"success": True},
    )
    monkeypatch.setattr(
        "mt5_executor.mt5_close_position",
        lambda ticket: close_calls.append(ticket) or {"success": True},
    )

    timed_exit_monitor._handle_mt5_row(row, cfg)

    # BE price includes a 5% SL-distance buffer above entry:
    # entry=1.1000, sl=1.0900 → sl_dist=0.01, buffer=0.01*0.05=0.0005 → be_price=1.1005
    _sl_dist = abs(row["entry_price"] - row["sl"])
    _expected_be = round(row["entry_price"] + _sl_dist * cfg.get("breakeven_buffer_r", 0.05), 10)
    assert be_calls == [(12345, _expected_be)]
    assert close_calls == []


def test_equal_close_and_be_window_closes_before_breakeven(monkeypatch):
    """If close and BE are both due, bank the profitable close before arming BE."""
    timed_exit_monitor._be_done.clear()
    opened = datetime.now(timezone.utc) - timedelta(minutes=6)
    row = {
        "pair": "USD/MXN",
        "direction": "SHORT",
        "entry_price": 17.23455,
        "sl": 17.25127,
        "tp": 17.21147,
        "ticket": 288617084,
        "engine": "engine_b",
        "style": "scalp",
        "ts": opened.isoformat(),
        "risk_amount": 100.0,
    }
    cfg = {
        "scalp": {"breakeven_min": 5, "close_min": 5},
        "intraday": {"breakeven_min": 15, "close_min": 30},
        "swing": {"breakeven_days": 2.5, "close_days": 5.0},
    }
    monkeypatch.setattr(
        "mt5_executor.mt5_get_positions",
        lambda: {
            "error": False,
            "positions": [
                {
                    "ticket": 288617084,
                    "pair": "USD/MXN",
                    "direction": "SHORT",
                    "entry": 17.23455,
                    "currentPrice": 17.23000,
                    "sl": 17.25127,
                    "tp": 17.21147,
                    "profit": 12.0,
                    "volume": 0.01,
                }
            ],
        },
    )
    be_calls = []
    close_calls = []
    monkeypatch.setattr(
        "mt5_executor.mt5_move_sl_to_breakeven",
        lambda ticket, entry: be_calls.append((ticket, entry)) or {"success": True},
    )
    monkeypatch.setattr(
        "mt5_executor.mt5_close_position",
        lambda ticket: close_calls.append(ticket)
        or {
            "success": True,
            "closePrice": 17.23000,
            "liveProfit": 12.0,
            "entryPrice": 17.23455,
            "direction": "SHORT",
        },
    )

    timed_exit_monitor._handle_mt5_row(row, cfg)

    assert close_calls == [288617084]
    assert be_calls == []

# --- 3. Backtest TP1+TP2 Tests ---

def _mock_scalp_setup(monkeypatch, candles, tp1, tp2):
    monkeypatch.setitem(backtest_runner.CONFIG, "SCALP_ENGINE", {
        "BT_ENABLED": True,
        "BT_SESSION_MODE": "all",
        "MIN_RR": 0.5,
        "TP2_ENABLED": (tp2 is not None),
        "BT_SCRATCH_ENABLED": False,
        "SCALP_VP_LOOKBACK_BARS": 20,
        "BIAS_TIMEFRAME": "M15",
        "MIN_GRADE_AUTO_EXECUTE": "C",
        # Terminal TP1 exit for assertions (default partial+runner yields BE/TIMEOUT after TP1 touch).
        "ENGINE_D_PARTIAL_EXIT_ENABLED": False,
    })

    monkeypatch.setattr(mt5_executor, "mt5_map_symbol", lambda x: "EURUSD")
    monkeypatch.setattr(scalp_engine, "mt5_fetch_scalp_candles", lambda *args, **kwargs: candles)
    
    monkeypatch.setattr(backtest_runner, "calc_atr", lambda *args: [1.0]*len(candles))
    monkeypatch.setattr(volume_profile, "compute_fixed_range_volume_profile", lambda *args, **kwargs: {
        "profile_valid": True, "poc": tp1, "vah": 105.0, "val": 100.0, "balance_ratio": 0.5
    })
    monkeypatch.setattr(indicators, "detect_absorption", lambda c, *args, **kwargs: [{"absorbed": i >= len(c) - 5, "direction": "bullish"} for i in range(len(c))])
    monkeypatch.setattr(indicators, "calc_cvd", lambda c, *args, **kwargs: {"smoothed_delta": list(range(len(c))), "cvd": list(range(len(c)))})
    monkeypatch.setattr(scalp_engine, "scalp_session_window", lambda *args, **kwargs: (True, "mock"))
    monkeypatch.setattr(scalp_engine, "_classify_market_state", lambda *args: "balance")
    monkeypatch.setattr(scalp_engine, "_locate_price_vs_vp", lambda price, vp, atr_m15=0: {"location": "at_val", "nearest_level": vp.get("val", 100.0), "distance_pct": 0.0})
    monkeypatch.setattr(scalp_engine, "_check_aaa_sequence", lambda candles, absorption, cvd, asset_type=None: {"complete": False, "phase": "absorption_only"})
    monkeypatch.setattr(backtest_runner, "_format_backtest_results", lambda trades, *args, **kwargs: {"trades": trades, "summary": {}})
    monkeypatch.setattr(backtest_runner, "_rt", lambda: types.SimpleNamespace(AUDIT_DB=":memory:"))

def test_scalp_backtest_exits_at_tp1_without_runner_averaging(monkeypatch):
    """Engine D backtest TP1 exits are profitable and don't chain to TP2.

    partial_taken_1r is True whenever price passes +1R (tp_partial) before TP1 —
    that's expected behaviour (50% partial at +1R, remaining 50% exits at TP1 or BE).
    The assertion here is that a TP1 exit is recorded with a positive R, not that
    the partial model is absent.
    """
    candles = [{"time": time.time(), "open": 100.0, "high": 100.1, "low": 99.99, "close": 100.0, "vol": 1000} for i in range(300)]

    # Entry bar i=101. Price approx 100.
    # Bar 110: Hit TP1 (110)
    candles[110]["open"] = 111.0
    candles[110]["high"] = 111.0

    # Bar 115: Hit TP2 (120) should not matter in TP1-only backtest management.
    candles[115]["open"] = 121.0
    candles[115]["high"] = 121.0

    _mock_scalp_setup(monkeypatch, candles, tp1=110.0, tp2=120.0)

    result = backtest_runner.backtest_pair_scalp({"display": "EUR/USD", "type": "forex"})

    assert "trades" in result and len(result["trades"]) > 0
    trade = next(t for t in result["trades"] if t["exit_reason"] == "TP1")
    assert trade["exit_reason"] == "TP1"
    assert trade["resultR"] > 0.0
    # partial_taken_1r may be True (price passed +1R on the way to TP1) — that's fine
    assert trade.get("tp1_hit") is True

def test_scalp_backtest_tp1_only(monkeypatch):
    candles = [{"time": time.time(), "open": 100.0, "high": 100.1, "low": 99.99, "close": 100.0, "vol": 1000} for i in range(300)]
    candles[110]["open"] = 111.0
    candles[110]["high"] = 111.0
    
    _mock_scalp_setup(monkeypatch, candles, tp1=110.0, tp2=None)
    
    result = backtest_runner.backtest_pair_scalp({"display": "EUR/USD", "type": "forex"})

    assert "trades" in result and len(result["trades"]) > 0
    trade = next(t for t in result["trades"] if t["exit_reason"] == "TP1")
    assert trade["exit_reason"] == "TP1"
    assert trade.get("tp1_hit") is True


def test_scalp_backtest_never_records_negative_tp1_for_long(monkeypatch):
    candles = [{"time": time.time(), "open": 100.0, "high": 100.1, "low": 99.99, "close": 100.0, "vol": 1000} for i in range(300)]
    candles[110]["open"] = 111.0
    candles[110]["high"] = 111.0

    _mock_scalp_setup(monkeypatch, candles, tp1=98.0, tp2=None)
    monkeypatch.setitem(
        backtest_runner.CONFIG,
        "SCALP_ENGINE",
        {**backtest_runner.CONFIG.get("SCALP_ENGINE", {}), "MIN_RR": 1.0},
    )

    result = backtest_runner.backtest_pair_scalp({"display": "EUR/USD", "type": "forex"})

    for trade in result.get("trades", []):
        assert trade["tp1"] > trade["entry"]
        assert not (trade["exit_reason"] == "TP1" and trade["resultR"] <= 0)

# --- 4. Precision & Level Collapse Regression Tests ---

def test_crypto_scalp_precision_guard():
    """Verify that calculate_scalp_levels prevents level collapse on low-priced crypto."""
    # Sub-$1 levels: 0.0912 vs 0.0905
    entry = 0.0912
    sl = 0.0905
    tp1 = 0.0925
    vp = {"poc": tp1, "vah": 0.0935, "val": sl}
    
    # Coarse precision that would normally collapse these to 0.09
    symbol_info = {"digits": 2, "point": 0.01}
    
    levels = scalp_engine.calculate_scalp_levels(
        direction="LONG",
        entry=entry,
        vp=vp,
        setup_type="mean_reversion",
        symbol_info=symbol_info,
        asset_type="crypto"
    )
    
    # Assertions: Levels must remain distinct
    assert levels["entry"] != levels["sl"], "Entry and SL collapsed into 0.09!"
    assert levels["entry"] != levels["tp1"], "Entry and TP1 collapsed into 0.09!"
    # The guard should push digits to 6 for entry < 1.0
    assert levels["entry"] == 0.0912
    assert levels["sl"] < 0.091 


def test_scalp_tp1_is_1r_and_structural_target_is_context(monkeypatch):
    """TP1 is the 1R self-pay target; VP structure remains separate context."""
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "MIN_RR": 3.0,
            "ATR_SL_ENABLED": False,
            "TP1_R_MULT": 1.0,
        },
    )
    vp = {"poc": 1.1002, "vah": 1.1010, "val": 1.1000}
    levels = scalp_engine.calculate_scalp_levels(
        direction="LONG",
        entry=1.1001,
        vp=vp,
        setup_type="mean_reversion",
        symbol_info={"digits": 5, "point": 0.00001},
        asset_type="forex",
    )
    assert levels["tp1"] == levels["tp_partial"]
    assert levels["rr"] == 1.0
    assert levels["rr_below_min"] is False
    assert levels["structural_tp"] == round(vp["poc"], 5)
    assert levels["structure_target_close"] is True


def test_bybit_scalp_exec_uses_tp1_for_exchange_tp(monkeypatch):
    """Engine D Bybit protective TP should be set at TP1."""
    captured = {"tp": None}

    class _X:
        def fetch_ticker(self, symbol):
            return {"ask": 100.0, "bid": 100.0, "last": 100.0}
        def create_market_order(self, symbol, side, amount, params=None):
            return {"id": "ord-1", "average": 100.0, "price": 100.0, "filled": amount}

    monkeypatch.setattr(bybit_executor, "_get_exchange", lambda: _X())
    monkeypatch.setattr(bybit_executor, "bybit_map_symbol", lambda pair: "BTC/USDT:USDT")
    monkeypatch.setattr(bybit_executor, "_ensure_leverage", lambda *args, **kwargs: None)
    monkeypatch.setattr(bybit_executor, "_validate_exit_levels", lambda *args, **kwargs: None)
    monkeypatch.setattr(bybit_executor, "_entry_slippage_bps", lambda *args, **kwargs: (100.0, 0.0))
    monkeypatch.setattr(bybit_executor.telegram_notify, "notify_trade_opened", lambda **kwargs: None)
    monkeypatch.setattr(
        bybit_executor,
        "_set_trading_stop",
        lambda exchange, ccxt_symbol, sl=0, tp=0: captured.__setitem__("tp", tp),
    )

    approval = RiskApproval(
        approved=True,
        volume=0.01,
        risk_amount=10.0,
        risk_pct=0.01,
        portfolio_heat=0.01,
        drawdown_pct=0.0,
        reason="OK",
    )
    signal = {
        "pair": "BTC/USDT",
        "direction": "LONG",
        "price": 100.0,
        "sl": 99.0,
        "tp1": 101.0,
        "tp2": 103.0,
        "engine": "scalp",
    }
    result = bybit_executor.bybit_execute(signal, approval)
    assert result.get("success") is True
    assert captured["tp"] == signal["tp1"]

def test_bybit_symbol_info_precision_extraction(monkeypatch):
    """Verify that bybit_executor correctly extracts digits from float tick sizes."""
    import bybit_executor
    
    # Mock CCXT market data for HBAR
    mock_market = {
        "precision": {"price": 0.0001, "amount": 1.0},
        "limits": {"amount": {"min": 1}}
    }
    mock_ticker = {"last": 0.0912}
    
    # Standard mocks for bybit_get_symbol_info dependencies
    mock_exchange = MagicMock()
    mock_exchange.market.return_value = mock_market
    mock_exchange.fetch_ticker.return_value = mock_ticker
    
    with patch("bybit_executor._get_exchange", return_value=mock_exchange):
        with patch("bybit_executor.bybit_map_symbol", return_value="HBAR/USDT:USDT"):
            info = bybit_executor.bybit_get_symbol_info("HBAR/USDT")
            
            # The new logic should correctly identify 4 decimal places
            assert info["digits"] == 4, f"Expected 4 digits for 0.0001 tick size, got {info['digits']}"
            assert info["point"] == 0.0001
            assert info["trade_tick_size"] == 0.0001
