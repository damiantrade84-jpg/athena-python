"""ASE data-layer bug fixes — run once after applying code patches.

Fixes:
  1. PTIS catalog: mark FRED series as verified (unblocks enriched models)
  2. Diagnose missing crypto events in phase1_events.parquet
  3. Verify OI series ID alignment (code fix in build.py)
  4. Verify COT asset mapping coverage

Usage:
  py ase_fix_data_bugs.py              # diagnose + fix
  py ase_fix_data_bugs.py --dry-run    # diagnose only, no writes
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


def ptis_root() -> Path:
    override = os.environ.get("ATHENA_PTIS_ROOT", "").strip()
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        local = str(Path.home() / "AppData" / "Local")
    return Path(local) / "Athena" / "ptis"


def catalog_path() -> Path:
    return ptis_root() / "ptis_catalog.db"


def fix_fred_verified(dry_run: bool) -> int:
    db = catalog_path()
    if not db.exists():
        print(f"  [SKIP] catalog not found: {db}")
        return 0
    with sqlite3.connect(str(db), timeout=15.0) as con:
        rows = con.execute(
            "SELECT series_id, rule_id, verified FROM series "
            "WHERE rule_id IN ('fred_daily', 'fred_policy') AND verified = 0"
        ).fetchall()
        if not rows:
            print("  [OK] all FRED series already verified")
            return 0
        print(f"  Found {len(rows)} unverified FRED series:")
        for sid, rule, v in rows:
            print(f"    {sid}  rule={rule}  verified={v}")
        if dry_run:
            print("  [DRY RUN] would set verified=1")
            return len(rows)
        con.execute(
            "UPDATE series SET verified = 1, updated_at = datetime('now') "
            "WHERE rule_id IN ('fred_daily', 'fred_policy') AND verified = 0"
        )
        con.commit()
        count = con.execute(
            "SELECT COUNT(*) FROM series "
            "WHERE rule_id IN ('fred_daily', 'fred_policy') AND verified = 0"
        ).fetchone()[0]
        assert count == 0, f"still {count} unverified"
        print(f"  [FIXED] {len(rows)} FRED series marked verified=1")
        return len(rows)


def diagnose_crypto_events() -> None:
    try:
        import pandas as pd
    except ImportError:
        print("  [SKIP] pandas not installed")
        return
    events_path = Path(__file__).parent / "athena_research" / "ase" / "output" / "phase1_events.parquet"
    if not events_path.exists():
        print(f"  [MISSING] {events_path}")
        print("  Run:  py -m athena_research.ase.run_phase1")
        return
    df = pd.read_parquet(events_path)
    print(f"  Total events: {len(df)}")
    if "family" not in df.columns:
        print("  [BUG] 'family' column missing")
        return
    families = df["family"].value_counts()
    print(f"  Family distribution:")
    for fam, count in families.items():
        print(f"    {fam}: {count}")
    crypto = df[df["family"] == "crypto"]
    if crypto.empty:
        print("\n  [BUG] Zero crypto events in parquet!")
        print("  Fix: py ase_cli.py ingest --sources binance,bybit")
        print("       py -m athena_research.ase.run_phase1")
    else:
        horizons = crypto["horizon"].value_counts()
        print(f"\n  Crypto events found ({len(crypto)} total):")
        for h, c in horizons.items():
            print(f"    {h}: {c}")
        print("  [OK] crypto events present")


def verify_oi_alignment() -> None:
    db = catalog_path()
    if not db.exists():
        print("  [SKIP] catalog not found")
        return
    with sqlite3.connect(str(db), timeout=15.0) as con:
        oi_rows = con.execute(
            "SELECT series_id FROM series WHERE series_id LIKE 'BYBIT:%:oi'"
        ).fetchall()
        oi_wrong = con.execute(
            "SELECT series_id FROM series WHERE series_id LIKE 'BYBIT:%:open_interest'"
        ).fetchall()
    if oi_rows:
        print(f"  [OK] {len(oi_rows)} BYBIT:*:oi series found (matches fixed code)")
    if oi_wrong:
        print(f"  [WARN] {len(oi_wrong)} BYBIT:*:open_interest series (old format)")
    if not oi_rows and not oi_wrong:
        print("  [WARN] no Bybit OI series in catalog — run: py ase_cli.py ingest --sources bybit")


def verify_cot_coverage() -> None:
    try:
        from athena_ase.features.build import COT_ASSET_MAP, cot_asset_for_symbol
        from athena_ase.instruments import instruments_for_family
    except ImportError:
        print("  [SKIP] can't import ASE modules (run from repo root)")
        return
    for family in ("forex", "commodity"):
        instruments = instruments_for_family(family)
        mapped = [(i.symbol, cot_asset_for_symbol(i.symbol)) for i in instruments]
        covered = [(s, a) for s, a in mapped if a]
        missing = [(s, a) for s, a in mapped if not a]
        print(f"  {family}: {len(covered)}/{len(mapped)} instruments have COT mapping")
        if missing:
            print(f"    Unmapped (will use core model, not enriched):")
            for s, _ in missing:
                print(f"      {s}")
    db = catalog_path()
    if db.exists():
        with sqlite3.connect(str(db), timeout=15.0) as con:
            cot_series = con.execute(
                "SELECT series_id FROM series WHERE series_id LIKE 'CFTC:COT:%'"
            ).fetchall()
        cot_assets = {r[0].split(":")[2] for r in cot_series}
        map_assets = set(COT_ASSET_MAP.values())
        missing_data = map_assets - cot_assets
        if missing_data:
            print(f"  [WARN] mapped assets without PTIS data: {missing_data}")
        else:
            print(f"  [OK] all mapped COT assets have PTIS data")


def main() -> int:
    parser = argparse.ArgumentParser(description="ASE data-layer bug fixes")
    parser.add_argument("--dry-run", action="store_true", help="diagnose only")
    args = parser.parse_args()
    print("=" * 60)
    print("ASE data-layer bug diagnostic + fix")
    print("=" * 60)
    print("\n1. FRED series verification (enriched model blocker)")
    fix_fred_verified(args.dry_run)
    print("\n2. Crypto events in phase1_events.parquet")
    diagnose_crypto_events()
    print("\n3. Bybit OI series ID alignment")
    verify_oi_alignment()
    print("\n4. COT asset mapping coverage")
    verify_cot_coverage()
    print("\n" + "=" * 60)
    print("Next steps:")
    print("  1. py ase_fix_data_bugs.py          (run fixes)")
    print("  2. py ase_cli.py ingest --sources eodhd,dukascopy,cot,fred,bybit")
    print("  3. py -m athena_research.ase.run_phase1")
    print("  4. py ase_cli.py train-all")
    print("  5. py ase_cli.py holdout-eval --family forex --horizon swing")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
