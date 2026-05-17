#!/usr/bin/env python3
"""Rebuild funnel JSONL + summary from a persisted full-scan JSON (diagnostics-only).

Example:
    python tools/summarize_engine_b_gate_funnel.py --input logs/engine_b_gate_funnel/latest_full_scan.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Repo root on sys.path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from athena_app.diagnostics.engine_b_gate_funnel_persist import (  # noqa: E402
    regenerate_funnel_artifacts_from_scan_file,
    resolve_funnel_output_directory,
)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Regenerate latest_funnel_rows.jsonl + latest_funnel_summary.json from a scan JSON."
    )
    ap.add_argument(
        "--input",
        "-i",
        required=True,
        help="Path to full_scan JSON (e.g. logs/engine_b_gate_funnel/latest_full_scan.json)",
    )
    ap.add_argument(
        "--output-dir",
        "-o",
        default=None,
        help="Directory for funnel artifacts (defaults to ENGINE_B_SCAN_GATE_FUNNEL_OUTPUT_DIR)",
    )
    args = ap.parse_args()
    out = resolve_funnel_output_directory(args.output_dir)
    rp, sp = regenerate_funnel_artifacts_from_scan_file(args.input, output_dir=out)
    print(f"wrote_rows={rp}")
    print(f"wrote_summary={sp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
