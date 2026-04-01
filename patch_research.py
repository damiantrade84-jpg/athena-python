import sys
import re

with open('c:/Users/damia/OneDrive/Desktop/athena-python/backtest_runner.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Update signature of backtest_pair
old_sig = 'def backtest_pair(pair, style="auto"):'
new_sig = 'def backtest_pair(pair, style="auto", validation_mode="standard", purge_gap=200, folds=3):'
if old_sig in text:
    text = text.replace(old_sig, new_sig)

# 2. Update slippage if live_parity is enabled
# Inject inside _get_slippage_for_bar? The flag isn't there. 
# We'll just define that live_parity affects the loop variables.
slippage_old = '''            slip = raw_entry * _get_slippage_for_bar(entry_bar, _ptype)

            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip'''

slippage_new = '''            _slip_mult = 3.0 if validation_mode == "live_parity" else 1.0
            slip = raw_entry * _get_slippage_for_bar(entry_bar, _ptype) * _slip_mult

            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip'''
text = text.replace(slippage_old, slippage_new)


# 3. Add walk-forward / embargo tracking
# We need to tag is_oos properly on each trade.
trade_old = '''                "oos": i >= _oos_start,'''
trade_new = '''                "oos": i >= _oos_start,
                "validation_mode": validation_mode,'''
text = text.replace(trade_old, trade_new)

# How do we actually compute _oos_start and skip embargo?
# Around line 555
oos_start_old = '''        _oos_start = MIN_BARS + int((total_bars - MIN_BARS) * 0.7)

        while i < total_bars - 1:'''
oos_start_new = '''        _oos_start = MIN_BARS + int((total_bars - MIN_BARS) * 0.7)
        _purge_start = _oos_start - (purge_gap if validation_mode == "embargoed" else 0)
        _fold_size = int((total_bars - MIN_BARS) / folds) if validation_mode == "walk_forward" else 0

        while i < total_bars - 1:
            if validation_mode == "embargoed" and _purge_start <= i < _oos_start:
                i += 1
                continue
            
            if validation_mode == "walk_forward":
                # rolling definition of OOS based on fold math
                _current_fold = min(folds - 1, int((i - MIN_BARS) / _fold_size))
                _fold_oos_start = MIN_BARS + _current_fold * _fold_size + int(_fold_size * 0.7)
                _purge_fold_start = _fold_oos_start - purge_gap
                _is_local_oos = i >= _fold_oos_start
                # strict walk-forward embargo
                if _purge_fold_start <= i < _fold_oos_start:
                    i += 1
                    continue
                _oos_start = _fold_oos_start  # dynamically update for the trade logger
'''
text = text.replace(oos_start_old, oos_start_new)

# INTRADAY LOOP update (around 1600 probably, need to find while i < total_bars - 1:)
# Wait, there are multiple while i < total_bars -1 loops.
# Let's replace the intraday loop specifically.
# First, let's see how many matches of "_oos_start" we have.

