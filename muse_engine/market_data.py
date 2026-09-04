"""Broker-native candle acquisition and closed-bar normalization for MUSE."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Callable

from .config import MuseConfig
from .models import Candle, MarketSnapshot, TIMEFRAME_SECONDS, utc_iso


class MuseMarketDataError(RuntimeError):
    pass


def _epoch(value: Any) -> float:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if numeric > 1e14:
            numeric /= 1_000_000.0
        elif numeric > 1e11:
            numeric /= 1_000.0
        return numeric
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp missing")
    try:
        numeric = float(text)
    except ValueError:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return _epoch(numeric)


def _float(row: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            result = float(value)
            if math.isfinite(result):
                return result
    raise ValueError(f"missing numeric field: {'/'.join(keys)}")


def _optional_float(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        result = float(value)
        if math.isfinite(result) and result >= 0:
            return result
    return None


def normalize_closed_candles(
    rows: Any,
    timeframe: str,
    *,
    now_epoch: float,
    provider: str,
    observed_at_epoch: float | None = None,
) -> tuple[list[Candle], dict[str, Any], list[str]]:
    errors: list[str] = []
    source_detail = ""
    if isinstance(rows, dict):
        source_detail = str(rows.get("detail") or "")
        if rows.get("error") is True:
            code = f"SOURCE_ERROR:{timeframe}:{source_detail or 'unknown'}"
            return [], {"provider": provider, "timeframe": timeframe, "bars": 0,
                        "sourceDetail": source_detail}, [code]
        rows = rows.get("candles", rows.get("data", []))
    if not isinstance(rows, list):
        return [], {"provider": provider, "timeframe": timeframe, "bars": 0}, [f"BAD_PAYLOAD:{timeframe}"]
    seconds = TIMEFRAME_SECONDS[timeframe]
    now_bar_open = math.floor(float(now_epoch) / seconds) * seconds
    candles: list[Candle] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            stamp = _epoch(row.get("time", row.get("timestamp", row.get("t"))))
            bar_open = math.floor(stamp / seconds) * seconds
            if bar_open >= now_bar_open:
                continue  # forming bar is never scored
            candle = Candle(
                time=float(bar_open),
                open=_float(row, "open", "o"),
                high=_float(row, "high", "h"),
                low=_float(row, "low", "l"),
                close=_float(row, "close", "c"),
                volume=_optional_float(row, "volume", "v", "tick_volume"),
                volume_source=provider,
            )
        except (ValueError, TypeError):
            errors.append(f"MALFORMED_BAR:{timeframe}")
            continue
        candles.append(candle)
    candles.sort(key=lambda c: c.time)
    # De-duplicate by bar open, keep last.
    deduped: dict[int, Candle] = {}
    for candle in candles:
        deduped[int(candle.time)] = candle
    ordered = [deduped[key] for key in sorted(deduped)]
    provenance = {"provider": provider, "timeframe": timeframe, "bars": len(ordered),
                  "lastClosedAt": utc_iso(ordered[-1].time) if ordered else None,
                  "sourceDetail": source_detail,
                  "observedAt": utc_iso(observed_at_epoch) if observed_at_epoch else None}
    return ordered, provenance, errors


class MuseMarketDataProvider:
    def __init__(self, *, config: MuseConfig, fetch_mt5, fetch_bybit) -> None:
        self.config = config
        self._fetch_mt5 = fetch_mt5
        self._fetch_bybit = fetch_bybit

    def snapshot(self, pair: dict[str, Any], *, now_epoch: float) -> MarketSnapshot:
        asset_type = str(pair.get("type") or "unknown").strip().lower()
        venue = "bybit" if asset_type == "crypto" else "mt5"
        display = str(pair.get("display") or pair.get("symbol") or "")
        symbol = str(pair.get("symbol") or display)
        frames: dict[str, list[Candle]] = {}
        provenance: dict[str, dict[str, Any]] = {}
        quality_errors: list[str] = []
        for timeframe in ("D1", "H4", "M15", "M5"):
            requested = int(self.config.scan["bars"][timeframe])
            try:
                if venue == "bybit":
                    rows = self._fetch_bybit(symbol.replace("/", "").upper(), timeframe, requested)
                    provider = "bybit"
                else:
                    rows = self._fetch_mt5(display, timeframe, requested)
                    provider = "mt5"
            except Exception as exc:
                quality_errors.append(f"FETCH_FAILED:{timeframe}:{type(exc).__name__}")
                frames[timeframe] = []
                provenance[timeframe] = {"provider": venue, "timeframe": timeframe, "bars": 0}
                continue
            candles, prov, errors = normalize_closed_candles(rows, timeframe, now_epoch=now_epoch, provider=provider)
            frames[timeframe] = candles
            provenance[timeframe] = prov
            quality_errors.extend(errors)
        return MarketSnapshot(pair=pair, frames=frames, provenance=provenance,
                              as_of_epoch=float(now_epoch), quality_errors=quality_errors)
