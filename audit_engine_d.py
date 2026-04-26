import json
import glob
import os
import pandas as pd
import numpy as np

def find_engine_d_trades():
    target_files = []
    # common locations for matrix json output
    target_files.extend(glob.glob("logs/strategy_lab/*.json"))
    target_files.extend(glob.glob("logs/backtest_matrix/*.json"))
    target_files.extend(glob.glob("logs/backtest_matrix_test/*.json"))
    target_files.extend(glob.glob("logs/scalp_audit/*.json"))
    target_files.extend(glob.glob("logs/threshold_audit/*.json"))
    target_files.extend(glob.glob("logs/*.json"))
    
    source_file = None
    best_trades = []
    
    for f in target_files:
        try:
            with open(f, 'r', encoding='utf-8') as fp:
                data = json.load(fp)
                trades = []
                if isinstance(data, list):
                    trades = data
                elif isinstance(data, dict) and "trades" in data:
                    trades = data["trades"]
                elif isinstance(data, dict) and "results" in data:
                    trades = data["results"]
                
                if trades and isinstance(trades, list) and len(trades) > 0 and isinstance(trades[0], dict):
                    ed_trades = [t for t in trades if t.get('engine') == 'ENGINE_D' or t.get('strategy_family') == 'ENGINE_D' or t.get('signal_source') == 'ENGINE_D']
                    if len(ed_trades) > len(best_trades):
                        best_trades = ed_trades
                        source_file = f
        except Exception:
            pass
            
    return best_trades, source_file

trades, source_file = find_engine_d_trades()
print(f"Source of full trades: {source_file}")
print(f"Total Engine D trades found: {len(trades)}")

if len(trades) == 0:
    print("Could not find the trades!")
    import sys
    sys.exit(0)

df = pd.DataFrame(trades)

# Standardize column names
lower_map = {c.lower(): c for c in df.columns}
print("Columns found:", df.columns.tolist())
r_col = lower_map.get("r_multiple") or lower_map.get("resultr") or lower_map.get("result_r") or lower_map.get("r") or lower_map.get("pnl_r") or lower_map.get("realized_r")
exit_reason_col = lower_map.get("exit_reason") or lower_map.get("failure_reason") or lower_map.get("reason")
symbol_col = lower_map.get("pair") or lower_map.get("symbol") or lower_map.get("asset")
session_col = lower_map.get("session_name") or lower_map.get("session") or lower_map.get("session_id")

if r_col:
    df[r_col] = pd.to_numeric(df[r_col], errors="coerce")

print("\n--- OVERALL METRICS ---")
if r_col:
    print(df[r_col].describe(percentiles=[0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]))
    wins = df[df[r_col] > 0]
    losses = df[df[r_col] < 0]
    print(f"Wins: {len(wins)}, Losses: {len(losses)}, Win Rate: {round(len(wins)/len(df)*100,2)}%")
    print(f"Avg Win R: {round(wins[r_col].mean(), 4) if len(wins) else None}")
    print(f"Avg Loss R: {round(losses[r_col].mean(), 4) if len(losses) else None}")
    gross_win = wins[r_col].sum()
    gross_loss = abs(losses[r_col].sum())
    pf = gross_win / gross_loss if gross_loss else float('inf')
    print(f"Profit Factor: {round(pf, 4)}")

if exit_reason_col:
    print("\n--- EXIT REASON BREAKDOWN ---")
    out = df.groupby(exit_reason_col, dropna=False)[r_col].agg(["count", "mean", "min", "max"]).sort_values("count", ascending=False)
    print(out.to_string())

    print("\n--- TP_HIT PATH BREAKDOWN ---")
    tp_df = df[df[exit_reason_col].astype(str).str.upper().str.contains("TP", na=False)]
    if not tp_df.empty:
        print(tp_df[r_col].describe())
        # Check if tp1, tp_partial, tp2 exists to see if it's full TP or partial
        cols_to_print = [c for c in tp_df.columns if c.lower() in ["entry", "entry_price", "tp", "tp1", "tp_partial", "tp2", r_col, exit_reason_col, "exit_price"]]
        print("\nSample TP_HIT rows:")
        print(tp_df[cols_to_print].head(10).to_string())
    
    print("\n--- MANUAL_CLOSE BREAKDOWN ---")
    mc_df = df[df[exit_reason_col].astype(str).str.upper().str.contains("MANUAL", na=False)]
    if not mc_df.empty:
        print(mc_df[r_col].describe())

