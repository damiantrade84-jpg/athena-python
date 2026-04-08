import re

with open('scanner.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Replace the first chunk in scanner.py
pattern1 = re.compile(r'    signal\["newsCtx"\] = news_ctx\s+diagnostics = \[\]', re.MULTILINE)
replacement1 = '''    signal["newsCtx"] = news_ctx

    if CONFIG.get("EVENT_RISK_ENABLED", True):
        try:
            from event_risk import check_event_risk
            
            ev_risk = check_event_risk(pair.get("display", ""), pair.get("type", ""), lookahead_hours=CONFIG.get("EVENT_RISK_HOURS", 4))
            signal["macroEventRisk"] = {
                "blocked": not ev_risk.get("allowed", True),
                "reason": ev_risk.get("reason", ""),
                "events": ev_risk.get("events", [])
            }
        except Exception as e:
            log.warning(f"Error checking macro event risk for scan: {e}")
            signal["macroEventRisk"] = {"blocked": False, "reason": "Error checking macro events", "events": []}

    diagnostics = []'''

text = pattern1.sub(replacement1, text)

# Replace the second chunk in scanner.py
pattern2 = re.compile(r'    if signal\["eventRisk"\].get\("hardBlock"\):\n\s+diagnostics\.append\(\n\s+\{\n\s+"code": "event_risk",\n\s+"detail": ", "\.join\(signal\["eventRisk"\]\.get\("reasons", \[\]\)\),\n\s+\}\n\s+\)', re.MULTILINE)
replacement2 = '''    if signal["eventRisk"].get("hardBlock"):
        diagnostics.append(
            {
                "code": "event_risk",
                "detail": ", ".join(signal["eventRisk"].get("reasons", [])),
            }
        )

    if signal.get("macroEventRisk", {}).get("blocked"):
        diagnostics.append(
            {
                "code": "macro_event_risk",
                "detail": signal["macroEventRisk"].get("reason", "Blocked by macro event"),
            }
        )'''

text = pattern2.sub(replacement2, text)

with open('scanner.py', 'w', encoding='utf-8', newline='\n') as f:
    f.write(text)

with open('scoring.py', 'r', encoding='utf-8') as f:
    stext = f.read()

pattern3 = re.compile(r'    hard_event = signal\.get\("eventRisk", \{\}\)\.get\("hardBlock", False\)\n\s+exchange_closed = signal\.get\("exchangeClosed", False\)\n\s+if \(\n\s+pair\.get\("enabled", True\)\n\s+and not exchange_closed\n\s+and not hard_event\n\s+and score >= threshold\n\s+\):', re.MULTILINE)

replacement3 = '''    hard_event = signal.get("eventRisk", {}).get("hardBlock", False)
    macro_event_blocked = signal.get("macroEventRisk", {}).get("blocked", False)
    exchange_closed = signal.get("exchangeClosed", False)
    if (
        pair.get("enabled", True)
        and not exchange_closed
        and not hard_event
        and not macro_event_blocked
        and score >= threshold
    ):'''

stext = pattern3.sub(replacement3, stext)

pattern4 = re.compile(r'    if score >= threshold and \(exchange_closed or hard_event\):\n\s+return "watchlist", "; "\.join\(reasons\) or "Blocked by exchange/event risk"', re.MULTILINE)
replacement4 = '''    if score >= threshold and (exchange_closed or hard_event or macro_event_blocked):
        return "watchlist", "; ".join(reasons) or "Blocked by exchange/event risk"'''

stext = pattern4.sub(replacement4, stext)

with open('scoring.py', 'w', encoding='utf-8', newline='\n') as f:
    f.write(stext)
