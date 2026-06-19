"""Read-only XAU reliable-era abnormal gap review (legacy wrapper)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from athena_research.commodity_data_audit.mt5_reader import (
    Mt5ReadError,
    connect_mt5_readonly,
    copy_rates_range_utc,
    shutdown_mt5,
)
from athena_research.commodity_data_audit.reliable_era_gap_review import run_xau_reliable_era_gap_review


def main() -> int:
    parser = argparse.ArgumentParser(description="Review XAU reliable-era abnormal gaps (read-only).")
    parser.add_argument("--reliable-start", default="2016-07-29")
    parser.add_argument("--skip-mt5", action="store_true")
    parser.add_argument("--mt5-path", default="", help="Optional MT5 terminal path.")
    args = parser.parse_args()

    copy_rates = None
    mt5 = None
    if not args.skip_mt5:
        try:
            mt5 = connect_mt5_readonly(terminal_path=args.mt5_path or None)

            def _copy_rates(sym: str, tf: str, start, end):
                return copy_rates_range_utc(mt5, sym, tf, start, end)

            copy_rates = _copy_rates
        except Mt5ReadError as exc:
            print("status=MT5_UNAVAILABLE", file=sys.stderr)
            print(str(exc), file=sys.stderr)
            return 2

    try:
        result = run_xau_reliable_era_gap_review(
            reliable_start=args.reliable_start,
            copy_rates=copy_rates,
        )
    finally:
        if mt5 is not None:
            shutdown_mt5(mt5)

    print(f"gap_count={result['gap_count']}")
    print(f"classification_counts={json.dumps(result['classification_counts'], sort_keys=True)}")
    print(f"all_legitimate_closures={result['all_legitimate_closures']}")
    print(f"review={result['review_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
