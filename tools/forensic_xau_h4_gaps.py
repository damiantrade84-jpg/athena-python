"""Read-only XAU/USD H4 abnormal-gap forensic — does not modify frozen raw or manifests."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from athena_research.commodity_data_audit.gap_forensic import run_xau_h4_gap_forensic
from athena_research.commodity_data_audit.freeze_store import resolve_path
from athena_research.commodity_data_audit.mt5_reader import (
    Mt5ReadError,
    connect_mt5_readonly,
    copy_rates_range_utc,
    shutdown_mt5,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Forensic export of XAU H4 abnormal gaps (read-only).")
    parser.add_argument(
        "--output-dir",
        default="logs/commodity_data_audit/forensics/xau_h4",
        help="Diagnostic output directory (not frozen raw/manifest storage).",
    )
    parser.add_argument("--skip-mt5", action="store_true", help="Skip live MT5 refetch probes.")
    parser.add_argument("--mt5-path", default="", help="Optional MT5 terminal path.")
    args = parser.parse_args()

    raw_root = resolve_path("logs/commodity_data_audit/raw/v1")
    output_dir = resolve_path(args.output_dir)

    copy_rates = None
    if not args.skip_mt5:
        try:
            mt5 = connect_mt5_readonly(terminal_path=args.mt5_path or None)
        except Mt5ReadError as exc:
            print(f"status=MT5_UNAVAILABLE forensic continues without refetch", file=sys.stderr)
            print(str(exc), file=sys.stderr)
        else:
            copy_rates = lambda sym, tf, start, end: copy_rates_range_utc(mt5, sym, tf, start, end)
            try:
                result = run_xau_h4_gap_forensic(
                    raw_root=raw_root,
                    output_dir=output_dir,
                    copy_rates=copy_rates,
                    skip_mt5=False,
                )
            finally:
                shutdown_mt5(mt5)
            _print_result(result)
            return 0

    result = run_xau_h4_gap_forensic(
        raw_root=raw_root,
        output_dir=output_dir,
        copy_rates=copy_rates,
        skip_mt5=True,
    )
    _print_result(result)
    return 0


def _print_result(result: dict) -> None:
    print(f"abnormal_events={result['abnormal_event_count']}")
    print(f"events={result['events_path']}")
    print(f"summary={result['summary_path']}")
    print(f"refetch={result['refetch_path']}")
    print(f"mt5_refetch_status={result['mt5_refetch_status']}")
    rec = result["recommendation"]["evidence_summary"]
    print(f"boundary_share={rec.get('at_chunk_boundary_share')}")
    print(f"dominant_bars_per_day={rec.get('dominant_bars_per_trading_day')}")
    print(f"refetch_A={rec.get('refetch_class_a_count')} B={rec.get('refetch_class_b_count')} C={rec.get('refetch_class_c_count')}")


if __name__ == "__main__":
    raise SystemExit(main())
