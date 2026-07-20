"""Phase-1 commodity MT5 freeze — research-only, no execution paths."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from athena_research.commodity_data_audit.freeze import (
    TOOL_VERSION,
    default_copy_rates,
    freeze_one_series,
)
from athena_research.commodity_data_audit.freeze_registry import (
    CHUNK_DAYS_BY_TIMEFRAME,
    DEFAULT_REQUESTED_START,
    PHASE_1_CANONICAL_SYMBOLS,
    PHASE_1_TIMEFRAMES,
    parse_utc_datetime,
)
from athena_research.commodity_data_audit.freeze_range import resolve_requested_range
from athena_research.commodity_data_audit.freeze_store import FreezeStoreError
from athena_research.commodity_data_audit.mt5_reader import (
    Mt5ReadError,
    broker_identity,
    connect_mt5_readonly,
    fetch_symbol_metadata,
    shutdown_mt5,
)
from athena_research.commodity_data_audit.freeze_registry import resolve_phase1_mt5_symbol

EXIT_OK = 0
EXIT_INCOMPLETE = 2
EXIT_MT5_UNAVAILABLE = 3


def _parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase-1 commodity MT5 freeze (research-only).")
    parser.add_argument(
        "--symbols",
        default=",".join(PHASE_1_CANONICAL_SYMBOLS),
        help="Comma-separated canonical Athena display names.",
    )
    parser.add_argument(
        "--timeframes",
        default=",".join(PHASE_1_TIMEFRAMES),
        help="Comma-separated timeframes (native MT5; H4 is not resampled).",
    )
    parser.add_argument("--start-date", default="2014-01-01", help="Requested UTC start date.")
    parser.add_argument(
        "--end-date",
        default="",
        help="Inclusive UTC end date (YYYY-MM-DD). Stored as end-of-day inclusive; filter uses next-day exclusive boundary.",
    )
    parser.add_argument(
        "--end-time",
        default="",
        help="Exclusive UTC end timestamp (ISO-8601). Overrides --end-date when set.",
    )
    parser.add_argument(
        "--chunk-days",
        default="",
        help="Optional override chunk size in days; defaults per timeframe.",
    )
    parser.add_argument("--no-resume", action="store_true", help="Disable chunk resume state.")
    parser.add_argument(
        "--as-of",
        default="",
        help="Pin acquisition as_of (ISO-8601 UTC). Resume reuses stored pin unless --new-acquisition-run.",
    )
    parser.add_argument(
        "--new-acquisition-run",
        action="store_true",
        help="Start a new acquisition run with a fresh pinned as_of (does not overwrite existing chunks).",
    )
    parser.add_argument("--mt5-path", default="", help="Optional MT5 terminal path.")
    args = parser.parse_args()

    symbols = _parse_csv(args.symbols)
    timeframes = [tf.upper() for tf in _parse_csv(args.timeframes)]
    unknown = [sym for sym in symbols if sym not in PHASE_1_CANONICAL_SYMBOLS]
    if unknown:
        print(f"unsupported Phase-1 symbols: {', '.join(unknown)}", file=sys.stderr)
        return EXIT_INCOMPLETE
    bad_tf = [tf for tf in timeframes if tf not in PHASE_1_TIMEFRAMES]
    if bad_tf:
        print(f"unsupported timeframes: {', '.join(bad_tf)}", file=sys.stderr)
        return EXIT_INCOMPLETE

    requested_range = resolve_requested_range(
        start_date=args.start_date,
        end_date=args.end_date,
        end_time=args.end_time,
    )
    requested_start = requested_range.start_inclusive
    requested_end = requested_range.end_exclusive
    pinned_as_of = parse_utc_datetime(args.as_of) if args.as_of.strip() else None

    try:
        mt5 = connect_mt5_readonly(terminal_path=args.mt5_path or None)
    except Mt5ReadError as exc:
        print(f"status=BLOCKED_DATA/MT5_UNAVAILABLE", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return EXIT_MT5_UNAVAILABLE

    broker = broker_identity(mt5)
    copy_rates = default_copy_rates(mt5)
    metadata_cache: dict[str, dict] = {}

    results = []
    try:
        for symbol in symbols:
            terminal = resolve_phase1_mt5_symbol(symbol)
            if symbol not in metadata_cache:
                metadata_cache[symbol] = fetch_symbol_metadata(mt5, terminal)
            for tf in timeframes:
                chunk_days = (
                    int(args.chunk_days)
                    if args.chunk_days.strip()
                    else CHUNK_DAYS_BY_TIMEFRAME[tf]
                )
                print(f"[FREEZE] {symbol} {tf} terminal={terminal} ...", flush=True)
                try:
                    result = freeze_one_series(
                        canonical_symbol=symbol,
                        timeframe=tf,
                        requested_start=requested_start,
                        requested_end=requested_end,
                        chunk_days=chunk_days,
                        copy_rates=copy_rates,
                        symbol_metadata=metadata_cache[symbol],
                        broker=broker,
                        resume=not args.no_resume,
                        as_of=pinned_as_of,
                        new_acquisition_run=args.new_acquisition_run,
                    )
                except Mt5ReadError as exc:
                    print(f"status=BLOCKED_DATA/MT5_UNAVAILABLE", file=sys.stderr)
                    print(str(exc), file=sys.stderr)
                    return EXIT_MT5_UNAVAILABLE
                except FreezeStoreError as exc:
                    print(f"status=BLOCKED_DATA/STORE_CONFLICT", file=sys.stderr)
                    print(str(exc), file=sys.stderr)
                    return EXIT_INCOMPLETE
                results.append(result)
                print(
                    f"  rows={result.bar_count} ok={result.ok} manifest={result.manifest_path}",
                    flush=True,
                )
                if result.issues:
                    print(f"  issues={','.join(result.issues)}", flush=True)
    finally:
        shutdown_mt5(mt5)

    all_ok = all(item.ok for item in results)
    print(f"tool={TOOL_VERSION}")
    print(f"capability_gate=CLEAR_ON_CAPABILITY")
    print(f"empirical_gate={'CLEAR_ON_FREEZE' if all_ok else 'BLOCKED_INCOMPLETE_ACQUISITION'}")
    if not all_ok:
        print("status=BLOCKED_DATA/INCOMPLETE_ACQUISITION", file=sys.stderr)
        return EXIT_INCOMPLETE
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
