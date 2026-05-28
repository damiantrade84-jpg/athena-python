import sys
import os
import logging
import traceback

# Mock logging
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("athena")

# Add current dir to path
sys.path.insert(0, os.getcwd())

from legacy.forex_scoring import compute_forex_score

def reproduce_forex():
    d1_snap = {"adx": 30, "ema200": 1.05, "close": 1.10}
    h4_snap = {"ema50": 1.08, "ema200": 1.05, "adx": 30}
    h1_snap = {"rsi": 40, "ema21": 1.09, "ema50": 1.085}
    h1_candles = [{"time": "2026-04-16T08:00:00Z", "open": 1.08, "high": 1.10, "low": 1.07, "close": 1.09}] * 100
    
    pair = {"display": "EUR/USD", "type": "forex", "score_group": "forex_majors"}
    
    print("Executing compute_forex_score...")
    try:
        res = compute_forex_score(
            d1_snap=d1_snap,
            h4_snap=h4_snap,
            h1_snap=h1_snap,
            h1_candles=h1_candles,
            pair=pair,
            bar_time="2026-04-16T08:00:00Z",
            backtest_mode=True
        )
        print("Success!")
    except Exception as e:
        print(f"FAILED with error: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    reproduce_forex()
