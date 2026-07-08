"""Ingest Bybit klines, funding and open-interest history into PTIS."""

from __future__ import annotations

import logging
import time
from typing import Any

from athena_ase.data.availability import AvailabilityRuleId
from athena_ase.data.ingest.common import (
    append_ptis_rows,
    bybit_series_id,
    price_series_id,
    row_from_rule,
)
from athena_ase.data.ptis import PTISStore

log = logging.getLogger("ase.ingest.bybit")

# Public market-data REST (mainnet: real prices for signals; execution stays
# on the testnet URL enforced by the demo gate/bridge — market data is
# read-only and carries no order risk).
_KLINE_URL = "https://api.bybit.com/v5/market/kline"
_KLINE_INTERVALS = {"H1": "60", "D1": "D"}
_TF_MS = {"H1": 3_600_000, "D1": 86_400_000}
_KLINE_MAX_LIMIT = 1000
_PRICE_FIELDS = ("open", "high", "low", "close", "volume")

def _default_crypto_symbols() -> list[str]:
    from athena_ase.instruments import instruments_for_family

    return [inst.symbol for inst in instruments_for_family("crypto")]


def _fetch_derivatives(symbol: str, *, lookback_days: int = 730) -> tuple[list[dict], list[dict]]:
    from datetime import datetime, timedelta, timezone

    from data_feeds import prepare_crypto_backtest_derivative_series

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    # Minimal candle stubs to bound the fetch window.
    stub = {
        "time": start.isoformat(),
    }
    stub_end = {"time": end.isoformat()}
    pair = {"symbol": symbol, "type": "crypto"}
    return prepare_crypto_backtest_derivative_series(
        pair, [stub_end], [stub_end], [stub, stub_end], oi_interval="1h"
    )


