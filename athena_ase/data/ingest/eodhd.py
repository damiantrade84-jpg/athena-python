"""Ingest EODHD OHLCV from the backtest candle cache into PTIS."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from athena_ase.data.availability import AvailabilityRuleId
from athena_ase.data.ingest.common import (
    append_ptis_rows,
    bar_close_ms,
    eodhd_series_id,
    parse_iso_to_ms,
    row_from_rule,
)
from athena_ase.data.ptis import PTISStore
from athena_ase.instruments import instrument_by_symbol
from runtime_paths import resolve_backtest_candle_cache_db_path

log = logging.getLogger("ase.ingest.eodhd")

_EODHD_FIELDS = ("close", "open", "high", "low", "volume")
_EODHD_TFS = ("H1", "H4", "D1")


def _db_path(override: str | None = None) -> str:
    if override:
        return override
    return str(resolve_backtest_candle_cache_db_path())


def list_cached_pair_providers(
    *,
    db_path: str | None = None,
    provider_like: str = "eodhd%",
) -> list[dict[str, str]]:
    path = _db_path(db_path)
    try:
        with sqlite3.connect(path, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT DISTINCT display, symbol, source, asset_type, provider
                FROM backtest_candles
                WHERE provider LIKE ?
                ORDER BY provider, symbol
                """,
                (provider_like,),
            ).fetchall()
    except sqlite3.Error as exc:
        log.warning("backtest candle cache unavailable: %s", exc)
        return []
    return [dict(r) for r in rows]


def iter_backtest_bars(
    pair: dict[str, Any],
    tf: str,
    provider: str,
    *,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    import backtest_candle_cache as cache

    if db_path:
        import os

        prev = os.environ.get("ATHENA_BACKTEST_CANDLE_CACHE_DB_PATH")
        os.environ["ATHENA_BACKTEST_CANDLE_CACHE_DB_PATH"] = db_path
        try:
            return cache._read_rows(pair, tf.upper(), 10_000_000, provider)
        finally:
            if prev is None:
                os.environ.pop("ATHENA_BACKTEST_CANDLE_CACHE_DB_PATH", None)
            else:
                os.environ["ATHENA_BACKTEST_CANDLE_CACHE_DB_PATH"] = prev
    return cache._read_rows(pair, tf.upper(), 10_000_000, provider)


def ingest_pair_tf(
    store: PTISStore,
    pair: dict[str, Any],
    tf: str,
    provider: str,
    *,
    db_path: str | None = None,
    fields: tuple[str, ...] = _EODHD_FIELDS,
) -> dict[str, int]:
    raw_symbol = str(pair.get("symbol") or pair.get("display") or "")
    # Canonicalize cache symbols (EURUSD.FOREX, AAPL.US) to ASE catalog ids so
    # the written series match the ids the signal/feature paths resolve. Keep
    # the raw id when unmapped (e.g. benchmark series such as USDX that are
    # read under their bare name).
    inst = instrument_by_symbol(raw_symbol) or instrument_by_symbol(
        str(pair.get("display") or "")
    )
    symbol = inst.symbol if inst is not None else raw_symbol
    bars = iter_backtest_bars(pair, tf, provider, db_path=db_path)
    counts: dict[str, int] = {}
    if not bars:
        return counts

    field_rows: dict[str, list[dict]] = {f: [] for f in fields}
    for bar in bars:
        open_ms = parse_iso_to_ms(bar.get("time") or bar.get("datetime"))
        if open_ms is None:
            continue
        close_ms = bar_close_ms(open_ms, tf)
        for field in fields:
            if field == "volume":
                val = float(bar.get("vol", bar.get("volume", 0)) or 0)
            else:
                price = bar.get(field)
                if price is None and field != "close":
                    price = bar.get("close")
                if price is None:
                    continue
                val = float(price)
            sid = eodhd_series_id(str(symbol), tf, field)
            field_rows[field].append(
                row_from_rule(
                    sid,
                    AvailabilityRuleId.EODHD_BAR,
                    value_time_ms=close_ms,
                    value=val,
                    bar_close_ms=close_ms,
                )
            )

    for field, rows in field_rows.items():
        sid = eodhd_series_id(str(symbol), tf, field)
        counts[sid] = append_ptis_rows(store, sid, "EODHD", rows)
    return counts


def ingest_all(
    store: PTISStore,
    *,
    db_path: str | None = None,
    provider_like: str = "eodhd%",
    timeframes: tuple[str, ...] = _EODHD_TFS,
) -> dict[str, int]:
    totals: dict[str, int] = {}
    pairs = list_cached_pair_providers(db_path=db_path, provider_like=provider_like)
    for meta in pairs:
        pair = {
            "display": meta.get("display") or meta.get("symbol"),
            "symbol": meta.get("symbol") or meta.get("display"),
            "source": meta.get("source"),
            "type": meta.get("asset_type"),
        }
        provider = meta.get("provider") or "eodhd_intraday"
        for tf in timeframes:
            try:
                written = ingest_pair_tf(
                    store, pair, tf, provider, db_path=db_path
                )
                totals.update(written)
            except Exception as exc:
                log.warning(
                    "EODHD ingest failed %s %s %s: %s",
                    pair.get("symbol"),
                    tf,
                    provider,
                    exc,
                )
    log.info("EODHD ingest complete: %d series touched", len(totals))
    return totals
