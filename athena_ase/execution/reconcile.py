"""Reconcile ASE trade journal realized R from PTIS bars."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from athena_ase.execution.journal import load_trade_journal, trade_journal_path


def reconcile_trade_outcomes(
    *,
    path: Path | None = None,
    outcome_lookup: dict[tuple[str, int], dict[str, Any]] | None = None,
) -> int:
    """Fill realized outcome columns when lookup provided.

    Keys: (instrument, decisionTimeMs) → {realizedNetR, realizedWin}.
    """
    out_path = path or trade_journal_path()
    if not out_path.exists() or not outcome_lookup:
        return 0
    df = load_trade_journal(out_path)
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
