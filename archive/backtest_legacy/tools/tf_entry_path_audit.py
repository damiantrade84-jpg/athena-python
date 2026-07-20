#!/usr/bin/env python3
"""TF / Entry Path Audit CLI — research diagnostic for Engine A and Engine B.

Usage:
    python tools/tf_entry_path_audit.py --quick
    python tools/tf_entry_path_audit.py --engines A B --profiles bar_native calendar_equivalent
    python tools/tf_entry_path_audit.py --symbols EUR/USD BTC/USDT --vectorbt

Safety:
    - Research/diagnostic only (ATHENA_TF_ENTRY_PATH_AUDIT=1)
    - Does not modify live execution or production thresholds
    - Does not overwrite unrelated baseline reports (writes tf_entry_path_audit_* only)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")
os.environ.setdefault("ATHENA_TF_ENTRY_PATH_AUDIT", "1")
os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

from athena_research.tf_entry_path_audit.parameter_intent import IndicatorProfile
from athena_research.tf_entry_path_audit.runner import run_tf_entry_path_audit


def _parse_profiles(raw: list[str]) -> tuple[IndicatorProfile, ...]:
    mapping = {
        "bar_native": IndicatorProfile.BAR_NATIVE,
        "calendar_equivalent": IndicatorProfile.CALENDAR_EQUIVALENT,
        "volatility_native": IndicatorProfile.VOLATILITY_NATIVE,
    }
    if not raw:
        return (IndicatorProfile.BAR_NATIVE,)
    out = []
    for name in raw:
        key = name.strip().lower()
        if key not in mapping:
            raise SystemExit(f"Unknown profile: {name}. Choose from: {', '.join(mapping)}")
        out.append(mapping[key])
    return tuple(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Engine A/B TF entry path audit")
    parser.add_argument("--engines", nargs="+", default=["A", "B"], choices=["A", "B"])
    parser.add_argument("--symbols", nargs="*", help="Pair display names (default: audit set)")
    parser.add_argument(
        "--profiles",
        nargs="*",
        default=["bar_native"],
        help="Indicator profiles: bar_native calendar_equivalent volatility_native",
    )
    parser.add_argument("--output-dir", default="reports", help="Output directory")
    parser.add_argument("--vectorbt", action="store_true", help="Run provisional vectorbt sweeps")
    parser.add_argument("--quick", action="store_true", help="First 3 pairs only")
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="Only aligned signal=entry cells (fastest; skips separated-TF diagnostic walks)",
    )
    parser.add_argument("--list-matrix", action="store_true", help="Print matrix and exit")
    args = parser.parse_args()

    if args.list_matrix:
        from athena_research.tf_entry_path_audit.matrix import ENGINE_A_MATRIX, ENGINE_B_MATRIX

        print("Engine A matrix:")
        for c in ENGINE_A_MATRIX:
            print(f"  {c.label}: signal={c.signal_tf} entry={c.entry_tf} exit={c.exit_tf}")
        print("Engine B matrix:")
        for c in ENGINE_B_MATRIX:
            print(f"  {c.label}: signal={c.signal_tf} entry={c.entry_tf} exit={c.exit_tf}")
        return

    pairs = None
    if args.symbols:
        from tools._athena_pair_loader import find_pair_by_display

        pairs = []
        for sym in args.symbols:
            p = find_pair_by_display(sym)
            if not p:
                raise SystemExit(f"Unknown pair: {sym}")
            pairs.append(p)

    result = run_tf_entry_path_audit(
        pairs=pairs,
        engines=tuple(args.engines),
        profiles=_parse_profiles(args.profiles),
        output_dir=Path(args.output_dir),
        use_vectorbt=args.vectorbt,
        quick=args.quick,
        baseline_only=args.baseline_only,
    )
    print(f"Audit complete: {result['trade_count']} trades, {result['validation_rows']} validation rows")
    print(f"Output: {result['output_dir']}")
    if result.get("blocked"):
        print(f"Blocked cells: {len(result['blocked'])}")
    if result.get("vectorbt_used"):
        print("VectorBT provisional CSV written (NOT final validation)")


if __name__ == "__main__":
    main()
