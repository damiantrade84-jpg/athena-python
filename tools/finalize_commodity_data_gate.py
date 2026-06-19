"""Final commodity H4 data-gate consolidation (research only).

Reproducible from frozen artifacts only -- no MT5, no raw mutation. Produces:

  1. consolidated v4 reviews for XAU/USD and XAG/USD, reproducing their validated
     full-holiday closures (CLEAR_ON_FREEZE) from the original reliable-era
     forensic evidence (frozen peer corroboration), referencing that artifact's
     hash. Their classification is not redone or weakened.
  2. a WTI Oil NON_TRADING_PLACEHOLDER exclusion mask + manifest: every
     zero-range (O==H==L==C) carry-forward bar is excluded from candidate
     timestamps and indicator observations; raw bars are preserved unchanged.
  3. a Nat Gas provider-gap exclusion interval artifact (CLEAR_WITH_GAP_EMBARGO,
     unchanged; StrategyEmbargoContract intentionally NOT requested).
  4. one authoritative consolidated gate table across all clusters, with raw-hash
     verification and the exact qualifying count.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from athena_research.commodity_data_audit.acquisition_run import research_eligible_bars
from athena_research.commodity_data_audit.consolidated_gap_review import (
    REVIEW_SCHEMA_VERSION,
    build_consolidated_payload,
    family_policy_for_symbol,
    non_trading_placeholder_events,
    residual_evidence_from_gap_rows,
    write_versioned_review_artifact,
)
from athena_research.commodity_data_audit.freeze_registry import (
    resolve_phase1_mt5_symbol,
    slug_symbol,
)
from athena_research.commodity_data_audit.freeze_store import (
    hash_file_bytes,
    hash_stable_json,
    load_raw_bars_from_store,
    qa_manifest_path,
    read_json,
    resolve_path,
    resolve_raw_evidence_hashes,
    state_path,
    write_json_immutable,
)
from athena_research.commodity_data_audit.gap_embargo import build_placeholder_exclusion_mask

RAW_ROOT = resolve_path("logs/commodity_data_audit/raw/v1")
NORM_ROOT = resolve_path("logs/commodity_data_audit/normalized/commodity_ohlcv_v1")
V4_ROOT = resolve_path("logs/commodity_data_audit/forensics/consolidated_v4")
V3_ROOT = resolve_path("logs/commodity_data_audit/forensics/consolidated_v3")

# Authoritative source version per cluster.
ALL_CLUSTERS = [
    ("XAU/USD", "v4"),
    ("XAG/USD", "v4"),
    ("XPT/USD", "v3"),
    ("XPD/USD", "v3"),
    ("WTI Oil", "v4"),
    ("Brent Oil", "v3"),
    ("Nat Gas", "v4"),
    ("Gasoline", "v3"),
]
BASE_METAL_ORDER = ["Copper", "Aluminium", "Nickel", "Zinc"]
CLEAR = {"CLEAR_ON_FREEZE", "CLEAR_WITH_GAP_EMBARGO"}


def _reliable_and_usable(slug: str) -> tuple[str, str | None]:
    qa = read_json(qa_manifest_path(NORM_ROOT, slug, "H4", qa_algorithm_version="commodity_freeze_qa_v3"))
    return str(qa["reliable_research_start"]), qa.get("warmup_adjusted_usable_start")


def _pinned_as_of(slug: str, bars: list[dict]) -> datetime:
    state = read_json(state_path(RAW_ROOT, slug, "H4"))
    if state.get("pinned_as_of"):
        return datetime.fromisoformat(str(state["pinned_as_of"]).replace("Z", "+00:00"))
    # Older freezes (XAU/XAG) have no pinned_as_of: all historical bars are
    # confirmed, so pin just past the last bar's close.
    last = max(int(b["time"]) for b in bars)
    return datetime.fromtimestamp(last, tz=timezone.utc) + timedelta(days=1)


def _expected_timestamps(slug: str, reliable_start: str, events) -> list[datetime]:
    bars = load_raw_bars_from_store(RAW_ROOT, slug, "H4")
    pinned = _pinned_as_of(slug, bars)
    confirmed = research_eligible_bars(bars, timeframe="H4", pinned_as_of=pinned)
    observed = [
        datetime.fromtimestamp(int(b["time"]), tz=timezone.utc)
        for b in confirmed
        if datetime.fromtimestamp(int(b["time"]), tz=timezone.utc).date().isoformat() >= reliable_start
    ]
    return sorted(set(observed).union(ts for e in events for ts in e.missing_timestamps))


def onboard_metal(canonical_symbol: str) -> dict:
    slug = slug_symbol(canonical_symbol)
    policy = family_policy_for_symbol(canonical_symbol)
    peer_symbol = policy.primary_peers[0]
    forensic_path = resolve_path(
        f"logs/commodity_data_audit/forensics/{slug}_H4_reliable_gaps/"
        f"{slug}_H4_reliable_era_gap_review.json"
    )
    forensic = read_json(forensic_path)
    events = [
        residual_evidence_from_gap_rows(
            canonical_symbol=canonical_symbol,
            peer_gap_rows=[(peer_symbol, gap)],
        )
        for gap in forensic["gaps"]
    ]
    reliable_start, usable_start = _reliable_and_usable(slug)
    expected = _expected_timestamps(slug, reliable_start, events)
    hashes = resolve_raw_evidence_hashes(RAW_ROOT, slug, "H4")
    review = build_consolidated_payload(
        canonical_symbol=canonical_symbol,
        terminal_symbol=resolve_phase1_mt5_symbol(canonical_symbol),
        events=events,
        expected_timestamps=expected,
        reliable_start=reliable_start,
        usable_start=usable_start,
        source_hashes={
            "raw_content_hash": hashes["merged_bar_content_hash"],
            "chunk_set_hash": hashes["chunk_set_hash"],
        },
    )
    # The reliable-era forensic recorded raw_content_hash via hash_file_bytes of
    # merged_raw.json; compare with the same method to prove the raw is unchanged.
    merged = RAW_ROOT / slug / "H4" / "merged_raw.json"
    current_merged_file_hash = hash_file_bytes(merged) if merged.exists() else None
    review["onboarded_from_forensic"] = {
        "tool_version": forensic.get("tool_version"),
        "artifact_path": str(forensic_path.relative_to(REPO)).replace("\\", "/"),
        "artifact_sha256": hash_file_bytes(forensic_path),
        "prior_classification_counts": forensic.get("classification_counts"),
        "prior_raw_content_hash": forensic.get("raw_content_hash"),
        "current_merged_raw_file_hash": current_merged_file_hash,
        "raw_content_unchanged": current_merged_file_hash == forensic.get("raw_content_hash"),
        "peer_symbol": peer_symbol,
    }
    review["run_hash"] = hash_stable_json({k: v for k, v in review.items() if k != "run_hash"})
    out = V4_ROOT / slug / f"H4.residual_gap_review.{REVIEW_SCHEMA_VERSION}.json"
    digest = write_versioned_review_artifact(out, review)
    return {
        "canonical_symbol": canonical_symbol,
        "gate": review["gate"],
        "classification_counts": review["classification_counts"],
        "artifact_path": str(out.relative_to(REPO)).replace("\\", "/"),
        "artifact_hash": digest,
        "raw_content_unchanged": review["onboarded_from_forensic"]["raw_content_unchanged"],
        "onboarded_forensic_sha256": review["onboarded_from_forensic"]["artifact_sha256"],
    }


def build_wti_placeholder_mask() -> dict:
    slug = slug_symbol("WTI Oil")
    reliable_start, usable_start = _reliable_and_usable(slug)
    bars = load_raw_bars_from_store(RAW_ROOT, slug, "H4")
    pinned = _pinned_as_of(slug, bars)
    placeholders = non_trading_placeholder_events(
        bars, reliable_start=reliable_start, usable_start=usable_start
    )
    placeholder_ts = [
        datetime.fromisoformat(row["timestamp"]) for row in placeholders if row["in_reliable_era"]
    ]
    confirmed = research_eligible_bars(bars, timeframe="H4", pinned_as_of=pinned)
    candidates = [
        datetime.fromtimestamp(int(b["time"]), tz=timezone.utc)
        for b in confirmed
        if datetime.fromtimestamp(int(b["time"]), tz=timezone.utc).date().isoformat() >= reliable_start
    ]
    hashes = resolve_raw_evidence_hashes(RAW_ROOT, slug, "H4")
    mask = build_placeholder_exclusion_mask(
        candidates,
        placeholder_ts,
        run_context={
            "symbol": "WTI Oil",
            "timeframe": "H4",
            "reliable_start": reliable_start,
            "raw_content_hash": hashes["merged_bar_content_hash"],
        },
    )
    excluded_in_universe = sum(1 for a in mask.allowed if a is False)
    payload = {
        "schema": "commodity_h4_non_trading_placeholder_mask_v1",
        "canonical_symbol": "WTI Oil",
        "timeframe": "H4",
        "classification": "NON_TRADING_PLACEHOLDER",
        "policy": (
            "Zero-range O==H==L==C bars are non-trading carry-forward placeholders. "
            "Excluded from candidate timestamps and indicator observations. Never "
            "treated as real zero-volatility trades. Raw bars preserved unchanged."
        ),
        "reliable_start": reliable_start,
        "usable_start": usable_start,
        "source_hashes": {
            "raw_content_hash": hashes["merged_bar_content_hash"],
            "chunk_set_hash": hashes["chunk_set_hash"],
        },
        "placeholder_count_total": len(placeholders),
        "placeholder_count_reliable_era": len(placeholder_ts),
        "candidate_universe_size": len(candidates),
        "candidates_excluded": excluded_in_universe,
        "placeholders": placeholders,
        "excluded_timestamps": list(mask.excluded_timestamps),
        "mask_run_hash": mask.run_hash,
        "no_interpolation": True,
        "raw_mutated": False,
    }
    payload["run_hash"] = hash_stable_json({k: v for k, v in payload.items() if k != "run_hash"})
    out = V4_ROOT / slug / "H4.non_trading_placeholder_mask.commodity_h4_non_trading_placeholder_mask_v1.json"
    digest = write_json_immutable(out, payload)
    return {
        "artifact_path": str(out.relative_to(REPO)).replace("\\", "/"),
        "artifact_hash": digest,
        "placeholder_count_total": len(placeholders),
        "placeholder_count_reliable_era": len(placeholder_ts),
        "candidates_excluded": excluded_in_universe,
    }


def build_natgas_provider_gap_mask() -> dict:
    slug = slug_symbol("Nat Gas")
    review = read_json(V4_ROOT / slug / f"H4.residual_gap_review.{REVIEW_SCHEMA_VERSION}.json")
    intervals = review.get("provider_gap_intervals", [])
    payload = {
        "schema": "commodity_h4_provider_gap_exclusion_v1",
        "canonical_symbol": "Nat Gas",
        "timeframe": "H4",
        "gate": review["gate"],
        "candidate_mask_status": review["candidate_mask_status"],
        "strategy_embargo_contract_requested": False,
        "note": (
            "CLEAR_WITH_GAP_EMBARGO. Candidate-mask generation stays blocked until "
            "a StrategyEmbargoContract is supplied once the specialist's feature and "
            "holding windows are frozen. Exact provider-gap timestamps recorded below."
        ),
        "provider_gap_intervals": intervals,
        "source_hashes": review["source_hashes"],
    }
    payload["run_hash"] = hash_stable_json({k: v for k, v in payload.items() if k != "run_hash"})
    out = V4_ROOT / slug / "H4.provider_gap_exclusion.commodity_h4_provider_gap_exclusion_v1.json"
    digest = write_json_immutable(out, payload)
    return {"artifact_path": str(out.relative_to(REPO)).replace("\\", "/"), "artifact_hash": digest}


def _authoritative_row(canonical_symbol: str, version: str) -> dict:
    slug = slug_symbol(canonical_symbol)
    root = V4_ROOT if version == "v4" else V3_ROOT
    schema = "commodity_h4_residual_gap_review_v4" if version == "v4" else "commodity_h4_residual_gap_review_v3"
    review = read_json(root / slug / f"H4.residual_gap_review.{schema}.json")
    hashes = resolve_raw_evidence_hashes(RAW_ROOT, slug, "H4")
    return {
        "canonical_symbol": canonical_symbol,
        "authoritative_version": version,
        "gate": review["gate"],
        "classification_counts": review["classification_counts"],
        "qualifying": review["gate"] in CLEAR,
        "raw_content_hash": hashes["merged_bar_content_hash"],
        "raw_content_unchanged_vs_artifact": hashes["merged_bar_content_hash"]
        == review["source_hashes"]["raw_content_hash"],
        "artifact": f"logs/commodity_data_audit/forensics/consolidated_{version}/{slug}/"
        f"H4.residual_gap_review.{schema}.json",
    }


def build_authoritative_table(onboard_results, wti_mask, ng_mask) -> dict:
    rows = [_authoritative_row(sym, ver) for sym, ver in ALL_CLUSTERS]
    qualifying = [r["canonical_symbol"] for r in rows if r["qualifying"]]
    blocked = [
        {"canonical_symbol": r["canonical_symbol"], "gate": r["gate"]}
        for r in rows
        if not r["qualifying"]
    ]
    base_metal_status = [
        {
            "canonical_symbol": metal,
            "raw_data_present": (RAW_ROOT / slug_symbol(metal) / "H4").exists(),
            "status": "NOT_ACQUIRED_MT5_REQUIRED",
        }
        for metal in BASE_METAL_ORDER
    ]
    table = {
        "schema": "commodity_h4_authoritative_gate_table_v1",
        "generated_from": "frozen artifacts only; no MT5; no raw mutation",
        "clusters": rows,
        "qualifying_clusters": qualifying,
        "qualifying_count": len(qualifying),
        "blocked_clusters": blocked,
        "exclusion_masks": {
            "wti_non_trading_placeholder": wti_mask["artifact_path"],
            "natgas_provider_gap": ng_mask["artifact_path"],
        },
        "base_metal_replacement": {
            "predeclared_order": BASE_METAL_ORDER,
            "status": "BLOCKED_ACQUISITION_UNAVAILABLE",
            "reason": (
                "Registry source is MT5 (registry.py primary_source=MT5); no base-metal "
                "raw exists in the immutable store and MetaTrader5 is unavailable in this "
                "environment. No existing immutable acquisition to reuse."
            ),
            "candidates": base_metal_status,
        },
        "all_raw_unchanged": all(r["raw_content_unchanged_vs_artifact"] for r in rows),
        "success_condition_eight_qualifying": len(qualifying) >= 8,
    }
    table["run_hash"] = hash_stable_json({k: v for k, v in table.items() if k != "run_hash"})
    out = V4_ROOT / "commodity_authoritative_gate_table_v1.json"
    digest = write_json_immutable(out, table)
    table["_artifact_hash"] = digest
    table["_artifact_path"] = str(out.relative_to(REPO)).replace("\\", "/")
    return table


def main() -> int:
    onboard = [onboard_metal("XAU/USD"), onboard_metal("XAG/USD")]
    for row in onboard:
        print(f"{row['canonical_symbol']}: gate={row['gate']} counts={row['classification_counts']} "
              f"raw_unchanged={row['raw_content_unchanged']}")
    wti_mask = build_wti_placeholder_mask()
    print(f"WTI placeholder mask: total={wti_mask['placeholder_count_total']} "
          f"reliable_era={wti_mask['placeholder_count_reliable_era']} "
          f"candidates_excluded={wti_mask['candidates_excluded']}")
    ng_mask = build_natgas_provider_gap_mask()
    print(f"Nat Gas provider-gap mask: {ng_mask['artifact_path']}")
    table = build_authoritative_table(onboard, wti_mask, ng_mask)
    print("\n=== AUTHORITATIVE GATE TABLE ===")
    for r in table["clusters"]:
        flag = "QUALIFY" if r["qualifying"] else "       "
        print(f"  [{flag}] {r['canonical_symbol']:<10} {r['gate']:<28} ({r['authoritative_version']}) "
              f"raw_ok={r['raw_content_unchanged_vs_artifact']}")
    print(f"\n  qualifying_count = {table['qualifying_count']} / 8")
    print(f"  all_raw_unchanged = {table['all_raw_unchanged']}")
    print(f"  success(>=8) = {table['success_condition_eight_qualifying']}")
    print(f"  base-metal replacement = {table['base_metal_replacement']['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
