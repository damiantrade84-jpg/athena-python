"""Write holdout manifest (ASE v2.1 §9)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
HOLDOUT_MANIFEST = OUTPUT_DIR / "holdout_manifest.json"


def write_holdout_manifest(
    holdout_df: pd.DataFrame,
    family: str,
    horizon: str,
    *,
    path: Path | None = None,
) -> Path:
    out = path or HOLDOUT_MANIFEST
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "family": family,
        "horizon": horizon,
        "n_events": int(len(holdout_df)),
        "decision_time_min": int(holdout_df["decision_time_ms"].min()) if len(holdout_df) else 0,
        "decision_time_max": int(holdout_df["decision_time_ms"].max()) if len(holdout_df) else 0,
        "instruments": sorted(holdout_df["instrument"].astype(str).unique().tolist()) if len(holdout_df) else [],
    }
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return out
