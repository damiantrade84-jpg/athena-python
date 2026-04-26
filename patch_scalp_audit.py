"""Patch scalp_engine.py to add _funnel gate logging before every continue in run_scalp_scan loop."""
import re

with open('scalp_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

in_func = False
func_indent = None
in_for = False
for_indent = None
out_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    out_lines.append(line)
    if 'def run_scalp_scan(' in line:
        in_func = True
        func_indent = len(line) - len(line.lstrip())
        i += 1
        continue
    if in_func:
        indent = len(line) - len(line.lstrip())
        if line.strip().startswith('def ') and indent <= func_indent:
            in_func = False
            in_for = False
            i += 1
            continue
        if 'for display in pairs_or_symbols:' in line:
            in_for = True
            for_indent = len(line) - len(line.lstrip())
            i += 1
            continue
        if in_for:
            if line.strip() == 'continue':
                reason = None
                for j in range(len(out_lines) - 1, -1, -1):
                    prev = out_lines[j]
                    if 'skipped.append' in prev and 'reason' in prev:
                        m = re.search(r'"reason":\s*([^\n\)]+)', prev)
                        if m:
                            reason = m.group(1).strip().rstrip(')').rstrip(',').strip()
                        break
                if reason:
                    gate = 'BLOCKED'
                    rlower = reason.lower()
                    if any(k in rlower for k in ['insufficient_', 'market_closed', 'market_data_stale', 'no_mt5_mapping', 'symbol_not_available', 'htf_bias_unavailable']):
                        gate = 'DATA_MISSING'
                    elif 'no_setup' in rlower:
                        gate = 'NO_SETUP'
                    elif 'grade_' in rlower and '_below_min' in rlower:
                        gate = 'BLOCKED'
                    elif 'rr_below_min' in rlower:
                        gate = 'BLOCKED'
                    elif 'counter_trend' in rlower:
                        gate = 'BLOCKED'
                    indent_str = ' ' * indent
                    out_lines.insert(-1, indent_str + '_funnel["gate_result"] = "' + gate + '"\n')
                    out_lines.insert(-1, indent_str + '_funnel["fail_reasons"].append(' + reason + ')\n')
            i += 1
            continue
    i += 1

with open('scalp_engine.py', 'w', encoding='utf-8') as f:
    f.writelines(out_lines)

print('patch done')
