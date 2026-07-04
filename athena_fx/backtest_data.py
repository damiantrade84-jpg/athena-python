"""Daily OHLC loading for the FX factor backtest.

Reads D1 bars from the repo's backtest candle cache (offline, no network).
Missing symbols are surfaced as explicit diagnostics — bars are never
fabricated and gaps are never silently skipped (fail-closed research data).
"""
from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
from typing import Any, Optional

from runtime_paths import resolve_backtest_candle_cache_db_path

from athena_fx.models import DIAG_MISSING_OHLC, Diagnostic, G8_PAIRS

log = logging.getLogger("athena_fx.backtest_data")


def fx_pair_dict(symbol: str) -> dict[str, str]:
    """Minimal pair dict for backtest candle cache keys."""
    sym = str(symbol).replace("/", "").upper()
    return {
        "display": f"{sym[:3]}/{sym[3:]}" if len(sym) == 6 else sym,
        "symbol": sym,
        "source": "mt5",
        "type": "forex",
    }


def cached_d1_bar_count(symbol: str, *, db_path: Optional[str] = None) -> int:
    """Return count of cached D1 bars for *symbol* (any provider)."""
    path = db_path or str(resolve_backtest_candle_cache_db_path())
    sym = str(symbol).replace("/", "").upper()
    display = f"{sym[:3]}/{sym[3:]}" if len(sym) == 6 else sym
    try:
        con = sqlite3.connect(path, timeout=15.0)
        try:
            row = con.execute(
                """
                SELECT COUNT(1) FROM backtest_candles
                WHERE timeframe='D1'
                  AND (REPLACE(display,'/','')=? OR symbol=? OR display=?)
                """,
                (sym, sym, display),
            ).fetchone()
            return int(row[0] if row else 0)
        finally:
            con.close()
    except sqlite3.Error:
        return 0


def g8_pairs_with_cached_bars(*, db_path: Optional[str] = None) -> list[str]:
    """G8 symbols that have at least one D1 bar in the backtest cache."""
    return [p for p in G8_PAIRS if cached_d1_bar_count(p, db_path=db_path) > 0]


def ensure_d1_bars_mt5(
    symbols: list[str],
    *,
    min_bars: int = 500,
    fetch_limit: int = 3000,
    db_path: Optional[str] = None,
) -> list[Diagnostic]:
    """Fetch missing D1 history from MT5 into the backtest candle cache."""
    from backtest_candle_cache import fetch_backtest_candles

    diagnostics: list[Diagnostic] = []
    try:
        from mt5_executor import _get_mt5, mt5_connect, mt5_map_symbol
    except ImportError:
        diagnostics.append(Diagnostic(
            DIAG_MISSING_OHLC, "warning",
            "MT5 executor unavailable — cannot fetch missing D1 bars",
            {},
        ))
        return diagnostics

    mt5 = _get_mt5()
    if not mt5 or not mt5_connect():
        diagnostics.append(Diagnostic(
            DIAG_MISSING_OHLC, "warning",
            "MT5 not connected — skipping D1 bar fetch for missing symbols",
            {},
        ))
        return diagnostics

    for symbol in symbols:
        sym = str(symbol).replace("/", "").upper()
        existing = cached_d1_bar_count(sym, db_path=db_path)
        if existing >= min_bars:
            continue
        mt5_symbol = mt5_map_symbol(sym)
        if not mt5_symbol:
            diagnostics.append(Diagnostic(
                DIAG_MISSING_OHLC, "warning",
                f"no MT5 symbol mapping for {sym}",
                {"symbol": sym},
            ))
            continue
        if not mt5.symbol_select(mt5_symbol, True):
            diagnostics.append(Diagnostic(
                DIAG_MISSING_OHLC, "warning",
                f"MT5 symbol not selectable: {mt5_symbol}",
                {"symbol": sym, "mt5_symbol": mt5_symbol},
            ))
            continue

        pair = fx_pair_dict(sym)

        def _fetcher(limit: int, _mt5_sym: str = mt5_symbol) -> list[dict] | None:
            bars = mt5.copy_rates_from_pos(_mt5_sym, mt5.TIMEFRAME_D1, 0, int(limit))
            if bars is None or len(bars) == 0:
                return None
            names = getattr(getattr(bars, "dtype", None), "names", None) or ()
            out: list[dict] = []
            for bar in bars:
                ts = int(bar["time"])
                dt = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc)
                vol = float(bar["tick_volume"]) if "tick_volume" in names else 0.0
                out.append({
                    "time": dt.strftime("%Y-%m-%d"),
                    "open": float(bar["open"]),
                    "high": float(bar["high"]),
                    "low": float(bar["low"]),
                    "close": float(bar["close"]),
                    "vol": vol,
                })
            return out

        try:
            fetched = fetch_backtest_candles(
                pair,
                "D1",
                fetch_limit,
                _fetcher,
                provider="mt5",
                min_bars=min_bars,
            )
        except Exception as exc:
            diagnostics.append(Diagnostic(
                DIAG_MISSING_OHLC, "warning",
                f"D1 fetch failed for {sym}: {exc}",
                {"symbol": sym},
            ))
            continue

        new_count = cached_d1_bar_count(sym, db_path=db_path)
        if new_count <= existing:
            diagnostics.append(Diagnostic(
                DIAG_MISSING_OHLC, "warning",
                f"D1 fetch returned no new bars for {sym}",
                {"symbol": sym, "cached_before": existing},
            ))
        else:
            log.info(
                "cached D1 bars for %s: %s -> %s",
                sym, existing, new_count,
            )
            if fetched is None:
                diagnostics.append(Diagnostic(
                    DIAG_MISSING_OHLC, "info",
                    f"cached {new_count} D1 bars for {sym}",
                    {"symbol": sym, "bar_count": new_count},
                ))

    return diagnostics


