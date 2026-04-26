with open('scalp_engine.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

out = []
for l in lines:
    if '_funnel["gate_result"]' in l and ' = ' in l:
        continue
    if '_funnel["fail_reasons"].append(' in l:
        continue
    out.append(l)

with open('scalp_engine.py', 'w', encoding='utf-8') as f:
    f.writelines(out)
print('cleaned')
