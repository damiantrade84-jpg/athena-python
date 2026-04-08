import re
import os

with open('athena.py', 'r', encoding='utf-8') as f:
    text = f.read()

text = re.sub(r'\s+if "forex" in current:\n\s+content = _re\.sub\(\n\s+r"\^\(MIN_FOREX_CONFLUENCE\\s\*:\\s\*\)\[\\d\.\]\+",\n\s+lambda mm, v=current\["forex"\]: f"\{mm\.group\(1\)\}\{v\}",\n\s+content,\n\s+count=1,\n\s+flags=_re\.MULTILINE,\n\s+\)', '', text)

text = re.sub(r'\s+if "forex" in current:\n\s+CONFIG\["MIN_FOREX_CONFLUENCE"\] = round\(float\(current\["forex"\]\), 4\)', '', text)
text = re.sub(r'\s+"min_forex_confluence": CONFIG\.get\("MIN_FOREX_CONFLUENCE"\),', '', text)

with open('athena.py', 'w', encoding='utf-8', newline='\n') as f:
    f.write(text)

with open('config.py', 'r', encoding='utf-8') as f:
    text = f.read()
text = re.sub(r'\s+"MIN_FOREX_CONFLUENCE": 1\.0,\n', '\n', text)
with open('config.py', 'w', encoding='utf-8', newline='\n') as f:
    f.write(text)

with open('config.yaml', 'r', encoding='utf-8') as f:
    text = f.read()
text = re.sub(r'\s+MIN_FOREX_CONFLUENCE: 1\.0\n', '\n', text)
text = re.sub(r'\s+# Forex engine outputs 0–2\.0 — keep forex entry on that scale \(see MIN_FOREX_CONFLUENCE\)\.', '', text)
with open('config.yaml', 'w', encoding='utf-8', newline='\n') as f:
    f.write(text)

with open('forex_scoring.py', 'r', encoding='utf-8') as f:
    text = f.read()
text = re.sub(r'\s+# Threshold: MIN_FOREX_CONFLUENCE / MIN_CONFLUENCE_CLASS\.forex \(see config\.yaml\)\n', '\n', text)
with open('forex_scoring.py', 'w', encoding='utf-8', newline='\n') as f:
    f.write(text)

if os.path.exists('tests/test_athena.py'):
    with open('tests/test_athena.py', 'r', encoding='utf-8') as f:
        text = f.read()
    text = re.sub(r'\s+assert CONFIG\["MIN_FOREX_CONFLUENCE"\] == 1\.0\n', '\n', text)
    with open('tests/test_athena.py', 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
