"""Shared helpers for PTIS ingestion pipelines."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Iterator

from athena_ase.data.availability import AvailabilityRuleId, compute_available_time_ms
from athena_ase.data.ptis import PTISStore, build_row

_TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}
_MS = 1000


def compact_symbol(symbol: str) -> str:
    return str(symbol or "").replace("/", "").replace(" ", "").upper()


def eodhd_series_id(symbol: str, tf: str, field: str) -> str:
    return f"EODHD:{compact_symbol(symbol)}:{tf.upper()}:{field.lower()}"


def duka_series_id(duka_symbol: str, tf: str) -> str:
    return f"DUKASCOPY:{duka_symbol}:{tf.upper()}:volume"


def bybit_series_id(symbol: str, field: str) -> str:
    return f"BYBIT:{compact_symbol(symbol)}:{field.lower()}"


def cot_series_id(asset: str) -> str:
    return f"CFTC:COT:{asset.upper()}:noncomm_net"


def fred_series_id(fred_id: str) -> str:
    return f"FRED:{fred_id.upper()}:rate"


def parse_iso_to_ms(value: str | None) -> int | None:
    if not value:
        return None
    text = str(value).strip()
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        if text.endswith("Z"):
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * _MS)
    except ValueError:
        try:
            dt = datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * _MS)
        except ValueError:
            return None


def date_iso_to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * _MS)


def bar_close_ms(bar_open_ms: int, tf: str) -> int:
    sec = _TF_SECONDS.get(tf.upper(), 3600)
    return int(bar_open_ms) + sec * _MS


def chunked(iterable: Iterable, size: int) -> Iterator[list]:
    batch: list = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def append_ptis_rows(
    store: PTISStore,
    series_id: str,
    source: str,
    rows: list[dict],
    *,
    batch_size: int = 5000,
) -> int:
    if not rows:
        return 0
    total = 0
    store.register_series(series_id, source)
    for batch in chunked(rows, batch_size):
        total += store.append_rows(series_id, batch, source=source, auto_register=False)
    return total


def row_from_rule(
    series_id: str,
    rule_id: AvailabilityRuleId,
    *,
    value_time_ms: int,
    value: float,
    bar_close_ms: int | None = None,
    realtime_start_ms: int | None = None,
    revision: int = 0,
) -> dict:
    avail = compute_available_time_ms(
        rule_id,
        value_time_ms=value_time_ms,
        bar_close_ms=bar_close_ms,
        realtime_start_ms=realtime_start_ms,
    )
    return build_row(series_id, value_time_ms, avail, value, revision=revision)
