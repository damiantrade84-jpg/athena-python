import re
import os

print("Applying LOW-02...")
with open("execution.py", "r", encoding="utf-8") as f:
    text = f.read()
text = text.replace(
    '"cot": sig_a.get("votes", {}).get("derivatives"),\n                "carry": sig_a.get("votes", {}).get("carry"),',
    '"cot": sig_a.get("votes", {}).get("derivatives", sig_a.get("cot_boost")),\n                "carry": sig_a.get("votes", {}).get("carry", sig_a.get("carry_boost")),'
)
with open("execution.py", "w", encoding="utf-8", newline="\n") as f:
    f.write(text)

print("Applying MED-02...")
with open("scanner.py", "r", encoding="utf-8") as f:
    text = f.read()

target = '''            # analyze_pair anchored confluencePct to static threshold only — re-anchor to scan gate
            if effective_threshold and effective_threshold > 0:
                _cs = float(sig.get("confluenceScore", 0))
                sig["confluencePct"] = min(
                    100, max(0, round((_cs / effective_threshold) * 67))
                )'''

replacement = '''            # Backend separated UI metrics: explicit progress vs absolute score capacity
            _cs = float(sig.get("confluenceScore", 0))
            if effective_threshold and effective_threshold > 0:
                sig["thresholdProgressPct"] = min(100, max(0, round((_cs / effective_threshold) * 67)))
                sig["confluencePct"] = sig["thresholdProgressPct"] # backward compat
            
            _maxs = float(sig.get("maxScore", 2.0))
            if _maxs > 0:
                sig["scoreNormPct"] = min(100, max(0, round((_cs / _maxs) * 100)))'''

text = text.replace(target, replacement)
with open("scanner.py", "w", encoding="utf-8", newline="\n") as f:
    f.write(text)

with open("static/index.html", "r", encoding="utf-8") as f:
    text = f.read()

text = text.replace(
    "return getConfluenceLabel(pct) + ' (' + pct + '%)';",
    "var pText = getConfluenceLabel(pct) + ' (' + pct + '% to threshold)';\n  if (data.scoreNormPct != null) { pText += ' | ' + data.scoreNormPct + '% abs score'; }\n  return pText;"
)
text = text.replace(
    "var pct = data.confluencePct;",
    "var pct = data.thresholdProgressPct != null ? data.thresholdProgressPct : data.confluencePct;"
)
with open("static/index.html", "w", encoding="utf-8", newline="\n") as f:
    f.write(text)

print("Applying MED-04...")
with open("backtest_runner.py", "r", encoding="utf-8") as f:
    text = f.read()
# Replace JSON keys & dicts
text = text.replace('"btMinUsed"', '"evalThreshold"')
text = text.replace("'btMinUsed'", "'evalThreshold'")
text = text.replace('Avg bt_min', 'Avg eval_threshold')
text = text.replace('Min bt_min', 'Min eval_threshold')
text = text.replace('Max bt_min', 'Max eval_threshold')
# SQLite column rename
text = text.replace(
    'is_score,oos_score,max_dd_pct,bt_min,atr_source',
    'is_score,oos_score,max_dd_pct,eval_threshold,atr_source'
)
text = text.replace(
    'fail_score={funnel[\'fail_score\']} bt_min={_bt_min_used}',
    'fail_score={funnel[\'fail_score\']} evalThreshold={_bt_min_used}'
)

with open("backtest_runner.py", "w", encoding="utf-8", newline="\n") as f:
    f.write(text)

print("Applying LOW-01 (docs for config)...")
for md_file in ["CLAUDE.md", "SCORING_DIAGNOSTICS_REPORT.md", "SCORING_DIAGNOSTICS_MEASURED_REPORT.md"]:
    if os.path.exists(md_file):
        with open(md_file, "r", encoding="utf-8") as f:
            t = f.read()
        t = t.replace('bt_min', 'eval_threshold')
        t = t.replace('MIN_FOREX_CONFLUENCE', 'MIN_CONFLUENCE_CLASS.forex')
        with open(md_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(t)

print("Finished!")
