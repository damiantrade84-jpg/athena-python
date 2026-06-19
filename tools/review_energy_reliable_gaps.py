"""Energy-aware v4 consolidated residual-gap review for WTI Oil and Nat Gas.

Research-only. Reclassifies the residual reliable-era H4 gaps that the v3 run
left UNRESOLVED, using deterministic energy-session evidence derived entirely
from frozen artifacts and read-only raw OHLC inspection:

  * a recognized energy holiday carrying a zero-range provider *placeholder* day
    (flat D1 and flat present H4 neighbours) is a LEGITIMATE_MARKET_CLOSURE;
  * an isolated non-holiday intraday gap on a day the subject demonstrably
    traded (real-range D1 and real-range adjacent bars) is a PROVIDER_HISTORY_GAP.

No live MT5 refetch is performed: the frozen v3 targeted-refetch result
(subject_refetch_empty) is reused verbatim. Raw bars, prior manifests, and the
v1/v2/v3 review artifacts are preserved unchanged. New artifacts are written
only under forensics/consolidated_v4/.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from athena_research.commodity_data_audit.acquisition_run import research_eligible_bars
from athena_research.commodity_data_audit.consolidated_gap_review import (
    GapGate,
    REVIEW_SCHEMA_VERSION,
    build_consolidated_payload,
    residual_evidence_from_v3_event,
    write_versioned_review_artifact,
)
from athena_research.commodity_data_audit.freeze_registry import (
    resolve_phase1_mt5_symbol,
    slug_symbol,
)
from athena_research.commodity_data_audit.freeze_store import (
    hash_stable_json,
    load_raw_bars_from_store,
    read_json,
    resolve_path,
    resolve_raw_evidence_hashes,
    state_path,
    write_json_immutable,
)

EXIT_OK = 0
EXIT_BLOCKED = 2
DEFAULT_SYMBOLS = ("WTI Oil", "Nat Gas")
PRIOR_VERSION = "commodity_h4_residual_gap_review_v3"


def _prior_artifact_path(slug: str) -> Path:
    return resolve_path(
        f"logs/commodity_data_audit/forensics/consolidated_v3/{slug}/"
        f"H4.residual_gap_review.{PRIOR_VERSION}.json"
    )


def _build_symbol(canonical_symbol: str) -> tuple[dict, Path, str]:
    slug = slug_symbol(canonical_symbol)
    prior = read_json(_prior_artifact_path(slug))

    raw_root = resolve_path("logs/commodity_data_audit/raw/v1")
    bars = load_raw_bars_from_store(raw_root, slug, "H4")
    subject_lookup = {int(bar["time"]): bar for bar in bars}

    events = [
        residual_evidence_from_v3_event(event, subject_bar_lookup=subject_lookup)
        for event in prior["events"]
    ]

    state = read_json(state_path(raw_root, slug, "H4"))
    pinned_as_of = datetime.fromisoformat(str(state["pinned_as_of"]).replace("Z", "+00:00"))
    confirmed_bars = research_eligible_bars(bars, timeframe="H4", pinned_as_of=pinned_as_of)
    reliable_start = str(prior["reliable_start"])
    reliable_observed = [
        datetime.fromtimestamp(int(bar["time"]), tz=timezone.utc)
        for bar in confirmed_bars
        if datetime.fromtimestamp(int(bar["time"]), tz=timezone.utc).date().isoformat()
        >= reliable_start
    ]
    expected = sorted(
        set(reliable_observed).union(
            timestamp for event in events for timestamp in event.missing_timestamps
        )
    )

    hashes = resolve_raw_evidence_hashes(raw_root, slug, "H4")
    source_hashes = {
        "raw_content_hash": hashes["merged_bar_content_hash"],
        "chunk_set_hash": hashes["chunk_set_hash"],
    }
    # Prove the raw evidence is byte-identical to the superseded v3 run.
    raw_unchanged = source_hashes == prior.get("source_hashes")

    review = build_consolidated_payload(
        canonical_symbol=canonical_symbol,
        terminal_symbol=resolve_phase1_mt5_symbol(canonical_symbol),
        events=events,
        expected_timestamps=expected,
        reliable_start=reliable_start,
        usable_start=prior.get("usable_start"),
        source_hashes=source_hashes,
    )
    review["prior_artifact"] = {
        "schema": PRIOR_VERSION,
        "run_hash": prior.get("run_hash"),
        "raw_source_unchanged_vs_prior": raw_unchanged,
        "gate": prior.get("gate"),
        "classification_counts": prior.get("classification_counts"),
    }
    review["provider_gap_intervals"] = [
        {
            "event_id": event.event_id,
            "prev_bar_timestamp": event.prev_bar_timestamp.isoformat(),
            "next_bar_timestamp": event.next_bar_timestamp.isoformat(),
            "missing_timestamps": [ts.isoformat() for ts in event.missing_timestamps],
        }
        for event, classification in zip(events, [row["classification"] for row in review["events"]])
        if classification == "PROVIDER_HISTORY_GAP"
    ]
    review["run_hash"] = hash_stable_json(
        {k: v for k, v in review.items() if k != "run_hash"}
    )

    out = resolve_path(
        f"logs/commodity_data_audit/forensics/consolidated_v4/{slug}/"
        f"H4.residual_gap_review.{REVIEW_SCHEMA_VERSION}.json"
    )
    digest = write_versioned_review_artifact(out, review)
    return review, out, digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    args = parser.parse_args()
    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]

    summaries = []
    failures = []
    for symbol in symbols:
        try:
            payload, path, digest = _build_symbol(symbol)
            summaries.append(
                {
                    "canonical_symbol": symbol,
                    "gate": payload["gate"],
                    "classification_counts": payload["classification_counts"],
                    "admissibility": payload["admissibility"],
                    "provider_gap_intervals": payload["provider_gap_intervals"],
                    "raw_source_unchanged_vs_prior": payload["prior_artifact"][
                        "raw_source_unchanged_vs_prior"
                    ],
                    "artifact_path": str(path.relative_to(REPO)).replace("\\", "/"),
                    "artifact_hash": digest,
                }
            )
            print(
                f"{symbol}: gate={payload['gate']} "
                f"counts={payload['classification_counts']} "
                f"raw_unchanged={payload['prior_artifact']['raw_source_unchanged_vs_prior']}"
            )
        except Exception as exc:  # noqa: BLE001 - report per-symbol failure
            failures.append({"canonical_symbol": symbol, "error": str(exc)})
            print(f"{symbol}: FAILED {exc}", file=sys.stderr)

    summary = {
        "schema": REVIEW_SCHEMA_VERSION,
        "symbols": summaries,
        "failures": failures,
    }
    summary["run_hash"] = hash_stable_json(summary)
    summary_path = resolve_path(
        "logs/commodity_data_audit/forensics/consolidated_v4/"
        "commodity_h4_residual_gap_summary_v4.json"
    )
    write_json_immutable(summary_path, summary)

    clear = {GapGate.CLEAR_ON_FREEZE.value, GapGate.CLEAR_WITH_GAP_EMBARGO.value}
    return EXIT_OK if not failures and all(row["gate"] in clear for row in summaries) else EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
