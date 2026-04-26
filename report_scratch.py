import json

def report(filename):
    with open(filename) as f:
        data = json.load(f)
    
    valid_data = {k: v for k, v in data.items() if v.get('closed_trade_count', 0) > 0}
    if not valid_data: return
    
    def get_pf(v):
        pf = v.get('profit_factor', 0)
        if isinstance(pf, str):
            return 999.0 if pf.lower() in ('inf', 'infinity') else 0.0
        return float(pf) if pf is not None else 0.0

    sorted_pf = sorted(valid_data.items(), key=lambda x: get_pf(x[1]), reverse=True)
    
    print(f"\n--- {filename} ---")
    for k, v in sorted_pf[:5]:
        print(f"TOP: {k}: PF={get_pf(v):.2f}, WR={v.get('win_rate', 0)*100:.1f}%, AvgR={v.get('average_R', 0):.2f}, Trades={v.get('closed_trade_count', 0)}, MaxDD={v.get('max_drawdown_R', 0):.2f}")
    
    print("...")
    for k, v in sorted_pf[-5:]:
        print(f"BOT: {k}: PF={get_pf(v):.2f}, WR={v.get('win_rate', 0)*100:.1f}%, AvgR={v.get('average_R', 0):.2f}, Trades={v.get('closed_trade_count', 0)}, MaxDD={v.get('max_drawdown_R', 0):.2f}")

report('logs/strategy_lab/strategy_lab_by_symbol.json')
