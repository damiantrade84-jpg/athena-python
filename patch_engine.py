import sys

with open('c:/Users/damia/OneDrive/Desktop/athena-python/engine_c.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    new_lines.append(line)
    if '\"style\": signal_a.get(\"style\"' in line:
        new_lines.append('        \"confidence\": float(signal_a.get(\"confidenceDetail\", {}).get(\"confidence\", 0.5)),\n')

with open('c:/Users/damia/OneDrive/Desktop/athena-python/engine_c.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