def _date_only(bar_time: str) -> Optional[str]:
    text = str(bar_time or "").strip()
    if len(text) < 10:
        return None
    try:
        _dt.date.fromisoformat(text[:10])
    except ValueError:
        return None
    return text[:10]


def load_daily_bars(
    symbols: list[str],
    start: str,
    end: str,
    *,
    db_path: Optional[str] = None,
) -> tuple[dict[str, list[dict[str, Any]]], list[Diagnostic]]:
    """Load ascending D1 bars per symbol from the backtest candle cache.

    Returns (bars_by_symbol, diagnostics). Symbols without cached D1 data in
    the window are omitted from the map and reported via diagnostics.
    """
    path = db_path or str(resolve_backtest_candle_cache_db_path())
    bars_by_symbol: dict[str, list[dict[str, Any]]] = {}
    diagnostics: list[Diagnostic] = []
    try:
        con = sqlite3.connect(path, timeout=15.0)
    except sqlite3.Error as exc:
        return {}, [Diagnostic(
            DIAG_MISSING_OHLC, "error",
            f"backtest candle cache unavailable: {exc}", {"db_path": path},
        )]
    try:
        con.row_factory = sqlite3.Row
        for symbol in symbols:
            sym = str(symbol).replace("/", "").upper()
            display = f"{sym[:3]}/{sym[3:]}" if len(sym) == 6 else sym
            try:
                rows = con.execute(
                    """
                    SELECT bar_time, open, high, low, close
                    FROM backtest_candles
                    WHERE timeframe='D1'
                      AND (REPLACE(display,'/','')=? OR symbol=? OR display=?)
                    ORDER BY bar_time
                    """,
                    (sym, sym, display),
                ).fetchall()
            except sqlite3.Error as exc:
                diagnostics.append(Diagnostic(
                    DIAG_MISSING_OHLC, "error",
                    f"candle cache query failed for {sym}: {exc}",
                    {"symbol": sym},
                ))
                continue
            bars: list[dict[str, Any]] = []
            seen: set[str] = set()
            for row in rows:
                date = _date_only(row["bar_time"])
                if date is None or date < start or date > end or date in seen:
                    continue
                seen.add(date)
                bars.append({
                    "date": date,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                })
            if not bars:
                diagnostics.append(Diagnostic(
                    DIAG_MISSING_OHLC, "warning",
                    f"no cached D1 bars for {sym} in {start}..{end}",
                    {"symbol": sym, "start": start, "end": end},
                ))
                continue
            bars_by_symbol[sym] = bars
    finally:
        con.close()
    return bars_by_symbol, diagnostics
