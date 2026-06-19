"""Re-run commodity freeze QA on existing raw data without re-download."""

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
    QA_ALGORITHM_VERSION,
    QA_ALGORITHM_VERSION_V3,
    QA_ALGORITHM_VERSION_V3_1,
)
from athena_research.commodity_data_audit.freeze_reqa import reqa_from_original_manifest_inputs
from athena_research.commodity_data_audit.freeze_reqa_v3 import reqa_v3_from_original_manifest_inputs
from athena_research.commodity_data_audit.freeze_reqa_v3_1 import reqa_v3_1_from_original_manifest_inputs
from athena_research.commodity_data_audit.freeze_store import FreezeStoreError, read_json, resolve_path
from athena_research.commodity_data_audit.mt5_reader import (
    Mt5ReadError,
    connect_mt5_readonly,
    copy_rates_range_utc,
    shutdown_mt5,
)

EXIT_OK = 0
EXIT_INCOMPLETE = 2


def _parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-QA commodity freeze artifacts without re-download.")
    parser.add_argument("--symbols", default="XAU/USD", help="Comma-separated canonical symbols.")
    parser.add_argument("--timeframes", default="H4", help="Comma-separated timeframes.")
    parser.add_argument("--start-date", default="2014-01-01", help="Requested inclusive UTC start date.")
    parser.add_argument("--end-date", default="", help="Optional inclusive UTC end date (YYYY-MM-DD).")
    parser.add_argument(
        "--end-time",
        default="",
        help="Optional exclusive UTC end timestamp (ISO-8601). Overrides --end-date when set.",
    )
    parser.add_argument(
        "--qa-version",
        default="v3_1",
        choices=("v2", "v3", "v3_1"),
        help="QA algorithm version (default v3_1 era-aware coverage + reliable gap review).",
    )
    parser.add_argument("--skip-mt5", action="store_true", help="Skip MT5 D1 refetch for v3 midnight comparison.")
    parser.add_argument("--mt5-path", default="", help="Optional MT5 terminal path.")
    args = parser.parse_args()

    symbols = _parse_csv(args.symbols)
    timeframes = [tf.upper() for tf in _parse_csv(args.timeframes)]
    unknown = [sym for sym in symbols if sym not in PHASE_1_CANONICAL_SYMBOLS]
    if unknown:
        print(f"unsupported symbols: {', '.join(unknown)}", file=sys.stderr)
        return EXIT_INCOMPLETE
    bad_tf = [tf for tf in timeframes if tf not in PHASE_1_TIMEFRAMES]
    if bad_tf:
        print(f"unsupported timeframes: {', '.join(bad_tf)}", file=sys.stderr)
        return EXIT_INCOMPLETE

    copy_rates = None
    mt5 = None
    if args.qa_version in ("v3", "v3_1") and not args.skip_mt5:
        try:
            mt5 = connect_mt5_readonly(terminal_path=args.mt5_path or None)

            def _copy_rates(sym: str, tf: str, start, end):
                return copy_rates_range_utc(mt5, sym, tf, start, end)

            copy_rates = _copy_rates
        except Mt5ReadError as exc:
            print(f"status=MT5_UNAVAILABLE continuing without D1 refetch", file=sys.stderr)
            print(str(exc), file=sys.stderr)

    if args.qa_version == "v3_1":
        qa_label = QA_ALGORITHM_VERSION_V3_1
    elif args.qa_version == "v3":
        qa_label = QA_ALGORITHM_VERSION_V3
    else:
        qa_label = QA_ALGORITHM_VERSION
    results = []
    try:
        for symbol in symbols:
            for tf in timeframes:
                print(f"[REQA] {symbol} {tf} qa={qa_label} ...", flush=True)
                try:
                    if args.qa_version == "v3_1":
                        result = reqa_v3_1_from_original_manifest_inputs(
                            canonical_symbol=symbol,
                            timeframe=tf,
                            start_date=args.start_date,
                            end_date=args.end_date,
                            end_time=args.end_time,
                            copy_rates=copy_rates,
                        )
                    elif args.qa_version == "v3":
                        result = reqa_v3_from_original_manifest_inputs(
                            canonical_symbol=symbol,
                            timeframe=tf,
                            start_date=args.start_date,
                            end_date=args.end_date,
                            end_time=args.end_time,
                            copy_rates=copy_rates,
                        )
                    else:
                        result = reqa_from_original_manifest_inputs(
                            canonical_symbol=symbol,
                            timeframe=tf,
                            start_date=args.start_date,
                            end_date=args.end_date,
                            end_time=args.end_time,
                        )
                except FreezeStoreError as exc:
                    print(f"status=BLOCKED_DATA/REQA_FAILED", file=sys.stderr)
                    print(str(exc), file=sys.stderr)
                    return EXIT_INCOMPLETE

                manifest = read_json(resolve_path(result.manifest_path))
                if args.qa_version in ("v3", "v3_1"):
                    coverage = manifest.get("coverage_audit", {})
                    print(
                        f"  rows={result.bar_count} reliable={result.reliable_era_bar_count} "
                        f"gate={result.empirical_gate}",
                        flush=True,
                    )
                    print(f"  provider_start={manifest.get('provider_actual_start')}", flush=True)
                    print(f"  reliable_start={manifest.get('reliable_research_start')}", flush=True)
                    print(f"  warmup_start={manifest.get('warmup_adjusted_usable_start')}", flush=True)
                    print(
                        f"  excluded_bars={manifest.get('excluded_bar_count')} "
                        f"excluded_days={manifest.get('excluded_day_count')}",
                        flush=True,
                    )
                    print(f"  eras={json.dumps(coverage.get('era_summary', {}), sort_keys=True)}", flush=True)
                else:
                    quality = manifest.get("quality", {})
                    gaps = quality.get("details", {}).get("gap_classification", {}).get("counts", {})
                    spread = manifest.get("spread_audit", {})
                    print(f"  rows={result.bar_count} ok={result.ok} gate={result.empirical_gate}", flush=True)
                    print(f"  truncation={quality.get('terminal_truncation_suspected')}", flush=True)
                    print(f"  gaps={json.dumps(gaps, sort_keys=True)}", flush=True)
                    print(
                        "  spread_coverage="
                        f"usable={spread.get('observed_usable_count')} "
                        f"missing={spread.get('missing_spread_count')} "
                        f"outliers={spread.get('extreme_outlier_count')}",
                        flush=True,
                    )
                print(f"  manifest={result.manifest_path}", flush=True)
                if result.issues:
                    print(f"  issues={','.join(result.issues)}", flush=True)
                results.append(result)
    finally:
        if mt5 is not None:
            shutdown_mt5(mt5)

    all_ok = all(item.ok for item in results)
    print(f"qa_algorithm_version={qa_label}")
    print(f"empirical_gate={'CLEAR_ON_FREEZE' if all_ok else results[0].empirical_gate if results else 'BLOCKED'}")
    if not all_ok:
        print("status=BLOCKED_DATA/REQA_GATE", file=sys.stderr)
        return EXIT_INCOMPLETE
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
