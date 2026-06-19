"""Build versioned family-aware commodity H4 residual-gap reviews (research only)."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from athena_research.commodity_data_audit.consolidated_gap_review import (
    GapGate,
    REVIEW_SCHEMA_VERSION,
    build_consolidated_payload,
    family_policy_for_symbol,
    residual_evidence_from_gap_rows,
    write_versioned_review_artifact,
)
from athena_research.commodity_data_audit.acquisition_run import research_eligible_bars
from athena_research.commodity_data_audit.freeze_registry import (
    PHASE_1_CANONICAL_SYMBOLS,
    resolve_phase1_mt5_symbol,
    slug_symbol,
)
from athena_research.commodity_data_audit.freeze_store import (
    hash_stable_json,
    load_raw_bars_from_store,
    qa_manifest_path,
    read_json,
    resolve_path,
    resolve_raw_evidence_hashes,
    state_path,
    write_json_immutable,
)
from athena_research.commodity_data_audit.gap_forensic import load_chunk_boundaries
from athena_research.commodity_data_audit.mt5_reader import (
    Mt5ReadError,
    connect_mt5_readonly,
    copy_rates_range_utc,
    shutdown_mt5,
)
from athena_research.commodity_data_audit.reliable_era_gap_review import (
    review_reliable_era_abnormal_gaps,
    review_to_dict,
)

EXIT_OK = 0
EXIT_BLOCKED = 2
EXIT_MT5_UNAVAILABLE = 3
DEFAULT_SYMBOLS = ("XPT/USD", "XPD/USD", "WTI Oil", "Brent Oil", "Nat Gas", "Gasoline")


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _review_payload(
    *,
    canonical_symbol: str,
    peer_symbol: str | None,
    reliable_start: str,
    copy_rates,
) -> dict:
    raw_root = resolve_path("logs/commodity_data_audit/raw/v1")
    slug = slug_symbol(canonical_symbol)
    bars = load_raw_bars_from_store(raw_root, slug, "H4")
    peer_bars = None
    peer_terminal = resolve_phase1_mt5_symbol(canonical_symbol)
    if peer_symbol:
        peer_bars = load_raw_bars_from_store(raw_root, slug_symbol(peer_symbol), "H4")
        peer_terminal = resolve_phase1_mt5_symbol(peer_symbol)
    reviews = review_reliable_era_abnormal_gaps(
        bars=bars,
        chunks=load_chunk_boundaries(raw_root, slug, "H4"),
        reliable_start=reliable_start,
        copy_rates=copy_rates,
        subject_terminal=resolve_phase1_mt5_symbol(canonical_symbol),
        peer_terminal=peer_terminal,
        peer_bars=peer_bars,
    )
    return review_to_dict(
        reviews,
        canonical_symbol=canonical_symbol,
        peer_symbol=peer_symbol,
        timeframe="H4",
    )


def _build_symbol(canonical_symbol: str, copy_rates) -> tuple[dict, Path, str]:
    slug = slug_symbol(canonical_symbol)
    normalized_root = resolve_path("logs/commodity_data_audit/normalized/commodity_ohlcv_v1")
    qa_path = qa_manifest_path(
        normalized_root, slug, "H4", qa_algorithm_version="commodity_freeze_qa_v3"
    )
    qa = read_json(qa_path)
    reliable_start = str(qa["reliable_research_start"])
    policy = family_policy_for_symbol(canonical_symbol)
    peers: tuple[str | None, ...] = policy.primary_peers + policy.secondary_peers
    if not peers:
        peers = (None,)
    payloads = [
        (peer, _review_payload(
            canonical_symbol=canonical_symbol,
            peer_symbol=peer,
            reliable_start=reliable_start,
            copy_rates=copy_rates,
        ))
        for peer in peers
    ]
    event_ids = [gap["event_id"] for gap in payloads[0][1]["gaps"]]
    events = []
    for event_id in event_ids:
        rows = []
        for peer, payload in payloads:
            by_id = {gap["event_id"]: gap for gap in payload["gaps"]}
            if event_id in by_id:
                rows.append((peer, by_id[event_id]))
        events.append(
            residual_evidence_from_gap_rows(
                canonical_symbol=canonical_symbol,
                peer_gap_rows=rows,
            )
        )
    raw_root = resolve_path("logs/commodity_data_audit/raw/v1")
    bars = load_raw_bars_from_store(raw_root, slug, "H4")
    state = read_json(state_path(raw_root, slug, "H4"))
    pinned_as_of = datetime.fromisoformat(str(state["pinned_as_of"]).replace("Z", "+00:00"))
    confirmed_bars = research_eligible_bars(
        bars, timeframe="H4", pinned_as_of=pinned_as_of
    )
    reliable_observed = [
        datetime.fromtimestamp(int(bar["time"]), tz=timezone.utc)
        for bar in confirmed_bars
        if datetime.fromtimestamp(int(bar["time"]), tz=timezone.utc).date().isoformat()
        >= reliable_start
    ]
    expected = sorted(set(reliable_observed).union(
        timestamp for event in events for timestamp in event.missing_timestamps
    ))
    hashes = resolve_raw_evidence_hashes(raw_root, slug, "H4")
    review = build_consolidated_payload(
        canonical_symbol=canonical_symbol,
        terminal_symbol=resolve_phase1_mt5_symbol(canonical_symbol),
        events=events,
        expected_timestamps=expected,
        reliable_start=reliable_start,
        usable_start=qa.get("warmup_adjusted_usable_start"),
        source_hashes={
            "raw_content_hash": hashes["merged_bar_content_hash"],
            "chunk_set_hash": hashes["chunk_set_hash"],
        },
    )
    out = resolve_path(
        f"logs/commodity_data_audit/forensics/consolidated_v3/{slug}/"
        f"H4.residual_gap_review.{REVIEW_SCHEMA_VERSION}.json"
    )
    digest = write_versioned_review_artifact(out, review)
    return review, out, digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    args = parser.parse_args()
    symbols = _parse_csv(args.symbols)
    unsupported = [symbol for symbol in symbols if symbol not in PHASE_1_CANONICAL_SYMBOLS]
    if unsupported:
        print(f"unsupported symbols: {unsupported}", file=sys.stderr)
        return EXIT_BLOCKED
    try:
        mt5 = connect_mt5_readonly()
    except Mt5ReadError as exc:
        print(f"status=MT5_UNAVAILABLE {exc}", file=sys.stderr)
        return EXIT_MT5_UNAVAILABLE

    def copy_rates(symbol, timeframe, start, end):
        return copy_rates_range_utc(mt5, symbol, timeframe, start, end)

    summaries = []
    failures = []
    try:
        for symbol in symbols:
            try:
                payload, path, digest = _build_symbol(symbol, copy_rates)
                summaries.append(
                    {
                        "canonical_symbol": symbol,
                        "gate": payload["gate"],
                        "classification_counts": payload["classification_counts"],
                        "admissibility": payload["admissibility"],
                        "artifact_path": str(path.relative_to(REPO)).replace("\\", "/"),
                        "artifact_hash": digest,
                    }
                )
                print(f"{symbol}: gate={payload['gate']} events={payload['event_count']}")
            except Exception as exc:
                failures.append({"canonical_symbol": symbol, "error": str(exc)})
                print(f"{symbol}: FAILED {exc}", file=sys.stderr)
    finally:
        shutdown_mt5(mt5)
    summary = {
        "schema": REVIEW_SCHEMA_VERSION,
        "symbols": summaries,
        "failures": failures,
    }
    summary["run_hash"] = hash_stable_json(summary)
    summary_path = resolve_path(
        "logs/commodity_data_audit/forensics/consolidated_v3/"
        "commodity_h4_residual_gap_summary_v3.json"
    )
    write_json_immutable(summary_path, summary)
    clear = {GapGate.CLEAR_ON_FREEZE.value, GapGate.CLEAR_WITH_GAP_EMBARGO.value}
    return EXIT_OK if not failures and all(row["gate"] in clear for row in summaries) else EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
