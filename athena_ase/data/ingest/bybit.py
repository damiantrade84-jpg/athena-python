"""Ingest Bybit funding and open-interest history into PTIS."""

from __future__ import annotations

import logging
from typing import Any

from athena_ase.data.availability import AvailabilityRuleId
from athena_ase.data.ingest.common import append_ptis_rows, bybit_series_id, row_from_rule
from athena_ase.data.ptis import PTISStore

log = logging.getLogger("ase.ingest.bybit")

_DEFAULT_CRYPTO = (
    {"symbol": "BTCUSDT", "type": "crypto"},
    {"symbol": "ETHUSDT", "type": "crypto"},
)


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


def ingest_all(
    store: PTISStore,
    *,
    symbols: list[str] | None = None,
    lookback_days: int = 730,
) -> dict[str, int]:
    totals: dict[str, int] = {}
    targets = symbols or [p["symbol"] for p in _DEFAULT_CRYPTO]
    for sym in targets:
        try:
            totals.update(ingest_symbol(store, sym, lookback_days=lookback_days))
        except Exception as exc:
            log.warning("Bybit ingest failed %s: %s", sym, exc)
    log.info("Bybit ingest complete: %d series", len(totals))
    return totals
