"""ASE trade journal — signals + execution outcomes (demo/paper track record)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from athena_ase.contracts import ASESignal
from athena_ase.data.parquet_io import read_parquet_safe, write_parquet_atomic
from athena_ase.paths import trade_journal_path

log = logging.getLogger("ase.execution.journal")


def _flatten_signal(sig: ASESignal) -> dict[str, Any]:
    row = sig.to_dict()
    for key in (
        "returnQ",
        "maeQ",
        "mfeQ",
        "holdQ",
        "primarySignals",
        "predictionDiagnostics",
        "dataQuality",
        "modelHealth",
        "entryZone",
        "fxContext",
        "triangular",
    ):
        if key in row and not isinstance(row[key], (str, int, float, bool, type(None))):
            row[key] = json.dumps(row[key], sort_keys=True, default=str)
    row["recordedAt"] = datetime.now(timezone.utc).isoformat()
    row["executionStatus"] = None
    row["executionDetail"] = None
    row["orderId"] = None
    row["realizedNetR"] = None
    row["realizedWin"] = None
    row["reconciledAt"] = None
    return row


def _read_journal_df(out_path: Path) -> pd.DataFrame:
    if not out_path.exists():
        return pd.DataFrame()
    return read_parquet_safe(out_path)


def append_trade_signals(
    signals: list[ASESignal],
    *,
    path: Path | None = None,
) -> Path:
    out_path = path or trade_journal_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [_flatten_signal(s) for s in signals]
    if not rows:
        return out_path
    df_new = pd.DataFrame(rows)
    df_old = _read_journal_df(out_path)
    if df_old.empty:
        df = df_new
    else:
        df = pd.concat([df_old, df_new], ignore_index=True)
    write_parquet_atomic(out_path, df)
    return out_path


def append_execution_outcome(
    *,
    instrument: str,
    decision_time_ms: int,
    status: str,
    detail: dict[str, Any] | None = None,
    order_id: str | None = None,
    path: Path | None = None,
) -> bool:
    out_path = path or trade_journal_path()
    if not out_path.exists():
        return False
    df = _read_journal_df(out_path)
    if df.empty:
        return False
    mask = (df["instrument"].astype(str) == str(instrument)) & (
        df["decisionTimeMs"].astype(int) == int(decision_time_ms)
    )
    if not mask.any():
        return False
    idx = int(df.index[mask][-1])
    df.at[idx, "executionStatus"] = status
    df.at[idx, "executionDetail"] = json.dumps(detail or {}, sort_keys=True, default=str)
    if order_id:
        df.at[idx, "orderId"] = order_id
    write_parquet_atomic(out_path, df)
    return True


def load_trade_journal(path: Path | None = None) -> pd.DataFrame:
    return _read_journal_df(path or trade_journal_path())


def trade_journal_summary(path: Path | None = None) -> dict[str, Any]:
    df = load_trade_journal(path)
    if df.empty:
        return {
            "totalRows": 0,
            "byStatus": {},
            "byFamily": {},
            "executionCounts": {},
            "realizedNetR": 0.0,
            "reconciled": 0,
            "journalPath": str(path or trade_journal_path()),
        }
    status_counts = (
        df["decisionStatus"].value_counts().to_dict() if "decisionStatus" in df.columns else {}
    )
    family_counts = (
        df["modelFamily"].value_counts().to_dict() if "modelFamily" in df.columns else {}
    )
    exec_counts = (
        df["executionStatus"].value_counts().to_dict()
        if "executionStatus" in df.columns
        else {}
    )
    realized = 0.0
    if "realizedNetR" in df.columns:
        vals = pd.to_numeric(df["realizedNetR"], errors="coerce").dropna()
        realized = float(vals.sum()) if len(vals) else 0.0
    reconciled = int(df["reconciledAt"].notna().sum()) if "reconciledAt" in df.columns else 0
    return {
        "totalRows": int(len(df)),
        "byStatus": {str(k): int(v) for k, v in status_counts.items()},
        "byFamily": {str(k): int(v) for k, v in family_counts.items()},
        "executionCounts": {str(k): int(v) for k, v in exec_counts.items()},
        "realizedNetR": realized,
        "reconciled": reconciled,
        "journalPath": str(path or trade_journal_path()),
    }
