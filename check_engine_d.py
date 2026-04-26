import sqlite3
import pandas as pd
import math
import shutil
import os

# First, create the snapshot safely
try:
    if os.path.exists('audit.db'):
        shutil.copy2('audit.db', 'audit_engine_d_snapshot.db')
        print("Successfully created snapshot: audit_engine_d_snapshot.db")
    else:
        print("audit.db does not exist!")
except Exception as e:
    print(f"Error copying database: {e}")

DB = "audit_engine_d_snapshot.db"

con = sqlite3.connect(DB)

tables = pd.read_sql_query(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
    con
)

print("TABLES")
print(tables.to_string(index=False))

# Try likely trade tables
candidate_tables = []
for table in tables["name"]:
    cols = pd.read_sql_query(f"PRAGMA table_info({table})", con)
    colnames = set(cols["name"].str.lower())
    if any(c in colnames for c in ["engine", "strategy_family", "signal_source"]) and any(
        c in colnames for c in ["r_multiple", "r", "pnl_r", "realized_r"]
    ):
        candidate_tables.append(table)

print("\nCANDIDATE TRADE TABLES:", candidate_tables)

for table in candidate_tables:
    print(f"\n\n================ {table} ================")
    cols = pd.read_sql_query(f"PRAGMA table_info({table})", con)
    print(cols[["name", "type"]].to_string(index=False))

    colnames = list(cols["name"])
    lower_map = {c.lower(): c for c in colnames}

    engine_col = lower_map.get("engine") or lower_map.get("strategy_family") or lower_map.get("signal_source")
    r_col = lower_map.get("r_multiple") or lower_map.get("r") or lower_map.get("pnl_r") or lower_map.get("realized_r")

    if not engine_col or not r_col:
        continue

    conditions = []
    if lower_map.get("engine"):
        conditions.append("UPPER(COALESCE(engine, '')) IN ('ENGINE_D', 'SCALP', 'SCALPING')")
    if lower_map.get("strategy_family"):
        conditions.append("UPPER(COALESCE(strategy_family, '')) IN ('ENGINE_D', 'SCALP', 'SCALPING')")
    if lower_map.get("signal_source"):
        conditions.append("UPPER(COALESCE(signal_source, '')) IN ('ENGINE_D', 'SCALP', 'SCALPING')")

    if not conditions:
        continue

    cond_str = " OR ".join(conditions)

    q = f"""
    SELECT *
    FROM {table}
    WHERE {cond_str}
    """
    try:
        df = pd.read_sql_query(q, con)
    except Exception as e:
        print("Query failed:", e)
        continue

    if df.empty:
        print("No Engine D / scalp rows found in this table.")
        continue

    print("\nENGINE D ROWS:", len(df))

    # Basic R stats
    df[r_col] = pd.to_numeric(df[r_col], errors="coerce")
    print("\nR SUMMARY")
    print(df[r_col].describe(percentiles=[0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]))

    wins = df[df[r_col] > 0]
    losses = df[df[r_col] < 0]
    print("\nWIN/LOSS")
    print("wins:", len(wins))
    print("losses:", len(losses))
    print("win_rate:", round(len(wins) / len(df) * 100, 2))
    print("avg_win_R:", round(wins[r_col].mean(), 4) if len(wins) else None)
    print("avg_loss_R:", round(losses[r_col].mean(), 4) if len(losses) else None)
    print("largest_loss_R:", round(df[r_col].min(), 4))
    print("largest_win_R:", round(df[r_col].max(), 4))

    gross_win = wins[r_col].sum()
    gross_loss = abs(losses[r_col].sum())
    pf = gross_win / gross_loss if gross_loss else math.inf
    print("profit_factor:", round(pf, 4) if pf != math.inf else "inf")

    # Exit reason breakdown
    exit_cols = [c for c in df.columns if "exit" in c.lower() or "reason" in c.lower()]
    for ec in exit_cols:
        print(f"\nBREAKDOWN BY {ec}")
        out = df.groupby(ec, dropna=False)[r_col].agg(["count", "mean", "min", "max"]).sort_values("count", ascending=False)
        print(out.to_string())

    # Impossible exit checks
    possible_exit_col = None
    for c in exit_cols:
        if "reason" in c.lower() or "exit" in c.lower():
            possible_exit_col = c
            break

    if possible_exit_col:
        bad_tp = df[df[possible_exit_col].astype(str).str.upper().str.contains("TP", na=False) & (df[r_col] <= 0)]
        bad_sl = df[df[possible_exit_col].astype(str).str.upper().str.contains("SL|STOP", na=False) & (df[r_col] >= 0)]

        print("\nIMPOSSIBLE EXIT CHECKS")
        print("TP exits with R <= 0:", len(bad_tp))
        print("SL exits with R >= 0:", len(bad_sl))

        if len(bad_tp):
            print("\nSample bad TP rows:")
            print(bad_tp.head(10).to_string())

        if len(bad_sl):
            print("\nSample bad SL rows:")
            print(bad_sl.head(10).to_string())

    # Directional geometry checks
    entry = lower_map.get("entry_price") or lower_map.get("entry")
    sl = lower_map.get("sl")
    tp1 = lower_map.get("tp_partial") or lower_map.get("tp1") or lower_map.get("tp")
    tp2 = lower_map.get("tp2")
    direction = lower_map.get("direction")

    if all([entry, sl, tp1, tp2, direction]):
        for c in [entry, sl, tp1, tp2]:
            df[c] = pd.to_numeric(df[c], errors="coerce")

        long_bad = df[
            df[direction].astype(str).str.upper().eq("LONG")
            & ~((df[sl] < df[entry]) & (df[entry] < df[tp1]) & (df[tp1] <= df[tp2]))
        ]

        short_bad = df[
            df[direction].astype(str).str.upper().eq("SHORT")
            & ~((df[tp2] <= df[tp1]) & (df[tp1] < df[entry]) & (df[entry] < df[sl]))
        ]

        print("\nDIRECTIONAL GEOMETRY CHECKS")
        print("Bad LONG geometry:", len(long_bad))
        print("Bad SHORT geometry:", len(short_bad))

        if len(long_bad):
            print("\nSample bad LONG rows:")
            print(long_bad[[direction, entry, sl, tp1, tp2, r_col]].head(10).to_string())

        if len(short_bad):
            print("\nSample bad SHORT rows:")
            print(short_bad[[direction, entry, sl, tp1, tp2, r_col]].head(10).to_string())
    else:
        print("\nDirectional geometry check skipped. Missing one of: entry, sl, tp1, tp2, direction")

con.close()
