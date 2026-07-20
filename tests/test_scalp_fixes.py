import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

# Modules under test
import timed_exit_monitor
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

def test_scalp_trades_excluded_from_timed_exit_when_profit_protect_off(monkeypatch):
    """Engine D is ignored by timed-exit when LIVE_PROFIT_PROTECT is disabled."""
    row = {
        "pair": "EURUSD",
        "direction": "long",
        "entry_price": 1.0500,
        "ticket": 12345,
        "engine": "scalp",
        "ts": "2026-04-14T10:00:00Z",
    }

    cfg = {
        "scalp": {"breakeven_min": 5, "close_min": 10},
        "intraday": {},
        "swing": {},
        "scalp_live_profit_protect": False,
    }

    with patch("mt5_executor.mt5_get_positions") as mock_get_pos:
        mock_get_pos.return_value = {
            "error": None,
            "positions": [{"ticket": 12345, "sl": 1.0400, "tp": 1.0600, "profit": 0.0, "volume": 1.0}],
        }
        timed_exit_monitor._handle_mt5_row(row, cfg)
        mock_get_pos.assert_called_once()

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
# The three backtest_pair_scalp tests were removed with the legacy backtester
# (archive/backtest_legacy/): scalp backtesting is retired — the v3 rebuild
# covers Engine A/B only and /api/backtest-scalp returns 410 GONE.

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
            "MIN_RR": 1.0,
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


def test_scalp_rr_below_min_enforces_configured_min_rr(monkeypatch):
    monkeypatch.setitem(
        scalp_engine.CONFIG,
        "SCALP_ENGINE",
        {
            **scalp_engine.CONFIG.get("SCALP_ENGINE", {}),
            "MIN_RR": 1.2,
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
    assert levels["rr"] == 1.0
    assert levels["min_rr"] == 1.2
    assert levels["tp_direction_ok"] is True
    assert levels["rr_below_min"] is True


def test_bybit_scalp_exec_uses_tp1_for_exchange_tp(monkeypatch):
    """Engine D Bybit protective TP should be set at TP1."""
    captured = {"tp": None}

    class _X:
        def fetch_ticker(self, symbol):
            return {"ask": 100.0, "bid": 100.0, "last": 100.0}
        def create_market_order(self, symbol, side, amount, params=None):
            return {"id": "ord-1", "status": "closed", "average": 100.0, "price": 100.0, "filled": amount}

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
