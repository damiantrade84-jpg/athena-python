"""Ingest FRED rate series from carry_cache.db into PTIS."""

from __future__ import annotations

import logging
import os
import re
import sqlite3

from athena_ase.data.availability import AvailabilityRuleId
from athena_ase.data.ingest.common import (
    append_ptis_rows,
    date_iso_to_ms,
    deadline_reached,
    fred_series_id,
    row_from_rule,
    series_latest_value_time,
)
from athena_ase.data.ptis import PTISStore

log = logging.getLogger("ase.ingest.fred")


def _resolve_carry_db(path: str | None) -> str:
    if path:
        return path
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(repo_root, "carry_cache.db")


def list_fred_series(*, db_path: str | None = None) -> list[str]:
    path = _resolve_carry_db(db_path)
    if not os.path.exists(path):
        return []
    with sqlite3.connect(path, timeout=15.0) as con:
        rows = con.execute(
            "SELECT DISTINCT series_id FROM rate_series ORDER BY series_id"
        ).fetchall()
    return [r[0] for r in rows]


# OECD MEI monthly series (interbank IRSTCI01*, long-term IRLTLT01*) publish
# ~4-8 weeks after the observation month — must NOT get the daily obs+1BD rule
# (WO T0.5).
_OECD_MONTHLY_RE = re.compile(r"^IR(STCI|LTLT)01[A-Z]{2}M156N$")


def _rule_for_fred_series(fred_id: str) -> AvailabilityRuleId:
    fid = fred_id.upper()
    if _OECD_MONTHLY_RE.match(fid):
        return AvailabilityRuleId.FRED_MONTHLY_OECD
    # DFF included: it is the series carry.py actually uses for USD.
    policy = {"FEDFUNDS", "SOFR", "EFFR", "DFF"}
    if fid in policy:
        return AvailabilityRuleId.FRED_POLICY
    return AvailabilityRuleId.FRED_DAILY


def _latest_obs_ms(path: str, fred_id: str) -> int | None:
    with sqlite3.connect(path, timeout=15.0) as con:
        row = con.execute(
            "SELECT MAX(obs_date) FROM rate_series WHERE series_id=?",
            (fred_id,),
        ).fetchone()
    if not row or not row[0]:
        return None
    return date_iso_to_ms(str(row[0]))


def ingest_series(store: PTISStore, fred_id: str, *, db_path: str | None = None) -> int:
    path = _resolve_carry_db(db_path)
    sid = fred_series_id(fred_id)
    rule = _rule_for_fred_series(fred_id)
    latest_ms = _latest_obs_ms(path, fred_id)
    if latest_ms is not None:
        last_vt = series_latest_value_time(store, sid)
        if last_vt is not None and last_vt >= latest_ms:
            return 0
    rows: list[dict] = []
    with sqlite3.connect(path, timeout=15.0) as con:
        cur = con.execute(
            "SELECT obs_date, rate FROM rate_series WHERE series_id=? ORDER BY obs_date",
            (fred_id,),
        )
        for obs_date, rate in cur:
            value_ms = date_iso_to_ms(str(obs_date))
            rows.append(
                row_from_rule(
                    sid,
                    rule,
                    value_time_ms=value_ms,
                    value=float(rate),
                )
            )
    if rule == AvailabilityRuleId.FRED_POLICY:
        source = "FRED_POLICY"
    elif rule == AvailabilityRuleId.FRED_MONTHLY_OECD:
        source = "FRED_MONTHLY_OECD"
    else:
        source = "FRED"
    return append_ptis_rows(store, sid, source, rows)


def ingest_all(
    store: PTISStore,
    *,
    db_path: str | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, int]:
    totals: dict[str, int] = {}
    for fred_id in list_fred_series(db_path=db_path):
        if deadline_reached(deadline_monotonic):
            log.warning("FRED ingest stopped at deadline")
            break
        try:
            sid = fred_series_id(fred_id)
            totals[sid] = ingest_series(store, fred_id, db_path=db_path)
        except Exception as exc:
            log.warning("FRED ingest failed %s: %s", fred_id, exc)
    log.info("FRED ingest complete: %d series", len(totals))
    return totals
