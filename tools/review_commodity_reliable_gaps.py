"""Symbol-generic reliable-era abnormal gap review (read-only)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from athena_research.commodity_data_audit.freeze_registry import (
    PHASE_1_CANONICAL_SYMBOLS,
    PHASE_1_TIMEFRAMES,
    QA_ALGORITHM_VERSION_V3_1,
    default_reliable_peer_symbol,
)
from athena_research.commodity_data_audit.mt5_reader import (
    Mt5ReadError,
    connect_mt5_readonly,
    copy_rates_range_utc,
    shutdown_mt5,
)
from athena_research.commodity_data_audit.reliable_era_gap_review import (
    DEFAULT_RELIABLE_START,
    run_commodity_reliable_gap_review_with_qa,
    run_commodity_reliable_era_gap_review,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review reliable-era abnormal gaps for a commodity symbol (read-only)."
    )
    parser.add_argument("--symbol", required=True, help="Canonical symbol, e.g. XAG/USD")
    parser.add_argument("--timeframe", default="H4", help="Timeframe (default H4).")
    parser.add_argument(
        "--peer-symbol",
        default="",
        help="Corroborating peer symbol (default: registry default for subject, e.g. XAU/USD for XAG/USD).",
    )
    parser.add_argument(
        "--qa-version",
        default="",
        help="Optional QA manifest version to write (supported: v3_1 / commodity_freeze_qa_v3_1).",
    )
    parser.add_argument("--reliable-start", default=DEFAULT_RELIABLE_START)
    parser.add_argument("--start-date", default="2014-01-01", help="Requested start for QA manifest.")
    parser.add_argument("--end-date", default="", help="Optional inclusive UTC end date for QA manifest.")
    parser.add_argument("--end-time", default="", help="Optional exclusive UTC end timestamp for QA manifest.")
    parser.add_argument("--skip-mt5", action="store_true", help="Skip MT5 refetch corroboration.")
    parser.add_argument("--mt5-path", default="", help="Optional MT5 terminal path.")
    args = parser.parse_args()

    symbol = args.symbol.strip()
    timeframe = args.timeframe.upper().strip()
    if symbol not in PHASE_1_CANONICAL_SYMBOLS:
        print(f"unsupported symbol: {symbol}", file=sys.stderr)
        return 2
    if timeframe not in PHASE_1_TIMEFRAMES:
        print(f"unsupported timeframe: {timeframe}", file=sys.stderr)
        return 2

    peer_symbol = args.peer_symbol.strip() or default_reliable_peer_symbol(symbol)
    if not peer_symbol:
        print(f"no default peer for {symbol}; pass --peer-symbol", file=sys.stderr)
        return 2

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
        if args.qa_version.strip():
            result = run_commodity_reliable_gap_review_with_qa(
                canonical_symbol=symbol,
                timeframe=timeframe,
                peer_symbol=peer_symbol,
                reliable_start=args.reliable_start,
                copy_rates=copy_rates,
                qa_version=args.qa_version.strip(),
                start_date=args.start_date,
                end_date=args.end_date,
                end_time=args.end_time,
            )
        else:
            result = run_commodity_reliable_era_gap_review(
                canonical_symbol=symbol,
                timeframe=timeframe,
                peer_symbol=peer_symbol,
                reliable_start=args.reliable_start,
                copy_rates=copy_rates,
            )
    finally:
        if mt5 is not None:
            shutdown_mt5(mt5)

    print(f"symbol={symbol}")
    print(f"peer_symbol={peer_symbol}")
    print(f"timeframe={timeframe}")
    print(f"gap_count={result['gap_count']}")
    print(f"classification_counts={json.dumps(result['classification_counts'], sort_keys=True)}")
    print(f"all_legitimate_closures={result['all_legitimate_closures']}")
    print(f"review={result['review_path']}")
    if result.get("qa_manifest_path"):
        print(f"qa_manifest={result['qa_manifest_path']}")
        print(f"empirical_gate={result.get('empirical_gate')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
