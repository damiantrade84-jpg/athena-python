import sys
import os

# Mock CONFIG before any imports
os.environ["CONFIG_FILE"] = "config.yaml"

from factor_scoring import compute_factor_scores
from config import CONFIG

def test_debug_index():
    # Mock index data
    d1_snap = {"ema200": 7000, "close": 7500, "adx": 30}
    h4_snap = {"ema50": 7200, "ema200": 7000, "adx": 30, "atr": 50}
    h1_snap = {"close": 7450, "ema21": 7400, "ema50": 7300}
    
    indicators = {
        "ema_trend": 1.0,  # strong trend
        "rsi_z": 0.5,
        "adx_z": 1.0,
        "atr_z": 0.5
    }
    
    factor_scores = {
        "trend": 1.5,
        "momentum": 0.8,
        "volatility": 0.5,
        "volume": 0.5
    }
    
    pair = {"display": "UK100", "type": "index"}
    
    result = compute_factor_scores(
        asset_type="index",
        regime="TRENDING",
        indicators=indicators,
        factor_scores=factor_scores,
        pair=pair,
        h4_snap=h4_snap
    )
    
    print(f"Final Score: {result['final_score']}")
    print(f"Direction: {result['direction']}")
    print(f"Active Dir Factors: {result['active_directional_factors']}")
    print(f"Dir Score: {result['directional_score']}")
    print(f"Nondir Score: {result['nondirectional_score']}")

if __name__ == "__main__":
    test_debug_index()
