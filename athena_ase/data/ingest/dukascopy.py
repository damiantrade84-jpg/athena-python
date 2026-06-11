"""Ingest Dukascopy H1 tick volume from duka_volume.db into PTIS."""

from __future__ import annotations

import logging
import os
import sqlite3

from athena_ase.data.availability import AvailabilityRuleId
from athena_ase.data.ingest.common import (
    append_ptis_rows,
    bar_close_ms,
    duka_series_id,
    row_from_rule,
)
from athena_ase.data.ptis import PTISStore

log = logging.getLogger("ase.ingest.dukascopy")

_DEFAULT_DUKA_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "duka_volume.db")


def _resolve_duka_db(path: str | None) -> str:
    if path:
        return path
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(repo_root, "duka_volume.db")


def list_duka_symbols(*, db_path: str | None = None, tf: str = "H1") -> list[str]:
    path = _resolve_duka_db(db_path)
    if not os.path.exists(path):
        return []
    with sqlite3.connect(path, timeout=15.0) as con:
        rows = con.execute(
            "SELECT DISTINCT symbol FROM forex_volume WHERE tf=? ORDER BY symbol",
            (tf.upper(),),
        ).fetchall()
    return [r[0] for r in rows]


def ingest_symbol(
    store: PTISStore,
    duka_symbol: str,
    *,
    tf: str = "H1",
    db_path: str | None = None,
) -> int:
    path = _resolve_duka_db(db_path)
    sid = duka_series_id(duka_symbol, tf)
    rows: list[dict] = []
    with sqlite3.connect(path, timeout=15.0) as con:
        cur = con.execute(
            "SELECT bar_ts, volume FROM forex_volume WHERE symbol=? AND tf=? ORDER BY bar_ts",
            (duka_symbol, tf.upper()),
        )
        for bar_ts, volume in cur:
            open_ms = int(bar_ts) * 1000
            close_ms = bar_close_ms(open_ms, tf)
            rows.append(
                row_from_rule(
                    sid,
                    AvailabilityRuleId.DUKASCOPY_VOLUME,
                    value_time_ms=close_ms,
                    value=float(volume),
                    bar_close_ms=close_ms,
                )
            )
    return append_ptis_rows(store, sid, "DUKASCOPY", rows)


def ingest_all(
    store: PTISStore,
    *,
    db_path: str | None = None,
    tf: str = "H1",
) -> dict[str, int]:
    totals: dict[str, int] = {}
    for sym in list_duka_symbols(db_path=db_path, tf=tf):
        try:
            sid = duka_series_id(sym, tf)
            totals[sid] = ingest_symbol(store, sym, tf=tf, db_path=db_path)
        except Exception as exc:
            log.warning("Dukascopy ingest failed %s: %s", sym, exc)
    log.info("Dukascopy ingest complete: %d series", len(totals))
    return totals
