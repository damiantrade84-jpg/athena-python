"""Run full backtest via API and print results."""
import sys
import requests

is_naked = "--naked" in sys.argv
pair_args = [a for a in sys.argv[1:] if not a.startswith("--")]
target_pair = pair_args[0] if pair_args else None

if is_naked:
    if not target_pair:
        print("Error: Must specify a pair for --naked backtest (e.g., python run_backtest.py EUR/USD --naked)")
        sys.exit(1)
        
    print(f"=== Engine B (Naked) Backtest for {target_pair} ===")
    r = requests.post("http://localhost:5000/api/backtest-naked", 
                      json={"pair": target_pair}, timeout=300)
    
    try:
        d = r.json()
    except Exception as e:
        print(f"Failed to parse JSON response: {e}")
        print(r.text)
        sys.exit(1)
        
    if d.get("success", True) is False:
        print(f"Error: {d.get('error')}")
        sys.exit(1)

    print(f"\n{'PAIR':15s} {'SQN':>7s} {'WR%':>6s} {'PF':>6s} {'#TR':>4s} {'EXPECT':>8s} {'MaxDD':>6s}")
    print("-" * 75)
    print(f"{d.get('pair', target_pair):15s} {d.get('sqn', 0):+7.2f} {d.get('winRate', 0):5.1f}% {str(d.get('profitFactor', 0)):>6s} {d.get('totalTrades', 0):4d} {d.get('expectancy', 0):+7.3f}R {d.get('maxDrawdownPct', 0):5.1f}%")
    
    if d.get("engine") == "FOREX":
        print(f"\n--- FVG & Liquidity Edge ---")
        print(f"FVG Avg Bonus: {d.get('fvg_avg_bonus', 0):.3f}")
        print(f"Liquidity Sweep %: {d.get('liquidity_sweep_pct', 0):.1f}%")
        print(f"Avg Volume Strength: {d.get('avg_volume_strength', 0):.3f}")
        print(f"FVG Overlap %: {d.get('fvg_overlap_pct', 0):.1f}%")

else:
    r = requests.post("http://localhost:5000/api/backtest", json={"style": "auto", "symbol": target_pair} if target_pair else {"style": "auto"}, timeout=600)
    d = r.json()

    print(f"Success: {d.get('success')}  Total pairs: {d.get('totalPairs', 1 if target_pair else 0)}  Errors: {len(d.get('errors',[]))}")
    if d.get("errors"):
        for e in d["errors"]:
            print(f"  ERR: {e['pair']}: {e['error']}")

    print(f"\n{'PAIR':15s} {'SQN':>7s} {'WR%':>6s} {'PF':>6s} {'#TR':>4s} {'EXPECT':>8s} {'MaxDD':>6s} {'IS_SQN':>7s} {'OOS_SQN':>8s} {'OVERFIT':>8s}")
    print("-" * 90)
    
    results = [d] if "sqn" in d and "pair" in d else d.get("results", [])
    
    for x in results:
        wf = x.get("wfSplit", {})
        print(f"{x['pair']:15s} {x['sqn']:+7.2f} {x['winRate']:5.1f}% {str(x['profitFactor']):>6s} {x['totalTrades']:4d} {x['expectancy']:+7.3f}R {x['maxDrawdownPct']:5.1f}% {wf.get('is_sqn',0):+7.2f} {wf.get('oos_sqn',0):+8.2f} {'YES' if wf.get('overfit_flag') else '':>8s}")

    if not target_pair:
        # Print regime stats for top 5
        print("\n=== REGIME STATS (top 5 by SQN) ===")
        for x in results[:5]:
            rs = x.get("regimeStats", {})
            if rs:
                parts = [f"{k}:{v['wr']:.0f}%/{v['trades']}tr" for k, v in rs.items()]
                print(f"  {x['pair']:15s} {' | '.join(parts)}")

        # Print funnel for top 5
        print("\n=== TRADE FUNNEL (top 5 by SQN) ===")
        for x in results[:5]:
            f = x.get("funnel", {})
            if f:
                print(f"  {x['pair']:15s} setups:{f.get('total_setups',0)} -> fail_score:{f.get('fail_score',0)} fail_macro:{f.get('fail_macro',0)} -> taken:{f.get('taken',0)}")
