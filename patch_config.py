import re

with open('config.py', 'r', encoding='utf-8') as f:
    text = f.read()

pattern1 = re.compile(r'\s+"SENTIMENT_GATE_ENABLED": True,\n\s+"EVENT_RISK_ENABLED": True,\n\s+"EVENT_RISK_HOURS": 4,', re.MULTILINE)
replacement1 = '''    "SENTIMENT_GATE_ENABLED": True,
    "SENTIMENT_API_FAIL_CLOSED": False,
    "EVENT_RISK_ENABLED": True,
    "EVENT_RISK_HOURS": 4,
    "EVENT_RISK_API_FAIL_CLOSED": False,'''
text = pattern1.sub(replacement1, text)

with open('config.py', 'w', encoding='utf-8', newline='\n') as f:
    f.write(text)

with open('config.yaml', 'r', encoding='utf-8') as f:
    stext = f.read()

pattern2 = re.compile(r'# ── EODHD Sentiment Gate ──────────────────────────────────\nSENTIMENT_GATE_ENABLED: true\s+# Check news sentiment before auto-trades\nSENTIMENT_BLOCK_THRESHOLD: 0\.4\s+# Block if sentiment opposes direction by >0\.4\n\n# ── EODHD Event Risk Filter ──────────────────────────────\nEVENT_RISK_ENABLED: true\s+# Block trades near high-impact events \(NFP/CPI/FOMC\)\nEVENT_RISK_HOURS: 4\s+# Hours ahead to check for events', re.MULTILINE)

replacement2 = '''# ── EODHD Sentiment Gate ──────────────────────────────────
SENTIMENT_GATE_ENABLED: true       # Check news sentiment before auto-trades
SENTIMENT_BLOCK_THRESHOLD: 0.4     # Block if sentiment opposes direction by >0.4
SENTIMENT_API_FAIL_CLOSED: false   # If true, blocks auto-execution if the sentiment API fails

# ── EODHD Event Risk Filter ──────────────────────────────
EVENT_RISK_ENABLED: true           # Block trades near high-impact events (NFP/CPI/FOMC)
EVENT_RISK_HOURS: 4                # Hours ahead to check for events
EVENT_RISK_API_FAIL_CLOSED: false  # If true, blocks auto-execution if the event calendar API fails'''

stext = pattern2.sub(replacement2, stext)

with open('config.yaml', 'w', encoding='utf-8', newline='\n') as f:
    f.write(stext)
