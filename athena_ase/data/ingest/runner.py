"""Orchestrate PTIS ingestion from Athena legacy caches and feeds."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from athena_ase.universe import Instrument

from athena_ase.data.ingest import audit as audit_mod
from athena_ase.data.ingest import binance, bybit, cot, dukascopy, eodhd, fred, mt5, mt5_live
from athena_ase.data.ingest.common import deadline_reached
from athena_ase.data.ptis import PTISStore, default_ptis_root

log = logging.getLogger("ase.ingest")

ALL_SOURCES = ("eodhd", "mt5", "mt5_live", "binance", "dukascopy", "bybit", "cot", "fred")


def run_ingest(
    store: PTISStore | None = None,
    *,
    sources: Iterable[str] = ALL_SOURCES,
    ptis_root: Path | str | None = None,
    backtest_db: str | None = None,
    duka_db: str | None = None,
    cot_db: str | None = None,
    carry_db: str | None = None,
    bybit_symbols: list[str] | None = None,
    mt5_live_instruments: tuple[Instrument, ...] | None = None,
    bybit_lookback_days: int = 730,
    write_audit: bool = True,
    audit_path: Path | str | None = None,
    mt5_live_topup: bool = False,
    deadline_monotonic: float | None = None,
) -> dict[str, dict[str, int]]:
    ptis = store or PTISStore(ptis_root or default_ptis_root())
    selected = {s.strip().lower() for s in sources}
    results: dict[str, dict[str, int]] = {}

    # Per-source isolation: one crashing feed must not starve the rest of the
    # pipeline (a failing first source previously aborted the whole run).
    def _run_source(name: str, fn) -> None:
        if deadline_reached(deadline_monotonic):
            log.warning("ASE ingest skipped %s (deadline)", name)
            results[name] = {"skipped": "deadline"}  # type: ignore[dict-item]
            return
        try:
            results[name] = fn()
        except Exception as exc:
            log.warning("ASE ingest source %s failed: %s", name, exc)
            results[name] = {"error": str(exc)}  # type: ignore[dict-item]

    if "eodhd" in selected:
        _run_source("eodhd", lambda: eodhd.ingest_all(ptis, db_path=backtest_db))
    if "mt5" in selected:
        _run_source("mt5", lambda: mt5.ingest_all(ptis, db_path=backtest_db))
    if "mt5_live" in selected:
        _run_source(
            "mt5_live",
            lambda: mt5_live.ingest_all(
                ptis,
                instruments=mt5_live_instruments,
                topup=mt5_live_topup,
                deadline_monotonic=deadline_monotonic,
            ),
        )
    if "binance" in selected:
        _run_source("binance", lambda: binance.ingest_all(ptis, db_path=backtest_db))
    if "dukascopy" in selected:
        _run_source("dukascopy", lambda: dukascopy.ingest_all(ptis, db_path=duka_db))
    if "bybit" in selected:
        _run_source(
            "bybit",
            lambda: bybit.ingest_all(
                ptis,
                symbols=bybit_symbols,
                lookback_days=bybit_lookback_days,
                deadline_monotonic=deadline_monotonic,
            ),
        )
    if "cot" in selected:
        _run_source(
            "cot",
            lambda: cot.ingest_all(
                ptis, db_path=cot_db, deadline_monotonic=deadline_monotonic
            ),
        )
    if "fred" in selected:
        _run_source(
            "fred",
            lambda: fred.ingest_all(
                ptis, db_path=carry_db, deadline_monotonic=deadline_monotonic
            ),
        )

    if write_audit and not deadline_reached(deadline_monotonic):
        path = audit_path or (
            Path(__file__).resolve().parents[3] / "reports" / "availability_audit.md"
        )
        audit_mod.write_availability_audit(ptis, path)
        log.info("availability audit written: %s", path)

    return results
