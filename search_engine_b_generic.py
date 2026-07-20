"""Dev helper: grep static/index.html for "engine b" text — not the Engine B scan pipeline.

Production Engine B: scanner.py, athena.py, execution.py.
"""
import sys
with open('static/index.html', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f, 1):
        if 'engine b' in line.lower():
            sys.stdout.buffer.write(f"static/index.html:{i}: {line.strip()}\n".encode('utf-8'))
