from pathlib import Path

checks = {
    "market_structure.py": [
        "recommended_stop_loss",
        "recommended_take_profit",
        "rr_ok",
        "risk_reward",
        "min_rr",
        "rr =",
    ],
    "athena.py": [
        "scalp_sl",
        "scalp_tp",
        "intraday_sl",
        "intraday_tp",
        "recommended_stop_loss",
        "recommended_take_profit",
    ],
}

for file, terms in checks.items():
    p = Path(file)
    if not p.exists():
        print(f"\nMISSING: {file}")
        continue

    print(f"\n===== {file} =====")
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()

    for i, line in enumerate(lines, 1):
        if any(t in line for t in terms):
            print(f"{i:>5}: {line.strip()}")
