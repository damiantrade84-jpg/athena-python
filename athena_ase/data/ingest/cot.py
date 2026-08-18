"""Ingest CFTC COT positioning from cot_cache.db into PTIS."""

from __future__ import annotations

import logging
import os
import sqlite3

from athena_ase.data.availability import AvailabilityRuleId
from athena_ase.data.ingest.common import (
    append_ptis_rows,
    cot_series_id,
    date_iso_to_ms,
    deadline_reached,
    row_from_rule,
    series_latest_value_time,
)
from athena_ase.data.ptis import PTISStore

log = logging.getLogger("ase.ingest.cot")


def _resolve_cot_db(path: str | None) -> str:
    if path:
        return path
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(repo_root, "cot_cache.db")


def list_cot_assets(*, db_path: str | None = None) -> list[str]:
    path = _resolve_cot_db(db_path)
    if not os.path.exists(path):
        return []
    with sqlite3.connect(path, timeout=15.0) as con:
        rows = con.execute("SELECT DISTINCT asset FROM cot_net ORDER BY asset").fetchall()
    return [r[0] for r in rows]


def _latest_report_ms(path: str, asset: str) -> int | None:
    with sqlite3.connect(path, timeout=15.0) as con:
        row = con.execute(
            "SELECT MAX(report_date) FROM cot_net WHERE asset=?",
            (asset,),
        ).fetchone()
    if not row or not row[0]:
        return None
    return date_iso_to_ms(str(row[0]))


def ingest_asset(store: PTISStore, asset: str, *, db_path: str | None = None) -> int:
    path = _resolve_cot_db(db_path)
    sid = cot_series_id(asset)
    latest_ms = _latest_report_ms(path, asset)
    if latest_ms is not None:
        last_vt = series_latest_value_time(store, sid)
        if last_vt is not None and last_vt >= latest_ms:
            return 0
    rows: list[dict] = []
    with sqlite3.connect(path, timeout=15.0) as con:
        cur = con.execute(
            "SELECT report_date, net_long FROM cot_net WHERE asset=? ORDER BY report_date",
            (asset,),
        )
        for report_date, net_long in cur:
            value_ms = date_iso_to_ms(str(report_date))
            rows.append(
                row_from_rule(
                    sid,
                    AvailabilityRuleId.CFTC_COT,
                    value_time_ms=value_ms,
                    value=float(net_long),
                )
            )
    return append_ptis_rows(store, sid, "CFTC", rows)


def ingest_all(
    store: PTISStore,
    *,
    db_path: str | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, int]:
    totals: dict[str, int] = {}
    for asset in list_cot_assets(db_path=db_path):
        if deadline_reached(deadline_monotonic):
            log.warning("COT ingest stopped at deadline")
            break
        try:
            sid = cot_series_id(asset)
            totals[sid] = ingest_asset(store, asset, db_path=db_path)
        except Exception as exc:
            log.warning("COT ingest failed %s: %s", asset, exc)
    log.info("COT ingest complete: %d series", len(totals))
    return totals