def _fetch_klines(symbol: str, interval: str, *, limit: int) -> list[list[str]]:
    import requests

    resp = requests.get(
        _KLINE_URL,
        params={
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": max(1, min(int(limit), _KLINE_MAX_LIMIT)),
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if int(data.get("retCode", -1)) != 0:
        raise RuntimeError(f"bybit kline error: {data.get('retMsg')}")
    return list((data.get("result") or {}).get("list") or [])


def _kline_limit(store: PTISStore, symbol: str, tf: str, lookback_days: int, now_ms: int) -> int:
    """Top-up size, self-healing to a full fetch when the series is behind."""
    tf_ms = _TF_MS[tf]
    want = int(lookback_days * 86_400_000 / tf_ms) + 10
    sid = price_series_id("BYBIT", symbol, tf, "close")
    if not store.series_exists(sid):
        return _KLINE_MAX_LIMIT
    try:
        rows = store.asof(sid, now_ms, 1)
    except KeyError:
        return _KLINE_MAX_LIMIT
    if len(rows) == 0:
        return _KLINE_MAX_LIMIT
    behind = int((now_ms - int(rows[-1]["value_time"])) / tf_ms) + 5
    return min(_KLINE_MAX_LIMIT, max(want, behind))


def ingest_symbol_klines(
    store: PTISStore,
    symbol: str,
    *,
    lookback_days: int = 730,
    timeframes: tuple[str, ...] = ("H1", "D1"),
    now_ms: int | None = None,
) -> dict[str, int]:
    """Confirmed Bybit kline OHLCV → PTIS (BYBIT price source).

    Drops the forming bar; value/available time is the bar close, matching the
    Binance-cache ingest convention so downstream freshness logic is uniform.
    """
    sym = symbol.replace("/", "").upper()
    now = now_ms or int(time.time() * 1000)
    counts: dict[str, int] = {}
    for tf in timeframes:
        interval = _KLINE_INTERVALS.get(tf)
        if interval is None:
            continue
        tf_ms = _TF_MS[tf]
        limit = _kline_limit(store, sym, tf, lookback_days, now)
        klines = _fetch_klines(sym, interval, limit=limit)
        field_rows: dict[str, list[dict]] = {f: [] for f in _PRICE_FIELDS}
        for entry in klines:
            # v5 kline row: [startMs, open, high, low, close, volume, turnover]
            try:
                start_ms = int(entry[0])
                values = {
                    "open": float(entry[1]),
                    "high": float(entry[2]),
                    "low": float(entry[3]),
                    "close": float(entry[4]),
                    "volume": float(entry[5]),
                }
            except (IndexError, TypeError, ValueError):
                continue
            close_ms = start_ms + tf_ms
            if close_ms > now:
                continue  # forming bar — confirmed bars only
            for field, value in values.items():
                sid = price_series_id("BYBIT", sym, tf, field)
                field_rows[field].append(
                    row_from_rule(
                        sid,
                        AvailabilityRuleId.MT5_BAR,
                        value_time_ms=close_ms,
                        value=value,
                        bar_close_ms=close_ms,
                    )
                )
        for field, rows in field_rows.items():
            if not rows:
                continue
            sid = price_series_id("BYBIT", sym, tf, field)
            counts[sid] = append_ptis_rows(store, sid, "BYBIT", rows)
    return counts


def ingest_symbol(
    store: PTISStore,
    symbol: str,
    *,
    lookback_days: int = 730,
) -> dict[str, int]:
    sym = symbol.replace("/", "").upper()
    funding_rows, oi_rows = _fetch_derivatives(sym, lookback_days=lookback_days)
    counts: dict[str, int] = {}

    fund_sid = bybit_series_id(sym, "funding")
    fund_ptis: list[dict] = []
    for row in funding_rows:
        ts_ms = int(row.get("ts_ms") or 0)
        if ts_ms <= 0:
            continue
        fund_ptis.append(
            row_from_rule(
                fund_sid,
                AvailabilityRuleId.BYBIT_EVENT,
                value_time_ms=ts_ms,
                value=float(row.get("rate") or 0.0),
            )
        )
    counts[fund_sid] = append_ptis_rows(store, fund_sid, "BYBIT", fund_ptis)

    oi_sid = bybit_series_id(sym, "oi")
    oi_ptis: list[dict] = []
    for row in oi_rows:
        ts_ms = int(row.get("ts_ms") or 0)
        if ts_ms <= 0:
            continue
        oi_ptis.append(
            row_from_rule(
                oi_sid,
                AvailabilityRuleId.BYBIT_EVENT,
                value_time_ms=ts_ms,
                value=float(row.get("oi") or 0.0),
            )
        )
    counts[oi_sid] = append_ptis_rows(store, oi_sid, "BYBIT", oi_ptis)
    return counts


def ingest_microstructure_snapshot(
    store: PTISStore,
    symbol: str,
    *,
    now_ms: int | None = None,
) -> dict[str, int]:
    """Snapshot trade-bucket CVD metrics into PTIS for ASE microstructure features."""
    from config import CONFIG

    from athena.microstructure.aggtrade_volume import check_trade_bucket_cvd
    from athena_ase.settings import microstructure_enabled

    if not microstructure_enabled():
        return {}

    now_ms = now_ms or int(time.time() * 1000)
    cvd = check_trade_bucket_cvd(
        symbol,
        CONFIG,
        reference_ts=now_ms,
        require_fresh=False,
    )
    counts: dict[str, int] = {}
    slope = float(cvd.get("cvd_slope") or 0.0)
    cvd_val = float(cvd.get("cvd_value") or 0.0)
    bucket_n = float(cvd.get("bucket_count") or 0.0)
    imbalance_z = slope
    bucket_delta_z = cvd_val / max(bucket_n, 1.0)

    for field, value in (
        ("trade_imbalance_z", imbalance_z),
        ("bucket_delta_z", bucket_delta_z),
    ):
        sid = bybit_series_id(symbol, field)
        row = row_from_rule(
            sid,
            now_ms,
            now_ms,
            value,
            rule_id=AvailabilityRuleId.BYBIT_DERIVATIVE,
        )
        counts[sid] = append_ptis_rows(store, sid, "BYBIT", [row])
    return counts


def ingest_all(
    store: PTISStore,
    *,
    symbols: list[str] | None = None,
    lookback_days: int = 730,
    include_klines: bool = True,
) -> dict[str, int]:
    totals: dict[str, int] = {}
    targets = symbols or _default_crypto_symbols()
    for sym in targets:
        if include_klines:
            try:
                totals.update(ingest_symbol_klines(store, sym, lookback_days=lookback_days))
            except Exception as exc:
                log.warning("Bybit kline ingest failed %s: %s", sym, exc)
        try:
            totals.update(ingest_symbol(store, sym, lookback_days=lookback_days))
        except Exception as exc:
            log.warning("Bybit ingest failed %s: %s", sym, exc)
        try:
            totals.update(ingest_microstructure_snapshot(store, sym))
        except Exception as exc:
            log.warning("Bybit microstructure ingest failed %s: %s", sym, exc)
    log.info("Bybit ingest complete: %d series", len(totals))
    return totals
