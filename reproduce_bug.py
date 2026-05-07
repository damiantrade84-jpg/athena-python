import math
import logging
import sys
import os

# Mock CONFIG
CONFIG = {
    "RANGING": {"index": {"dead": 15}},
    "TRENDING_ADX": 35,
    "DEVELOPING_ADX": 25,
    "REGIME_WEIGHTS": {
        "TRENDING": {"trend": 2.0, "momentum": 1.5}
    },
    "FACTOR_DIRECTIONAL_SOFT_SPAN": 0.20,
    "FACTOR_MIN_DIRECTIONAL": 0.25
}

# Add current dir to path to import local modules
sys.path.insert(0, os.getcwd())

# Mock logging
logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("athena")

from factor_scoring import compute_factor_scores

def reproduce():
    pair = {"display": "UK100", "type": "index"}
    d1_snap = {"ema21": 7500, "ema50": 7400, "close": 7500}
    h4_snap = {"ema21": 7500, "ema50": 7400, "close": 7500, "adx_z": 1.0, "atr_z": 0.5, "bbWidth_z": 0.5}
    h1_snap = {"ema21": 7500, "ema50": 7400, "close": 7500}
    
    h4_candles = [{"time": "2026-04-16T08:00:00Z", "open": 7400, "high": 7550, "low": 7350, "close": 7500, "vol": 1000}] * 100
    
    print("Executing compute_factor_scores...")
    try:
        res = compute_factor_scores(
            d1_snap=d1_snap,
            h4_snap=h4_snap,
            h1_snap=h1_snap,
            pair=pair,
            d1_candles=h4_candles,
            h4_candles=h4_candles,
            h1_candles=h4_candles,
            volume_ratio=1.2,
            bar_time="2026-04-16T08:00:00Z"
        )
        print("Success!")
        print(f"Score: {res['final_score']}")
    except Exception as e:
        print(f"FAILED with error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    reproduce()
