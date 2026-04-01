
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is in path
sys.path.insert(0, os.getcwd())

import athena
from config import CONFIG

class TestBug5(unittest.TestCase):
    def setUp(self):
        # Set a low MAX_SL_PCT for forex to trigger rejection easily in test
        CONFIG["MAX_SL_PCT"]["forex"] = 0.02 
        self.pair = {"display": "EUR/USD", "type": "forex", "symbol": "EURUSD"}
        
    @patch("athena.fetch_candles")
    @patch("athena.calc_indicators")
    @patch("athena.calc_confluence")
    @patch("athena.calc_levels")
    @patch("athena._atr_for_levels")
    @patch("athena.get_pair_profile")
    @patch("athena.get_pair_score_group")
    @patch("athena.calc_fib")
    def test_structural_sl_rejection(self, mock_fib, mock_score_group, mock_profile, mock_atr, mock_levels, mock_confluence, mock_indicators, mock_fetch):
        # Mocking necessary outputs
        mock_profile.return_value = {"min_score": 0.5}
        mock_score_group.return_value = "forex_majors"
        mock_fetch.return_value = [{"close": 1.10, "high": 1.11, "low": 1.09}] * 100
        mock_indicators.return_value = {"snap": {"close": 1.10, "atr": 0.01}}
        mock_confluence.return_value = {"score": 0.9, "direction": "LONG", "warnings": [], "maxScoreOverride": 1.0}
        mock_levels.return_value = {"sl": 1.09, "tp1": 1.12, "tp2": 1.15} # Base SL is 1% (1.10 -> 1.09)
        mock_atr.return_value = 0.01
        mock_fib.return_value = {"highest": 1.15, "lowest": 1.05}
        
        # We need to mock market_structure.engine
        mock_engine = MagicMock()
        mock_engine.set_registry_context.return_value = mock_engine
        
        # CASE 1: Wide structural stop (1.10 -> 1.05 = 4.5% distance). Max allowed is 2%
        mock_engine.analyze_structure.return_value = {
            "structural_verdict": "CLEAR",
            "recommended_stop_loss": 1.05, # Distance = 0.05 / 1.10 approx 4.5%
            "distance_to_res": 0.1
        }
        
        print("\nTesting Case 1: Structural stop (4.5%) exceeds MAX_SL_PCT (2.0%)")
        with patch("market_structure.engine", mock_engine):
            with patch("athena.use_naked_engine", True):
                # Mock athena.use_naked_engine variable? No, it's a param to analyze_pair
                result = athena.analyze_pair(self.pair, use_naked_engine=True)
                
                print(f"  Result: {result}")
                self.assertIsNone(result, "FAILED: analyze_pair should return None when structural stop is too wide.")
                print("  Correctly REJECTED.")

        # CASE 2: Valid structural stop (1.10 -> 1.085 = 1.36% distance)
        mock_engine.analyze_structure.return_value = {
            "structural_verdict": "CLEAR",
            "recommended_stop_loss": 1.085, # Distance = 0.015 / 1.10 approx 1.36%
            "distance_to_res": 0.1
        }
        print("\nTesting Case 2: Structural stop (1.36%) within MAX_SL_PCT (2.0%)")
        with patch("market_structure.engine", mock_engine):
            result = athena.analyze_pair(self.pair, use_naked_engine=True)
            
            print(f"  Result is None? {result is None}")
            self.assertIsNotNone(result, "FAILED: analyze_pair should return signal when stop is valid.")
            print("  Correctly APPROVED.")

if __name__ == "__main__":
    unittest.main()
