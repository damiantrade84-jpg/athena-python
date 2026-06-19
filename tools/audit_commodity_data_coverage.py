"""Engine A V4 commodity provider capability audit (read-only).

Writes coverage matrix JSON and acquisition manifest without broad downloads.
Optional MT5 symbol-existence probe when a terminal is connected.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")
os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

from athena_research.commodity_data_audit.costs import modelled_costs_for_instrument
from athena_research.commodity_data_audit.manifest import build_acquisition_manifest
from athena_research.commodity_data_audit.mt5_probe import (
    EMPIRICAL_BLOCKED_NOT_PROBED,
    EMPIRICAL_CLEAR_ON_PROBE,
    empirical_gate_status,
    run_mt5_symbol_probe,
)
from athena_research.commodity_data_audit.registry import (
    INDEPENDENT_CLUSTER_MIN,
    build_coverage_matrix,
    independent_cluster_count,
    load_commodity_pairs,
    recommended_acquisition_batch,
)

EXIT_OK = 0
EXIT_PROBE_FAILED = 2


def _parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _filter_pairs(symbols: list[str] | None) -> list[dict]:
    pairs = load_commodity_pairs()
    if not symbols:
        return [pair for pair in pairs if pair.get("enabled", True)]
    wanted = set(symbols)
    selected = [pair for pair in pairs if pair.get("display") in wanted]
    missing = sorted(wanted.difference({pair.get("display") for pair in selected}))
    if missing:
        raise SystemExit(f"Unknown commodity displays: {', '.join(missing)}")
    return selected


def build_report(
    *,
    symbols: list[str] | None,
    timeframes: list[str],
    probe_mt5: bool,
    probe_outcome=None,
) -> dict:
    pairs = _filter_pairs(symbols)
    matrix = build_coverage_matrix(pairs)
    batch = recommended_acquisition_batch()
    if symbols:
        batch["symbols"] = [pair["display"] for pair in pairs]
    batch["timeframes"] = [tf.upper() for tf in timeframes]
    manifest = build_acquisition_manifest(
        symbols=batch["symbols"],
        timeframes=batch["timeframes"],
    )
    rejected = [
        {
            "canonical_instrument": row["canonical_instrument"],
            "reason": row["rejection_reason"],
        }
        for row in matrix
        if row["rejection_reason"]
    ]
    costs = {
        row["canonical_instrument"]: {
            "spread_history": row["spread_history"],
            "financing_history": row["financing_history"],
            "modelled_spread_points": row["modelled_spread_points"],
            "stress_multipliers": list(modelled_costs_for_instrument(row["canonical_instrument"]).stress_multipliers),
        }
        for row in matrix
    }
    capability_status = (
        "CLEAR_ON_CAPABILITY"
        if independent_cluster_count(pairs) >= INDEPENDENT_CLUSTER_MIN
        else "BLOCKED_INSUFFICIENT_CLUSTERS"
    )
    empirical_status = empirical_gate_status(
        probe_mt5_requested=probe_mt5,
        probe_outcome=probe_outcome,
    )
    report = {
        "independent_cluster_count": independent_cluster_count(pairs),
        "independent_cluster_minimum": INDEPENDENT_CLUSTER_MIN,
        "capability_gate_status": capability_status,
        "empirical_gate_status": empirical_status,
        "no_code_gate_status": capability_status,
        "coverage_matrix": matrix,
        "rejected_instruments": rejected,
        "observed_vs_modelled_cost_status": {
            "spread_history": "modelled",
            "financing_history": "modelled",
            "notes": "No immutable historical bid/ask spread archive in current providers.",
        },
        "recommended_acquisition_batch": batch,
        "acquisition_manifest": manifest,
        "modelled_costs": costs,
    }
    if probe_mt5 and probe_outcome is not None:
        report["mt5_symbol_probe"] = probe_outcome.probe
        report["empirical_gate_reason"] = probe_outcome.reason_code
        if probe_outcome.unavailable_symbols:
            report["empirical_gate_unavailable_symbols"] = list(probe_outcome.unavailable_symbols)
    elif not probe_mt5:
        report["empirical_gate_reason"] = "PROBE_NOT_REQUESTED"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit commodity provider coverage for Engine A V4.")
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated Athena display names (default: all enabled commodities).",
    )
    parser.add_argument(
        "--timeframes",
        default="H4,H1,D1",
        help="Comma-separated timeframes for acquisition manifest.",
    )
    parser.add_argument(
        "--registry-only",
        action="store_true",
        help="Offline registry audit only (default behaviour).",
    )
    parser.add_argument(
        "--probe-mt5",
        action="store_true",
        help="Check MT5 symbol_select availability without downloading candles.",
    )
    parser.add_argument(
        "--output",
        default=str(REPO / "tests" / "_artifacts" / "commodity_data_coverage.json"),
        help="JSON report path.",
    )
    args = parser.parse_args()

    symbols = _parse_csv(args.symbols) or None
    timeframes = _parse_csv(args.timeframes) or ["H4", "H1", "D1"]
    pairs = _filter_pairs(symbols)
    probe_outcome = run_mt5_symbol_probe(pairs) if args.probe_mt5 else None
    report = build_report(
        symbols=symbols,
        timeframes=timeframes,
        probe_mt5=bool(args.probe_mt5),
        probe_outcome=probe_outcome,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"capability_gate={report['capability_gate_status']}")
    print(f"empirical_gate={report['empirical_gate_status']}")
    print(f"independent_clusters={report['independent_cluster_count']} min={INDEPENDENT_CLUSTER_MIN}")
    print(f"rejected={len(report['rejected_instruments'])}")
    print(f"report={output}")

    if args.probe_mt5:
        if probe_outcome is None or not probe_outcome.ok:
            reason = (probe_outcome.reason_code if probe_outcome else "MT5_UNAVAILABLE")
            print(f"status=BLOCKED_DATA/{reason}", file=sys.stderr)
            if probe_outcome and probe_outcome.unavailable_symbols:
                print(
                    "unavailable_symbols="
                    + ",".join(probe_outcome.unavailable_symbols),
                    file=sys.stderr,
                )
            return EXIT_PROBE_FAILED

        print(f"status={EMPIRICAL_CLEAR_ON_PROBE}")
        print(
            "acquire_cmd="
            f"python tools/audit_commodity_data_coverage.py "
            f"--symbols \"{','.join(report['recommended_acquisition_batch']['symbols'][:8])}\" "
            f"--timeframes {','.join(timeframes)} --probe-mt5"
        )
        return EXIT_OK

    print(f"empirical_gate={EMPIRICAL_BLOCKED_NOT_PROBED} (run with --probe-mt5 to verify MT5 symbols)")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
