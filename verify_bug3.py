
import sys
import os
from datetime import datetime
from unittest.mock import MagicMock

# Ensure project root is in path
sys.path.insert(0, os.getcwd())

import config
# Mock CONFIG
config.CONFIG = {
    "FOREX_SESSION_FILTER": True,
    "BACKTEST_IGNORE_SESSIONS": False,
    "FOREX_ENGINE": {"trend_gate_adx_min": 20.0}
}

from forex_scoring import compute_forex_score, ForexScoreResult

def test_bug3_parity():
    print("Testing BUG 3 — Forex session parity...")
    
    # Mock data
    d1_snap = {"close": 1.10, "ema200": 1.05, "adx": 30.0}
    h4_snap = {"ema50": 1.12, "ema200": 1.10, "macdHist": 0.001, "macdHistPrev": 0.0005, "adx": 25.0}
    h1_snap = {"close": 1.11, "ema21": 1.105, "ema50": 1.10, "rsi": 50.0}
    h1_candles = [{"time": "2026-04-01T21:00:00", "close": 1.11}] * 60
    pair = {"display": "EUR/USD", "type": "forex"}
    
    # 1. Off-session hour (UTC 22:00 is off-session for EUR/USD)
    off_session_time = "2026-04-01T22:00:00"
    
    print(f"Case 1: Backtest mode, off-session time ({off_session_time}), ignore_sessions=False")
    res = compute_forex_score(
        d1_snap=d1_snap, h4_snap=h4_snap, h1_snap=h1_snap,
        h1_candles=h1_candles, pair=pair, bar_time=off_session_time,
        backtest_mode=True
    )
    print(f"  Session active: {res.session_active}")
    assert res.session_active is False, "FAILED: Backtest should respect session gate by default."

    # 2. Escape hatch active
    print(f"Case 2: Backtest mode, off-session time ({off_session_time}), ignore_sessions=True")
    config.CONFIG["BACKTEST_IGNORE_SESSIONS"] = True
    res = compute_forex_score(
        d1_snap=d1_snap, h4_snap=h4_snap, h1_snap=h1_snap,
        h1_candles=h1_candles, pair=pair, bar_time=off_session_time,
        backtest_mode=True
    )
    print(f"  Session active: {res.session_active}")
    assert res.session_active is True, "FAILED: Escape hatch should allow bypassing sessions."

    # 3. Live mode (backtest_mode=False) - always respects sessions
    print("Case 3: Live mode, expect session filter to be active (mocking current hour to 22)")
    config.CONFIG["BACKTEST_IGNORE_SESSIONS"] = False # reset
    import forex_scoring
    original_local_to_utc_hour = forex_scoring._local_to_utc_hour
    forex_scoring._local_to_utc_hour = lambda: 22 # force off-session
    
    res = compute_forex_score(
        d1_snap=d1_snap, h4_snap=h4_snap, h1_snap=h1_snap,
        h1_candles=h1_candles, pair=pair, backtest_mode=False
    )
    print(f"  Session active: {res.session_active}")
    assert res.session_active is False, "FAILED: Live mode must respect session gate."
    
    # Restore
    forex_scoring._local_to_utc_hour = original_local_to_utc_hour

    print("\n[SUCCESS] BUG 3 verified: Parity restored and escape hatch works.")

if __name__ == "__main__":
    try:
        test_bug3_parity()
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
