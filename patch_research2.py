import sys
import re

with open('c:/Users/damia/OneDrive/Desktop/athena-python/backtest_runner.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Make sure all signatures are patched
sig1 = 'def backtest_pair(pair, style="auto"):'
sig2 = 'def backtest_pair(pair, style="auto", validation_mode="standard", purge_gap=200, folds=3):'
text = text.replace(sig1, sig2)

sig3 = 'def backtest_pair_naked(pair: dict, style: str = "naked"):'
sig4 = 'def backtest_pair_naked(pair: dict, style: str = "naked", validation_mode="standard", purge_gap=200, folds=3):'
text = text.replace(sig3, sig4)

# Replace OOS loops globally where matching
oos_old1 = '''        _oos_start = MIN_BARS + int((total_bars - MIN_BARS) * 0.7)

        while i < total_bars - 1:'''
oos_new1 = '''        _oos_start = MIN_BARS + int((total_bars - MIN_BARS) * 0.7)
        _purge_start = _oos_start - (purge_gap if validation_mode == "embargoed" else 0)
        _fold_size = int((total_bars - MIN_BARS) / folds) if validation_mode in ("walk_forward", "walk_forward_cv") else 0

        while i < total_bars - 1:
            if validation_mode == "embargoed" and _purge_start <= i < _oos_start:
                i += 1
                continue
            
            if validation_mode == "walk_forward":
                _current_fold = min(folds - 1, int((i - MIN_BARS) / max(1, _fold_size)))
                _fold_oos_start = MIN_BARS + _current_fold * _fold_size + int(_fold_size * 0.7)
                _purge_fold_start = _fold_oos_start - purge_gap
                if _purge_fold_start <= i < _fold_oos_start:
                    i += 1
                    continue
                _oos_start = _fold_oos_start
'''

# We also need to get slippage replaced universally
text = re.sub(
    r'(slip = raw_entry \* _get_slippage_for_bar\(entry_bar, _ptype\)\s*\n\s*entry = raw_entry \+ slip if direction == "LONG" else raw_entry - slip)',
    r'_slip_mult = 3.0 if validation_mode == "live_parity" else 1.0\n            slip = raw_entry * _get_slippage_for_bar(entry_bar, _ptype) * _slip_mult\n            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip',
    text
)

# Replace OOS inside the trade dict builder. 
# It looks like: '"oos": i >= _oos_start,'
# Let's replace it with '"oos": i >= _oos_start,\n                "validation_mode": validation_mode,'
text = re.sub(
    r'("oos": i >= _oos_start,)',
    r'\1\n                "validation_mode": validation_mode,',
    text
)

# Now, Regime Segmented Reporting at the bottom of the functions.
# Search for summary = { and inject the research metrics + regime loop

# Wait, the summary object is constructed before eturn enrich_backtest_summary(result, returns=r_values)
# For Engine A summary is called esult.
# Let's inject logic right before eturn enrich_backtest_summary(result, returns=r_values)
enrich_target = 'return enrich_backtest_summary(result, returns=r_values)'
new_enrich = '''
    # --- REGIME SEGMENTED REPORTING & WALK FORWARD FOLDS ---
    regimes = {}
    is_vals, oos_vals = [], []
    for t in trades:
        rgm = t.get("regime", "UNKNOWN")
        r_mult = t.get("r_multiple", 0)
        if rgm not in regimes:
            regimes[rgm] = {"trades": 0, "wins": 0, "r_sum": 0.0}
        regimes[rgm]["trades"] += 1
        regimes[rgm]["r_sum"] += r_mult
        if r_mult > 0:
            regimes[rgm]["wins"] += 1
            
        if t.get("oos"):
            oos_vals.append(r_mult)
        else:
            is_vals.append(r_mult)
            
    for k, v in regimes.items():
        v["win_rate"] = round(v["wins"] / max(1, v["trades"]), 4)
        v["expectancy"] = round(v["r_sum"] / max(1, v["trades"]), 4)
        
    result["regime_performance"] = regimes
    result["validation_mode"] = validation_mode

    return enrich_backtest_summary(result, returns=r_values, in_sample_scores=is_vals, out_of_sample_scores=oos_vals, chosen_index=0)
'''

text = text.replace(enrich_target, new_enrich)

text = text.replace(oos_old1, oos_new1)

with open('c:/Users/damia/OneDrive/Desktop/athena-python/backtest_runner.py', 'w', encoding='utf-8') as f:
    f.write(text)
