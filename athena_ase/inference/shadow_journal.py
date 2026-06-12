"""ASE shadow journal (v2.1 §15)."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd


def default_shadow_journal_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "Athena" / "ase" / "ase_shadow_journal.parquet"


def append_shadow_rows(rows: list[dict], path: Path | None = None) -> Path:
    p = path or default_shadow_journal_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    df_new = pd.DataFrame(rows)
    if p.exists():
        df_old = pd.read_parquet(p)
        df = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df = df_new
    df.to_parquet(p, index=False)
    return p


def read_shadow_journal(path: Path | None = None) -> pd.DataFrame:
    p = path or default_shadow_journal_path()
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)
