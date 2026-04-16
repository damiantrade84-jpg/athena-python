import os
import sys
import logging

# Add current dir to path
sys.path.insert(0, os.getcwd())

from config import CONFIG
from scoring import calc_confluence
from athena_runtime import _rt

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("athena")

def debug_backtest_scoring():
    pair = {"symbol": "UK100", "display": "UK100", "type": "index", "source": "mt5"}
    
    print(f"Fetching candles for {pair['display']}...")
    d1_raw = _rt().fetch_candles(pair, "D1", 750)
    h4_raw = _rt().fetch_candles(pair, "H4", 4400)
    h1_raw = _rt().fetch_candles(pair, "H1", 17600)
    
    print(f"D1: {len(d1_raw or [])}, H4: {len(h4_raw or [])}, H1: {len(h1_raw or [])}")
    
    if not (d1_raw and h4_raw and h1_raw):
        print("Missing data - cannot proceed.")
        return

    # Mock indicators and snaps for the first valid bar
    # In backtest_runner.py, it pre-calcs indices.
    try:
        from indicators import calc_indicators, calc_indicators_d1, calc_volume_ratio, calc_stochastic
    except ImportError as e:
        print(f"Import error: {e}")
        return

    # Simulate one iteration of the backtest loop
    i = 200 # Skip initial bars
    h1_window = h1_raw[:i+1]
    
    try:
        # This is a simplified version of backtest_runner.py's window approach
        d1i = calc_indicators_d1(d1_raw[:50]) # Simplified
        h4i = calc_indicators(h4_raw[:50])
        h1i = calc_indicators(h1_window)
        
        print("Calling calc_confluence...")
        res = calc_confluence(
            d1i, h4i, h1i,
            1.0, 50.0, # vr, stoch
            pair, "neutral",
            d1_candles=d1_raw[:50],
            h4_candles=h4_raw[:50],
            h1_candles=h1_window,
            volume_threshold=1.5
        )
        print(f"Result: {res['score']}")
    except Exception as e:
        print(f"EXCEPTION: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_backtest_scoring()
