import json
import os
import pandas as pd
import numpy as np

# Override DB to avoid writing to live audit DB
import config
os.makedirs("logs/backtest_matrix_engine_b_rr_validation", exist_ok=True)
config.AUDIT_DB = "logs/backtest_matrix_engine_b_rr_validation/audit.db"

import athena
import backtest_runner

# Disable actual server start if any
athena.app = None

pair = next((p for p in athena.ALL_PAIRS if p.get("symbol") == "BTCUSDT" or p.get("display") == "BTC/USDT"), None)
if not pair:
    print("BTC/USDT not found")
    exit(1)

print("Running backtest for", pair["display"])

result = backtest_runner.backtest_pair_naked(pair, style="naked", validation_mode="standard")

if not result:
    print("No result returned")
    exit(1)

trades = result.get("trades", [])

long_trades = [t for t in trades if t["direction"] == "LONG"]
short_trades = [t for t in trades if t["direction"] == "SHORT"]

def calc_stats(trds):
    if not trds:
        return 0, 0, 0, 0
    wins = len([t for t in trds if t["resultR"] > 0])
    wr = (wins / len(trds)) * 100
    gross_profit = sum([t["resultR"] for t in trds if t["resultR"] > 0])
    gross_loss = abs(sum([t["resultR"] for t in trds if t["resultR"] < 0]))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    avg_r = sum([t["resultR"] for t in trds]) / len(trds)
    return len(trds), wr, pf, avg_r

tc, wr, pf, avgr = calc_stats(trades)
ltc, lwr, lpf, lavgr = calc_stats(long_trades)
stc, swr, spf, savgr = calc_stats(short_trades)

print("\n1. Overall Engine B BTC/USDT result:")
print(f"Trade count: {tc}")
print(f"Win rate: {wr:.1f}%")
print(f"Profit factor: {pf:.2f}")
print(f"Avg net R: {avgr:.3f}")

print("\n2. Direction split:")
print(f"LONG count: {ltc}, WR: {lwr:.1f}%, PF: {lpf:.2f}, avgR: {lavgr:.3f}")
print(f"SHORT count: {stc}, WR: {swr:.1f}%, PF: {spf:.2f}, avgR: {savgr:.3f}")

print("\n3. RR telemetry:")
if trades:
    rr_targets = [t.get("rr_target", 0) for t in trades]
    actual_rrs = [t.get("actual_rr", 0) for t in trades]
    
    rr_target_series = pd.Series(rr_targets)
    actual_rr_series = pd.Series(actual_rrs)
    
    print(f"rr_target min/p25/median/p75/max: {rr_target_series.min():.2f} / {rr_target_series.quantile(0.25):.2f} / {rr_target_series.median():.2f} / {rr_target_series.quantile(0.75):.2f} / {rr_target_series.max():.2f}")
    print(f"actual_rr min/p25/median/p75/max: {actual_rr_series.min():.2f} / {actual_rr_series.quantile(0.25):.2f} / {actual_rr_series.median():.2f} / {actual_rr_series.quantile(0.75):.2f} / {actual_rr_series.max():.2f}")
    
    exactly_2 = sum(1 for r in rr_targets if abs(r - 2.0) < 0.01)
    print(f"count of rr_target exactly 2.0: {exactly_2}")
    print(f"unique rr_target count: {len(set([round(r, 2) for r in rr_targets]))}")
    
    tp_sources = pd.Series([t.get("selected_tp_source", "missing") for t in trades]).value_counts().to_dict()
    sl_sources = pd.Series([t.get("selected_sl_source", "missing") for t in trades]).value_counts().to_dict()
    print(f"selected_tp_source breakdown: {tp_sources}")
    print(f"selected_sl_source breakdown: {sl_sources}")

print("\n4. Feature attribution:")
def filter_stats(cond):
    trds = [t for t in trades if cond(t)]
    tc, wr, pf, avgr = calc_stats(trds)
    return f"{tc} trades, WR: {wr:.1f}%, PF: {pf:.2f}, avgR: {avgr:.3f}"

print(f"OB_at_zone true: {filter_stats(lambda t: t.get('ob_at_zone', False))}")
print(f"OB_at_zone false: {filter_stats(lambda t: not t.get('ob_at_zone', False))}")

print(f"BOS_MTF confirmed true: {filter_stats(lambda t: t.get('bos_mtf_confirmed', False))}")
print(f"BOS_MTF confirmed false: {filter_stats(lambda t: not t.get('bos_mtf_confirmed', False))}")

print(f"OB_at_zone + BOS_MTF confirmed: {filter_stats(lambda t: t.get('ob_at_zone', False) and t.get('bos_mtf_confirmed', False))}")
print(f"OB_at_zone + BOS_MTF confirmed + SHORT: {filter_stats(lambda t: t.get('ob_at_zone', False) and t.get('bos_mtf_confirmed', False) and t['direction'] == 'SHORT')}")

print(f"ENGULFING: {filter_stats(lambda t: t.get('trigger_pattern') == 'ENGULFING')}")
print(f"STRONG_CLOSE: {filter_stats(lambda t: t.get('trigger_pattern') == 'STRONG_CLOSE')}")

print(f"liquidity_sweep true: {filter_stats(lambda t: t.get('liquidity_sweep', False))}")
print(f"liquidity_sweep false: {filter_stats(lambda t: not t.get('liquidity_sweep', False))}")

print(f"zone_touched true: {filter_stats(lambda t: t.get('zone_touched', False))}")
print(f"zone_touched false: {filter_stats(lambda t: not t.get('zone_touched', False))}")

print(f"fvg_bonus non-zero: {filter_stats(lambda t: t.get('fvg_bonus', 0) > 0)}")
print(f"fvg_bonus zero: {filter_stats(lambda t: t.get('fvg_bonus', 0) == 0)}")

vol_str = [t.get('volume_strength', 0) for t in trades]
if vol_str:
    high_vol = filter_stats(lambda t: t.get('volume_strength', 0) > 1.0)
    low_vol = filter_stats(lambda t: 0 < t.get('volume_strength', 0) <= 1.0)
    zero_vol = filter_stats(lambda t: t.get('volume_strength', 0) == 0)
    print(f"volume_strength > 1.0: {high_vol}")
    print(f"volume_strength 0-1.0: {low_vol}")
    print(f"volume_strength 0: {zero_vol}")

with open("logs/backtest_matrix_engine_b_rr_validation/validation_report.json", "w") as f:
    json.dump(result, f, indent=2)

print("\nDone. Saved to logs/backtest_matrix_engine_b_rr_validation/validation_report.json")
