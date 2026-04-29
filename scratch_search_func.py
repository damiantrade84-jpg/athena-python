import sys
with open('static/index.html', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f, 1):
        if 'function formatconfluencetext' in line.lower():
            print(f"static/index.html:{i}: {line.strip()}")
