"""Broker-native candle acquisition and strict normalization for SOL."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import time
from typing import Any, Callable

from .config import SolConfig
from .models import Candle, MarketSnapshot, TIMEFRAME_SECONDS, utc_iso


class SolMarketDataError(RuntimeError):
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
    """Normalize one series and remove any bar that is not provably closed."""

    errors: list[str] = []
    source_detail = ""
    if isinstance(rows, dict):
        source_detail = str(rows.get("detail") or "")
        if rows.get("error") is True:
            code = f"SOURCE_ERROR:{timeframe}:{source_detail or 'unknown'}"
            return [], {
                "provider": provider,
                "timeframe": timeframe,
                "bars": 0,
                "sourceError": source_detail or "unknown",
            }, [code]
        rows = rows.get("candles")
    if not isinstance(rows, (list, tuple)):
        return [], {"provider": provider, "timeframe": timeframe, "bars": 0}, [f"NO_DATA:{timeframe}"]

    interval = TIMEFRAME_SECONDS[timeframe]
    normalized: dict[int, Candle] = {}
    malformed = 0
    forming = 0
    future = 0
    post_as_of = 0
    observed_at = float(observed_at_epoch if observed_at_epoch is not None else now_epoch)
    for raw in rows:
        if not isinstance(raw, dict):
            malformed += 1
            continue
        if raw.get("confirmed") is False:
            forming += 1
            continue
        try:
            time_value = raw.get("open_time")
            if time_value is None:
                time_value = raw.get("time", raw.get("timestamp", raw.get("ts")))
            opened_at = _epoch(time_value)
            # A provider bar beyond the actual receipt clock is corrupt/future.
            # A bar after a deliberately frozen scan clock is merely post-as-of
            # evidence (for example, a scan crossing a new M5 boundary).
            if opened_at > observed_at + 1.0:
                future += 1
                continue
            if opened_at > now_epoch + 1.0:
                post_as_of += 1
                continue
            # Provider bar stamps are opens.  A row without an explicit
            # confirmation flag is accepted only when its scheduled close is
            # no later than the point-in-time evaluation clock.
            if opened_at + interval > now_epoch + 1.0:
                forming += 1
                continue
            volume = _optional_float(raw, "volume", "vol", "real_volume", "tick_volume")
            volume_source = raw.get("volumeSource") or raw.get("volSource") or raw.get("volume_source")
            candle = Candle(
                time=opened_at,
                open=_float(raw, "open", "o"),
                high=_float(raw, "high", "h"),
                low=_float(raw, "low", "l"),
                close=_float(raw, "close", "c"),
                volume=volume,
                volume_source=str(volume_source) if volume_source else None,
            )
            normalized[int(round(opened_at * 1000))] = candle
        except (TypeError, ValueError, OverflowError):
            malformed += 1

    candles = [normalized[key] for key in sorted(normalized)]
    if malformed:
        errors.append(f"MALFORMED_CANDLES:{timeframe}:{malformed}")
    if future:
        errors.append(f"FUTURE_CANDLES:{timeframe}:{future}")
    volume_sources = sorted({candle.volume_source for candle in candles if candle.volume_source})
    provenance: dict[str, Any] = {
        "provider": provider,
        "timeframe": timeframe,
        "bars": len(candles),
        "formingBarsDropped": forming,
        "postAsOfBarsDropped": post_as_of,
        "futureBarsDropped": future,
        "malformedBarsDropped": malformed,
        "duplicateBarsDropped": max(0, len(rows) - forming - post_as_of - malformed - future - len(candles)),
        "volumeSources": volume_sources,
    }
    if source_detail:
        provenance["detail"] = source_detail
    if candles:
        provenance["firstOpenAt"] = utc_iso(candles[0].time)
        provenance["lastOpenAt"] = utc_iso(candles[-1].time)
        provenance["lastClosedAt"] = utc_iso(candles[-1].closes_at(timeframe))
    return candles, provenance, errors


class SolMarketDataProvider:
    """Fetch four SOL role series from the execution venue for each pair."""

    def __init__(
        self,
        *,
        config: SolConfig,
        fetch_mt5: Callable[[dict[str, Any], str, int], Any],
        fetch_bybit: Callable[[str, str, int], Any],
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.fetch_mt5 = fetch_mt5
        self.fetch_bybit = fetch_bybit
        self.now_fn = now_fn

    def _fetch(self, pair: dict[str, Any], timeframe: str, limit: int) -> tuple[Any, str]:
        asset_type = str(pair.get("type") or "").strip().lower()
        if asset_type == "crypto":
            symbol = str(pair.get("symbol") or pair.get("display") or "").replace("/", "").upper()
            return self.fetch_bybit(symbol, timeframe, limit), "bybit_v5_linear"
        return self.fetch_mt5(pair, timeframe, limit), "mt5_terminal"

    def fetch_snapshot(
        self,
        pair: dict[str, Any],
        *,
        limits: dict[str, int] | None = None,
        as_of_epoch: float | None = None,
    ) -> MarketSnapshot:
        point_in_time = float(as_of_epoch if as_of_epoch is not None else self.now_fn())
        requested = dict(self.config.scan["bars"])
        if limits:
            requested.update({str(key).upper(): int(value) for key, value in limits.items()})
        frames: dict[str, list[Candle]] = {}
        provenance: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        for timeframe in ("H4", "H1", "M15", "M5"):
            raw, provider = self._fetch(pair, timeframe, int(requested[timeframe]))
            candles, meta, frame_errors = normalize_closed_candles(
                raw,
                timeframe,
                now_epoch=point_in_time,
                provider=provider,
                observed_at_epoch=float(self.now_fn()),
            )
            frames[timeframe] = candles
            provenance[timeframe] = meta
            errors.extend(frame_errors)
        return MarketSnapshot(
            pair=dict(pair),
            frames=frames,
            provenance=provenance,
            as_of_epoch=point_in_time,
            quality_errors=errors,
        )
