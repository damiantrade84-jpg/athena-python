import sys
import re

with open('c:/Users/damia/OneDrive/Desktop/athena-python/backtest_runner.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Signatures
text = text.replace('def backtest_pair(pair, style="auto"):', 'def backtest_pair(pair, style="auto", validation_mode="standard", purge_gap=200, folds=3):')
text = text.replace('def backtest_pair_naked(pair: dict, style: str = "naked"):', 'def backtest_pair_naked(pair: dict, style: str = "naked", validation_mode="standard", purge_gap=200, folds=3):')

# 2. Extract slippage logic for live_parity
text = re.sub(
    r'(slip = raw_entry \* _get_slippage_for_bar\(entry_bar, _ptype\)\s*entry = raw_entry \+ slip if direction == "LONG" else raw_entry - slip)',
    r'_slip_mult = 3.0 if validation_mode == "live_parity" else 1.0\n            slip = raw_entry * _get_slippage_for_bar(entry_bar, _ptype) * _slip_mult\n            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip',
    text
)

# 3. OOS logic (the tricky part)
def repl_oos_loop(m):
    return '''        _oos_start = MIN_BARS + int((total_bars - MIN_BARS) * 0.7)
        _purge_start = _oos_start - (purge_gap if validation_mode == "embargoed" else 0)
        _fold_size = int((total_bars - MIN_BARS) / max(1, folds)) if validation_mode in ("walk_forward", "walk_forward_cv") else 0

        while i < total_bars - 1:
            if validation_mode == "embargoed" and _purge_start <= i < _oos_start:
                i += 1
                continue
            
            if validation_mode == "walk_forward" and _fold_size > 0:
                _current_fold = min(folds - 1, int((i - MIN_BARS) / _fold_size))
                _fold_oos_start = MIN_BARS + _current_fold * _fold_size + int(_fold_size * 0.7)
                _purge_fold_start = _fold_oos_start - purge_gap
                if _purge_fold_start <= i < _fold_oos_start:
                    i += 1
                    continue
                _oos_start = _fold_oos_start'''

text = re.sub(r'\s*_oos_start = MIN_BARS \+ int\(\(total_bars - MIN_BARS\) \* 0\.7\)\s*while i < total_bars - 1:', repl_oos_loop, text)

# 4. Record validation_mode and oos
text = re.sub(
    r'("oos": i >= _oos_start,)',
    r'\1\n                "validation_mode": validation_mode,',
    text
)

# 5. Enrich summary block
enrich_old = 'return enrich_backtest_summary(result, returns=r_values)'
enrich_new = '''
    # --- REGIME SEGMENTED REPORTING & RESEARCH FOLDS ---
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
# Fix indentation dynamically
lines = text.split('\n')
out = []
for line in lines:
    if line.strip() == 'return enrich_backtest_summary(result, returns=r_values)':
        indent = line[:len(line) - len(line.lstrip())]
        block = enrich_new.split('\n')
        for b in block:
            if b.strip() == '':
                out.append('')
            else:
                out.append(indent + b[4:] if b.startswith('    ') else indent + b)
    else:
        out.append(line)

final_text = '\n'.join(out)

with open('c:/Users/damia/OneDrive/Desktop/athena-python/backtest_runner.py', 'w', encoding='utf-8') as f:
    f.write(final_text)

# verify python compilation
import py_compile
try:
    py_compile.compile('c:/Users/damia/OneDrive/Desktop/athena-python/backtest_runner.py', doraise=True)
    print("SUCCESS: syntax is valid")
except py_compile.PyCompileError as e:
    print("SYNTAX ERROR:", e)

