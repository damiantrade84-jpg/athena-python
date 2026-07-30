"""One-time + incremental ETL: migrate the legacy SQLite backtest candle cache
into the parquet store, and top up each series to the maximum history each
provider allows.

This module does not reimplement any fetcher. It imports the existing
fetch functions lazily (only when --refresh top-up is requested) so that
plain migration never needs a live broker/API connection.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3

import backtest_candle_cache as bcc
from athena_backtest.data.store import ParquetCandleStore
from runtime_paths import resolve_backtest_candle_cache_db_path

log = logging.getLogger("sentinel")


def _is_current_scheme_row(key_row) -> bool:
    """Keep only bars stored under the exact cache_key a live read would produce today.

    The legacy cache accumulated bars under multiple superseded key schemes as the
    cache-key format evolved (an un-versioned scheme, and a versioned scheme with a
    corrupted `source` field like "binance:bybit_linear_kline"). Those are dead data:
    no current code path can read them back via `backtest_candle_cache._cache_key`,
    so migrating them would silently merge unrelated/stale series together.
    """
    source = str(key_row["source"] or "")
    symbol = str(key_row["symbol"] or "")
    if ":" in source or ":" in symbol:
        return False
    expected = bcc._cache_key({"source": source, "symbol": symbol}, key_row["provider"])
    return key_row["cache_key"] == expected


def _sqlite_series(db_path: str):
    """Yield (cache_key, provider, timeframe, rows) grouped by series from the legacy cache.

    Only series matching the current cache-key scheme are yielded (see
    `_is_current_scheme_row`); superseded/corrupted-format rows are skipped.
    """
    with sqlite3.connect(db_path, timeout=15.0) as con:
        con.row_factory = sqlite3.Row
        keys = con.execute(
            "SELECT DISTINCT cache_key, provider, timeframe, display, symbol, source, asset_type "
            "FROM backtest_candles"
        ).fetchall()
        for key_row in keys:
            if not _is_current_scheme_row(key_row):
                continue
            rows = con.execute(
                """
                SELECT bar_time, open, high, low, close, volume
                FROM backtest_candles
                WHERE cache_key=? AND provider=? AND timeframe=?
                ORDER BY bar_time ASC
                """,
                (key_row["cache_key"], key_row["provider"], key_row["timeframe"]),
            ).fetchall()
            yield key_row, rows


def migrate_sqlite_to_parquet(*, store: ParquetCandleStore | None = None) -> dict:
    """Migrate every series in the legacy backtest_candle_cache.db into the parquet store.

    Returns a summary dict: {series_migrated, bars_written, skipped}.
    """
    store = store or ParquetCandleStore()
    db_path = str(resolve_backtest_candle_cache_db_path())
    summary = {"series_migrated": 0, "bars_written": 0, "skipped": 0}
    try:
        con = sqlite3.connect(db_path, timeout=15.0)
        con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='backtest_candles'"
        ).fetchone()
        con.close()
    except sqlite3.Error as exc:
        log.warning("[BT-ETL] cannot open legacy cache at %s: %s", db_path, exc)
        return summary

    for key_row, rows in _sqlite_series(db_path):
        if not rows:
            summary["skipped"] += 1
            continue
        pair = {
            "display": key_row["display"],
            "symbol": key_row["symbol"],
            "source": key_row["source"],
            "type": key_row["asset_type"],
        }
        provider = key_row["provider"]
        tf = key_row["timeframe"]
        candles = [
            {
                "time": r["bar_time"],
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "vol": r["volume"] or 0,
            }
            for r in rows
        ]
        written = store.write(pair, tf, candles, provider=provider)
        summary["series_migrated"] += 1
        summary["bars_written"] += written
    return summary


# Per-TF fetch target and provider mapping. Providers here match what a live
# scan/backtest would resolve for the same pair, so top-up bars land under the
# exact cache key/parquet path a read would use.
_TF_INTERVAL_BINANCE = {"D1": "1d", "H4": "4h", "H1": "1h", "M30": "30m", "M15": "15m"}
_TF_TARGET_BARS = {"D1": 3000, "H4": 8000, "H1": 20000, "M30": 40000, "M15": 60000}
_MT5_PROVIDER = "mt5"
_BINANCE_PROVIDER = "binance_futures"
_EODHD_PROVIDER = "eodhd"


def _provider_for(pair: dict) -> str | None:
    source = str(pair.get("source") or "").strip().lower()
    if source == "mt5":
        return _MT5_PROVIDER
    if source == "binance":
        return _BINANCE_PROVIDER
    if source == "eodhd":
        return _EODHD_PROVIDER
    return None


def refresh_series(pair: dict, tf: str, *, store: ParquetCandleStore) -> int:
    """Top up one (pair, tf) series to the maximum history its provider allows.

    Returns the number of new bars written (0 if the fetch failed or added
    nothing). Never raises -- fetch failures (no MT5 terminal, EODHD rate
    limit, etc.) are logged and treated as "nothing to add" so a universe
    refresh can run unattended across a mixed-availability provider set.
    """
    from athena_backtest.data import fetchers

    provider = _provider_for(pair)
    if provider is None:
        return 0
    target = _TF_TARGET_BARS.get(tf, 3000)
    display = pair.get("display") or pair.get("symbol") or "?"
    try:
        if provider == _BINANCE_PROVIDER:
            interval = _TF_INTERVAL_BINANCE.get(tf)
            if interval is None:
                return 0
            rows = fetchers.fetch_binance_paginated(pair["symbol"], interval, target)
        elif provider == _MT5_PROVIDER:
            rows = fetchers.fetch_mt5(pair, tf, target)
        elif provider == _EODHD_PROVIDER:
            rows = fetchers.fetch_eodhd(pair, tf, target)
        else:
            return 0
    except Exception as exc:  # noqa: BLE001 - top-up must not abort the batch
        log.warning("[BT-ETL] refresh failed %s %s (%s): %s", display, tf, provider, exc)
        return 0

    # fetch_mt5/fetch_eodhd always return {"error": bool, "candles": [...]}
    # (never a bare list); a fallback-provider candle list on error is a
    # different source's data and must not be written under this provider key.
    if isinstance(rows, dict):
        if rows.get("error"):
            log.info(
                "[BT-ETL] refresh no-data %s %s (%s): %s",
                display,
                tf,
                provider,
                rows.get("detail"),
            )
            return 0
        rows = rows.get("candles")

    if not rows:
        return 0
    return store.write(pair, tf, rows, provider=provider)


# Policy v4 universal ladder needs setup H1 + trigger M15 on top of D1/H4
# structure. Keep M30 optional for legacy Engine B completeness.
_DEFAULT_REFRESH_TFS = ("D1", "H4", "H1", "M15")


def refresh_universe(
    pairs: list[dict],
    *,
    tfs: tuple[str, ...] = _DEFAULT_REFRESH_TFS,
    store: ParquetCandleStore | None = None,
) -> dict:
    """Top up every (pair, tf) series in `pairs` to maximum available history."""
    store = store or ParquetCandleStore()
    summary = {"attempted": 0, "written": 0, "no_data": 0, "unsupported_source": 0}
    for pair in pairs:
        if _provider_for(pair) is None:
            summary["unsupported_source"] += 1
            continue
        for tf in tfs:
            summary["attempted"] += 1
            written = refresh_series(pair, tf, store=store)
            if written:
                summary["written"] += written
                log.info(
                    "[BT-ETL] top-up %s %s: +%s bars",
                    pair.get("display") or pair.get("symbol"),
                    tf,
                    written,
                )
            else:
                summary["no_data"] += 1
    return summary


def coverage_report(*, store: ParquetCandleStore | None = None) -> list[dict]:
    """Return a coverage row per stored series: symbol/tf/provider/first/last/count."""
    store = store or ParquetCandleStore()
    from datetime import datetime, timezone

    report = []
    for provider, source, symbol_dir, tf, path in store.iter_series():
        meta = store.read_meta(symbol_dir)
        pair = {
            "display": meta.get("display") or symbol_dir.name,
            "symbol": meta.get("symbol") or symbol_dir.name,
            "source": source,
            "type": meta.get("asset_type"),
        }
        first, last, count = store.coverage(pair, tf, provider=provider)
        report.append(
            {
                "provider": provider,
                "source": source,
                "symbol": symbol_dir.name,
                "tf": tf,
                "count": count,
                "first": datetime.fromtimestamp(first, tz=timezone.utc).isoformat() if first else None,
                "last": datetime.fromtimestamp(last, tz=timezone.utc).isoformat() if last else None,
            }
        )
    return report


def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Athena backtest data ETL")
    parser.add_argument("--migrate", action="store_true", help="migrate legacy SQLite cache to parquet")
    parser.add_argument("--refresh", action="store_true", help="top up history to provider maximum")
    parser.add_argument(
        "--source",
        choices=("mt5", "binance", "eodhd"),
        help="restrict --refresh to pairs from one source",
    )
    parser.add_argument("--symbol", action="append", help="restrict --refresh to one or more symbols")
    parser.add_argument(
        "--tfs",
        default=",".join(_DEFAULT_REFRESH_TFS),
        help=f"comma-separated TFs to refresh (default: {','.join(_DEFAULT_REFRESH_TFS)})",
    )
    parser.add_argument("--coverage", action="store_true", help="print coverage report")
    args = parser.parse_args()

    store = ParquetCandleStore()
    log.info("[BT-ETL] store root: %s", store.root)

    if args.migrate:
        summary = migrate_sqlite_to_parquet(store=store)
        print(summary)

    if args.refresh:
        from athena_backtest.data.fetchers import _load_monolith

        mod = _load_monolith()
        pairs = list(getattr(mod, "ALL_PAIRS", []))
        if args.source:
            pairs = [p for p in pairs if str(p.get("source") or "").lower() == args.source]
        if args.symbol:
            wanted = {s.upper() for s in args.symbol}
            pairs = [
                p
                for p in pairs
                if str(p.get("symbol") or "").upper() in wanted
                or str(p.get("display") or "").upper() in wanted
            ]
        tfs = tuple(
            tf.strip().upper()
            for tf in str(args.tfs or "").split(",")
            if tf.strip()
        ) or _DEFAULT_REFRESH_TFS
        log.info("[BT-ETL] refresh: %s pairs tfs=%s", len(pairs), list(tfs))
        summary = refresh_universe(pairs, tfs=tfs, store=store)
        print(summary)

    if args.coverage or not (args.migrate or args.refresh or args.coverage):
        rows = coverage_report(store=store)
        rows.sort(key=lambda r: (r["source"], r["symbol"], r["tf"]))
        for row in rows:
            print(
                f"{row['source']:6s} {row['symbol']:14s} {row['tf']:4s} "
                f"n={row['count']:6d}  {row['first']}  ->  {row['last']}"
            )
        print(f"total series: {len(rows)}")


if __name__ == "__main__":
    _main()
