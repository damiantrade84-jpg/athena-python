import sys

with open('c:/Users/damia/OneDrive/Desktop/athena-python/backtest_runner.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Fix the slippage indentation error globally
import re

def fix_slip(m):
    # m.group(1) is the leading whitespace
    indent = m.group(1)
    # The previous bad replacement hardcoded '            ' 
    return f"{indent}slip = raw_entry * _get_slippage_for_bar(entry_bar, _ptype) * _slip_mult\n{indent}entry = raw_entry + slip if direction == \"LONG\" else raw_entry - slip"

# The broken text has:
bad_block = '''        _slip_mult = 3.0 if validation_mode == "live_parity" else 1.0
            slip = raw_entry * _get_slippage_for_bar(entry_bar, _ptype) * _slip_mult
            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip'''
good_block = '''        _slip_mult = 3.0 if validation_mode == "live_parity" else 1.0
        slip = raw_entry * _get_slippage_for_bar(entry_bar, _ptype) * _slip_mult
        entry = raw_entry + slip if direction == "LONG" else raw_entry - slip'''
text = text.replace(bad_block, good_block)

# Let's fix the other occurrence (the one in 12 spaces)
bad_block_12 = '''            _slip_mult = 3.0 if validation_mode == "live_parity" else 1.0
            slip = raw_entry * _get_slippage_for_bar(entry_bar, _ptype) * _slip_mult
            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip'''
good_block_12 = '''            _slip_mult = 3.0 if validation_mode == "live_parity" else 1.0
            slip = raw_entry * _get_slippage_for_bar(entry_bar, _ptype) * _slip_mult
            entry = raw_entry + slip if direction == "LONG" else raw_entry - slip'''
            
# The problem was my regex produced 8 spaces for _slip_mult and 12 spaces for slip.
# Let's just find _slip_mult = 3.0 and equalize the indent of the next two lines
lines = text.split('\n')
out = []
i = 0
while i < len(lines):
    line = lines[i]
    if '_slip_mult = 3.0 if validation_mode == "live_parity" else 1.0' in line:
        indent = line[:len(line) - len(line.lstrip())]
        out.append(line)
        l2 = lines[i+1].lstrip()
        l3 = lines[i+2].lstrip()
        out.append(indent + l2)
        out.append(indent + l3)
        i += 3
        continue
    out.append(line)
    i += 1

final_text = '\n'.join(out)

with open('c:/Users/damia/OneDrive/Desktop/athena-python/backtest_runner.py', 'w', encoding='utf-8') as f:
    f.write(final_text)

import py_compile
try:
    py_compile.compile('c:/Users/damia/OneDrive/Desktop/athena-python/backtest_runner.py', doraise=True)
    print("SUCCESS")
except py_compile.PyCompileError as e:
    print("SYNTAX ERROR:", e)

