"""Shadow journal parquet writer + reconciliation stub."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from athena_ase.contracts import ASESignal
from athena_ase.paths import shadow_journal_path

log = logging.getLogger("ase.shadow.journal")


def _flatten_signal(sig: ASESignal) -> dict[str, Any]:
    row = sig.to_dict()
    for key in ("returnQ", "maeQ", "mfeQ", "holdQ", "primarySignals", "predictionDiagnostics", "dataQuality", "modelHealth", "entryZone"):
        if key in row and not isinstance(row[key], (str, int, float, bool, type(None))):
            row[key] = json.dumps(row[key], sort_keys=True, default=str)
    row["recordedAt"] = datetime.now(timezone.utc).isoformat()
    row["realizedNetR"] = None
    row["realizedWin"] = None
    row["reconciledAt"] = None
    return row


def append_shadow_signals(signals: list[ASESignal], *, path: Path | None = None) -> Path:
    """Append ASE signals to shadow journal parquet."""
    out_path = path or shadow_journal_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [_flatten_signal(s) for s in signals if s.decisionStatus != "ERROR"]
    if not rows:
        return out_path
    df_new = pd.DataFrame(rows)
    if out_path.exists():
        try:
            df_old = pd.read_parquet(out_path)
            df = pd.concat([df_old, df_new], ignore_index=True)
        except Exception as exc:
            log.warning("Shadow journal read failed (%s) — overwriting", exc)
            df = df_new
    else:
        df = df_new
    df.to_parquet(out_path, index=False)
    return out_path


def load_shadow_journal(path: Path | None = None) -> pd.DataFrame:
    out_path = path or shadow_journal_path()
    if not out_path.exists():
        return pd.DataFrame()
    return pd.read_parquet(out_path)


def reconcile_shadow_outcomes(
    *,
    path: Path | None = None,
    outcome_lookup: dict[tuple[str, int], dict[str, Any]] | None = None,
) -> int:
    """Stub: fill realized outcome columns when lookup provided.

    Keys: (instrument, decisionTimeMs) → {realizedNetR, realizedWin}.
    Returns count of rows updated.
    """
    out_path = path or shadow_journal_path()
    if not out_path.exists() or not outcome_lookup:
        return 0
    df = pd.read_parquet(out_path)
    if df.empty:
        return 0
    updated = 0
    for idx, row in df.iterrows():
        if row.get("reconciledAt") is not None:
            continue
        key = (str(row.get("instrument") or ""), int(row.get("decisionTimeMs") or 0))
        hit = outcome_lookup.get(key)
        if not hit:
            continue
        df.at[idx, "realizedNetR"] = hit.get("realizedNetR")
        df.at[idx, "realizedWin"] = hit.get("realizedWin")
        df.at[idx, "reconciledAt"] = datetime.now(timezone.utc).isoformat()
        updated += 1
    if updated:
        df.to_parquet(out_path, index=False)
    return updated


def shadow_summary(path: Path | None = None) -> dict[str, Any]:
    df = load_shadow_journal(path)
    if df.empty:
        return {
            "totalRows": 0,
            "byStatus": {},
            "byFamily": {},
            "reconciled": 0,
            "journalPath": str(path or shadow_journal_path()),
        }
    status_counts = df["decisionStatus"].value_counts().to_dict() if "decisionStatus" in df.columns else {}
    family_counts = df["modelFamily"].value_counts().to_dict() if "modelFamily" in df.columns else {}
    reconciled = int(df["reconciledAt"].notna().sum()) if "reconciledAt" in df.columns else 0
    return {
        "totalRows": int(len(df)),
        "byStatus": {str(k): int(v) for k, v in status_counts.items()},
        "byFamily": {str(k): int(v) for k, v in family_counts.items()},
        "reconciled": reconciled,
        "journalPath": str(path or shadow_journal_path()),
    }
