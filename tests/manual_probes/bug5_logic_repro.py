
import unittest
from unittest.mock import MagicMock

# The logic we are testing from athena.py
def sl_logic_under_test(price, lvl_sl, direction, asset_type, structure_data, config):
    # Safest Stop Loss Override (Combined Risk Management)
    if structure_data.get("recommended_stop_loss"):
        _struct_sl = float(structure_data["recommended_stop_loss"])
        _math_sl = float(lvl_sl)
        # LONG: SL below price — max() picks closer (higher) = tighter
        # SHORT: SL above price — min() picks closer (lower) = tighter
        new_sl = (
            max(_math_sl, _struct_sl)
            if direction == "LONG"
            else min(_math_sl, _struct_sl)
        )
    else:
        new_sl = lvl_sl

    # Hard SL distance cap — prevents runaway structural overrides
    _max_sl_pct = config.get("MAX_SL_PCT", {}).get(asset_type, 0.05)
    _sl_dist_pct = abs(float(price) - float(new_sl)) / float(price)
    
    if _sl_dist_pct > _max_sl_pct:
        # This is where BUG 5 fix happens
        # Old behavior: clamp and recompute TPs
        # New behavior: return None (REJECTED)
        print(f"  [REPRO] REJECTED: dist {_sl_dist_pct:.1%} > cap {_max_sl_pct:.1%}")
        return None
    
    return {"sl": new_sl}

class TestBug5Logic(unittest.TestCase):
    def test_rejection_logic(self):
        config = {"MAX_SL_PCT": {"forex": 0.02}}
        price = 1.10
        math_sl = 1.05 # 4.5% SL
        
        # Scenario 1: Structural SL is 1.00 (approx 9% distance)
        # Max allowed is 2%. Math SL is already 4.5%.
        # max(1.05, 1.00) = 1.05.
        struct_data = {"recommended_stop_loss": 1.00}
        print("\nTesting Case 1: Resulting stop (4.5%) exceeds MAX_SL_PCT (2.0%)")
        result = sl_logic_under_test(price, math_sl, "LONG", "forex", struct_data, config)
        self.assertIsNone(result, "Logic should return None (REJECTED) for over-wide resulting stop.")
        
        # Scenario 2: Math SL is fine (1.09 = 0.9%), Structural SL is too wide but IGNORED
        math_sl_ok = 1.09
        struct_data_wide = {"recommended_stop_loss": 1.00}
        print("\nTesting Case 2: Math stop (0.9%) is valid, Structural stop (9%) is IGNORED by max()")
        result = sl_logic_under_test(price, math_sl_ok, "LONG", "forex", struct_data_wide, config)
        self.assertIsNotNone(result, "Logic should return Result (APPROVED) because max() picked math_sl.")
        self.assertEqual(result["sl"], 1.09)

        # Scenario 3: Math SL is fine (1.09), but Structural SL is TIGHTER (1.095)
        struct_data_tight = {"recommended_stop_loss": 1.095}
        print("\nTesting Case 3: Structural stop (1.095) is TIGHTER than math stop (1.09)")
        result = sl_logic_under_test(price, math_sl_ok, "LONG", "forex", struct_data_tight, config)
        self.assertIsNotNone(result)
        self.assertEqual(result["sl"], 1.095)

if __name__ == "__main__":
    unittest.main()