if symbol_col:
    print("\n--- SYMBOL BREAKDOWN ---")
    out = df.groupby(symbol_col, dropna=False)[r_col].agg(["count", "mean", "sum"]).sort_values("count", ascending=False)
    print(out.to_string())
else:
    print("\n--- SYMBOL BREAKDOWN ---")
    # try pair or symbol directly if case issue
    sym_col = 'pair' if 'pair' in df.columns else 'symbol' if 'symbol' in df.columns else None
    if sym_col:
        out = df.groupby(sym_col, dropna=False)[r_col].agg(["count", "mean", "sum"]).sort_values("count", ascending=False)
        print(out.to_string())
    else:
        print("No symbol column found.")

if session_col:
    print("\n--- SESSION BREAKDOWN ---")
    out = df.groupby(session_col, dropna=False)[r_col].agg(["count", "mean", "sum"]).sort_values("count", ascending=False)
    print(out.to_string())
else:
    print("\n--- SESSION BREAKDOWN ---")
    print("No session column found.")

print("\n--- DEEP DIVE: NEGATIVE R TP1 HITS ---")
neg_tp = tp_df[tp_df[r_col] < 0]
if not neg_tp.empty:
    print(f"Found {len(neg_tp)} TP1 hits with negative R. Samples:")
    cols_to_print = [c for c in neg_tp.columns if c.lower() in ["pair", "symbol", "direction", "entry", "sl", "tp1", r_col, "exit_price"]]
    print(neg_tp[cols_to_print].head(10).to_string())

print("\n--- PARTIAL ACCOUNTING VERDICT ---")
print("Evaluating if 'TP_HIT' average R is correctly representing partials...")
# We will just print the TP_HIT mean and max. If mean is around 0.5 or max is < 1.0 but it should be 1.0, maybe partial logic is halving it.

print("\n--- GEOMETRY BUG COUNT (EXCLUDING NaN ONLY) ---")
entry = lower_map.get("entry_price") or lower_map.get("entry")
sl = lower_map.get("sl")
tp1 = lower_map.get("tp_partial") or lower_map.get("tp1") or lower_map.get("tp")
tp2 = lower_map.get("tp2")
direction = lower_map.get("direction")

if all([entry, sl, tp1, direction]):
    # Exclude NaN
    geom_df = df.dropna(subset=[entry, sl, tp1])
    if tp2:
        geom_df = geom_df.dropna(subset=[tp2])
        
    for c in [entry, sl, tp1, tp2] if tp2 else [entry, sl, tp1]:
        geom_df[c] = pd.to_numeric(geom_df[c], errors="coerce")
        
    if tp2:
        long_bad = geom_df[
            geom_df[direction].astype(str).str.upper().eq("LONG")
            & ~((geom_df[sl] < geom_df[entry]) & (geom_df[entry] < geom_df[tp1]) & (geom_df[tp1] <= geom_df[tp2]))
        ]
        short_bad = geom_df[
            geom_df[direction].astype(str).str.upper().eq("SHORT")
            & ~((geom_df[tp2] <= geom_df[tp1]) & (geom_df[tp1] < geom_df[entry]) & (geom_df[entry] < geom_df[sl]))
        ]
    else:
        long_bad = geom_df[
            geom_df[direction].astype(str).str.upper().eq("LONG")
            & ~((geom_df[sl] < geom_df[entry]) & (geom_df[entry] < geom_df[tp1]))
        ]
        short_bad = geom_df[
            geom_df[direction].astype(str).str.upper().eq("SHORT")
            & ~((geom_df[tp1] < geom_df[entry]) & (geom_df[entry] < geom_df[sl]))
        ]

    print(f"Total valid non-NaN geometry rows: {len(geom_df)}")
    print(f"Bad LONG geometry (valid only): {len(long_bad)}")
    print(f"Bad SHORT geometry (valid only): {len(short_bad)}")
else:
    print("Missing columns for geometry check.")
