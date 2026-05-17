"""Dev helper: grep static/index.html for Engine B UI hooks — not the market-structure scanner.

Production Engine B entry points: scanner.py, athena._compute_naked_analysis, execution.py.
"""
import sys
with open('static/index.html', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f, 1):
        if 'engine_b_overlay' in line.lower() or 'engine_b_verdict' in line.lower():
            sys.stdout.buffer.write(f"static/index.html:{i}: {line.strip()}\n".encode('utf-8'))
