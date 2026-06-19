"""Read-only XAG store conflict forensic — does not modify existing XAG artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from athena_research.commodity_data_audit.freeze_quality import exclude_forming_bar
from athena_research.commodity_data_audit.freeze_store import (
    hash_file_bytes,
    hash_stable_json,
    read_json,
    repo_root,
    resolve_path,
)
from athena_research.commodity_data_audit.store_forensic import (
    compare_merged_bars,
    compare_report_to_dict,
    inventory_chunks,
    inventory_to_dict,
    rebuild_merged_from_chunk_dir,
)


def run_xag_store_conflict_review(
    *,
    output_dir: Path | None = None,
    as_of: datetime | None = None,
) -> dict:
    raw_root = resolve_path("logs/commodity_data_audit/raw/v1/XAG_USD/H4")
    chunk_dir = raw_root / "chunks"
    merged_legacy = raw_root / "merged_raw.json"
    resume_state = raw_root / "resume_state.json"
    output_dir = output_dir or resolve_path("logs/commodity_data_audit/forensics/xag_h4_store_conflict")
    output_dir.mkdir(parents=True, exist_ok=True)

    chunk_inventory = inventory_chunks(chunk_dir)
    rebuilt = rebuild_merged_from_chunk_dir(chunk_dir)
    existing = read_json(merged_legacy) if merged_legacy.exists() else []
    compare = compare_merged_bars(existing, rebuilt)
    compare_dict = compare_report_to_dict(compare)
    if merged_legacy.exists():
        compare_dict["existing_file_hash"] = hash_file_bytes(merged_legacy)

    as_of_utc = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    confirmed = exclude_forming_bar(rebuilt, timeframe="H4", as_of=as_of_utc)
    would_immutable_conflict = (
        merged_legacy.exists()
        and hash_stable_json(confirmed) != hash_stable_json(existing)
    )

    normalized_glob = list(resolve_path("logs/commodity_data_audit/normalized").rglob("XAG_USD/*"))
    payload = {
        "tool_version": "xag_h4_store_conflict_forensic_v1",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "symbol": "XAG/USD",
        "timeframe": "H4",
        "inventory": {
            "chunk_count": len(chunk_inventory),
            "chunks": inventory_to_dict(chunk_inventory),
            "merged_legacy": {
                "path": str(merged_legacy.relative_to(repo_root())).replace("\\", "/"),
                "exists": merged_legacy.exists(),
                "sha256": hash_file_bytes(merged_legacy) if merged_legacy.exists() else None,
                "row_count": len(existing),
                "modified_at": datetime.fromtimestamp(merged_legacy.stat().st_mtime, tz=timezone.utc).isoformat()
                if merged_legacy.exists()
                else None,
            },
            "resume_state": {
                "path": str(resume_state.relative_to(repo_root())).replace("\\", "/"),
                "exists": resume_state.exists(),
                "sha256": hash_file_bytes(resume_state) if resume_state.exists() else None,
                "completed_chunks": len(read_json(resume_state).get("completed_chunks", [])) if resume_state.exists() else 0,
                "modified_at": datetime.fromtimestamp(resume_state.stat().st_mtime, tz=timezone.utc).isoformat()
                if resume_state.exists()
                else None,
            },
            "normalized_outputs": [
                {
                    "path": str(path.relative_to(repo_root())).replace("\\", "/"),
                    "sha256": hash_file_bytes(path),
                    "modified_at": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(),
                }
                for path in normalized_glob
            ],
        },
        "compare_merged_vs_chunk_rebuild": compare_dict,
        "forming_bar_analysis": {
            "as_of": as_of_utc.isoformat(),
            "chunk_merge_rows": len(rebuilt),
            "confirmed_rows": len(confirmed),
            "delta_rows": len(rebuilt) - len(confirmed),
            "chunk_merge_content_hash": hash_stable_json(rebuilt),
            "confirmed_content_hash": hash_stable_json(confirmed),
            "legacy_fixed_path_would_immutable_conflict": would_immutable_conflict,
        },
        "root_cause": {
            "classification": "forming_bar_as_of_dual_write_on_fixed_merged_raw_path",
            "details": [
                "fetch_chunks_with_resume wrote mutable merged_raw.json from chunk merge (all MT5 bars).",
                "freeze_one_series attempted write_json_immutable on the same path with confirmed bars (forming-bar excluded).",
                "Immutable overwrite guard correctly blocked manifest/normalized completion.",
                "Chunk merge and legacy merged_raw are currently identical; chunks are authoritative.",
            ],
            "ruled_out": [
                "forensic XAG probe persisting raw data",
                "changed chunk set",
                "cross-chunk timestamp conflicts",
                "duplicate timestamps",
                "partial/corrupt chunk merge",
                "nondeterministic serialization mismatch",
            ],
        },
        "trust": {
            "immutable_chunks": "trusted",
            "legacy_merged_raw": "trusted_as_chunk_cache_only",
            "normalized_manifests": "absent_incomplete_acquisition",
            "research_use": "load from chunks or chunk-identical legacy merged_raw; do not treat legacy path as confirmed-only raw",
        },
        "provenance_preservation": {
            "legacy_merged_raw_preserved": True,
            "do_not_delete_or_rename_without_record": True,
        },
    }

    out_path = output_dir / "xag_h4_store_conflict_review.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    payload["review_path"] = str(out_path.relative_to(repo_root())).replace("\\", "/")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Forensic review of XAG H4 store conflict (read-only).")
    parser.add_argument(
        "--output-dir",
        default="logs/commodity_data_audit/forensics/xag_h4_store_conflict",
    )
    args = parser.parse_args()
    result = run_xag_store_conflict_review(output_dir=resolve_path(args.output_dir))
    print(f"review={result['review_path']}")
    print(f"identical={result['compare_merged_vs_chunk_rebuild']['identical']}")
    print(f"would_conflict={result['forming_bar_analysis']['legacy_fixed_path_would_immutable_conflict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
