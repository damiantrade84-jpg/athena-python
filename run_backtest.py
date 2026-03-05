"""Run full backtest via API and print results."""
import requests, json

r = requests.post("http://localhost:5000/api/backtest", json={}, timeout=600)
d = r.json()

print(f"Success: {d.get('success')}  Total pairs: {d.get('totalPairs')}  Errors: {len(d.get('errors',[]))}")
if d.get("errors"):
    for e in d["errors"]:
        print(f"  ERR: {e['pair']}: {e['error']}")

print(f"\n{'PAIR':15s} {'SQN':>7s} {'WR%':>6s} {'PF':>6s} {'#TR':>4s} {'EXPECT':>8s} {'MaxDD':>6s} {'IS_SQN':>7s} {'OOS_SQN':>8s} {'OVERFIT':>8s}")
print("-" * 90)
for x in d.get("results", []):
    wf = x.get("wfSplit", {})
    print(f"{x['pair']:15s} {x['sqn']:+7.2f} {x['winRate']:5.1f}% {str(x['profitFactor']):>6s} {x['totalTrades']:4d} {x['expectancy']:+7.3f}R {x['maxDrawdownPct']:5.1f}% {wf.get('is_sqn',0):+7.2f} {wf.get('oos_sqn',0):+8.2f} {'YES' if wf.get('overfit_flag') else '':>8s}")

# Print regime stats for top 5
print("\n=== REGIME STATS (top 5 by SQN) ===")
for x in d.get("results", [])[:5]:
    rs = x.get("regimeStats", {})
    if rs:
        parts = [f"{k}:{v['wr']:.0f}%/{v['trades']}tr" for k, v in rs.items()]
        print(f"  {x['pair']:15s} {' | '.join(parts)}")

# Print funnel for top 5
print("\n=== TRADE FUNNEL (top 5 by SQN) ===")
for x in d.get("results", [])[:5]:
    f = x.get("funnel", {})
    if f:
        print(f"  {x['pair']:15s} setups:{f.get('total_setups',0)} -> fail_score:{f.get('fail_score',0)} fail_macro:{f.get('fail_macro',0)} -> taken:{f.get('taken',0)}")
