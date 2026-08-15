"""Ingest MT5 OHLCV from Athena's backtest candle cache into PTIS."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from athena_ase.data.availability import AvailabilityRuleId
from athena_ase.data.ingest.common import (
    append_ptis_rows,
    bar_close_ms,
    parse_iso_to_ms,
    price_series_id,
    row_from_rule,
)
from athena_ase.data.ptis import PTISStore
from athena_ase.instruments import instrument_by_symbol
from runtime_paths import resolve_backtest_candle_cache_db_path

log = logging.getLogger("ase.ingest.mt5")

_FIELDS = ("close", "open", "high", "low", "volume")
_TIMEFRAMES = ("H1", "H4", "D1")


def _db_path(override: str | None = None) -> str:
    return override or str(resolve_backtest_candle_cache_db_path())


def list_cached_pairs(*, db_path: str | None = None) -> list[dict[str, str]]:
    try:
        with sqlite3.connect(_db_path(db_path), timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT DISTINCT display, symbol, source, asset_type, provider
                FROM backtest_candles
                WHERE provider = 'mt5'
                ORDER BY symbol
                """
            ).fetchall()
    except sqlite3.Error as exc:
        log.warning("backtest candle cache unavailable: %s", exc)
        return []
    return [dict(row) for row in rows]


def _canonical_symbol(pair: dict[str, Any]) -> str | None:
    for value in (pair.get("display"), pair.get("symbol")):
        inst = instrument_by_symbol(str(value or ""))
        if inst is not None:
            return inst.symbol
    return None


def _read_bars(
    pair: dict[str, Any],
    tf: str,
    *,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    path = _db_path(db_path)
    with sqlite3.connect(path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT bar_time, open, high, low, close, volume
            FROM backtest_candles
            WHERE display IS ? AND symbol IS ? AND source IS ?
              AND asset_type IS ? AND timeframe = ? AND provider = 'mt5'
            ORDER BY bar_time
            """,
            (
                pair.get("display"),
                pair.get("symbol"),
                pair.get("source"),
                pair.get("asset_type"),
                tf.upper(),
            ),
        ).fetchall()
    return [dict(row) for row in rows]


def ingest_pair_tf(
    store: PTISStore,
    pair: dict[str, Any],
    tf: str,
    *,
    db_path: str | None = None,
) -> dict[str, int]:
    symbol = _canonical_symbol(pair)
    if not symbol:
        return {}
    bars = _read_bars(pair, tf, db_path=db_path)
    if not bars:
        return {}

    field_rows: dict[str, list[dict]] = {field: [] for field in _FIELDS}
    for bar in bars:
        open_ms = parse_iso_to_ms(bar.get("bar_time"))
        if open_ms is None:
            continue
        # Never write 0.0 prices: a missing close would ingest as log(1e-12)
        # downstream and poison vol/signals.
        close_raw = bar.get("close")
        if not close_raw:
            continue
        close_ms = bar_close_ms(open_ms, tf)
        for field in _FIELDS:
            raw = bar.get(field)
            if raw is None:
                raw = 0 if field == "volume" else close_raw
            value = float(raw)
            sid = price_series_id("MT5", symbol, tf, field)
            field_rows[field].append(
                row_from_rule(
                    sid,
                    AvailabilityRuleId.MT5_BAR,
                    value_time_ms=close_ms,
                    value=value,
                    bar_close_ms=close_ms,
                )
            )

    counts: dict[str, int] = {}
    for field, rows in field_rows.items():
        sid = price_series_id("MT5", symbol, tf, field)
        counts[sid] = append_ptis_rows(store, sid, "MT5", rows)
    return counts


def ingest_all(
    store: PTISStore,
    *,
    db_path: str | None = None,
    timeframes: tuple[str, ...] = _TIMEFRAMES,
) -> dict[str, int]:
    totals: dict[str, int] = {}
    for pair in list_cached_pairs(db_path=db_path):
        for tf in timeframes:
            try:
                totals.update(ingest_pair_tf(store, pair, tf, db_path=db_path))
            except Exception as exc:
                log.warning("MT5 ingest failed %s %s: %s", pair.get("display"), tf, exc)
    log.info("MT5 ingest complete: %d series touched", len(totals))
    return totals
