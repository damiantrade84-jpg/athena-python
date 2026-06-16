from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from statistics import fmean
from typing import Any


def parse_utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        from config import CONFIG
        offset = int(CONFIG.get("SERVER_TZ_OFFSET_HOURS", 2))
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=offset))).astimezone(timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


@dataclass(frozen=True)
class SessionRange:
    session: str
    start_hour: int
    end_hour: int
    high: float
    low: float
    open: float
    close: float
    bar_count: int
    vwap: float


def _session_vwap(candles: list[dict]) -> float:
    num = 0.0
    den = 0.0
    for candle in candles:
        typical = (
            float(candle["high"]) + float(candle["low"]) + float(candle["close"])
        ) / 3.0
        vol = max(float(candle.get("vol") or candle.get("volume") or 0.0), 1.0)
        num += typical * vol
        den += vol
    return num / den if den > 0 else fmean(float(c["close"]) for c in candles)


def _bars_in_hour_window(
    candles: list[dict],
    *,
    day: datetime,
    start_hour: int,
    end_hour: int,
) -> list[dict]:
    rows: list[dict] = []
    for candle in candles:
        ts = parse_utc(candle.get("time") or candle.get("datetime"))
        if ts is None or ts.date() != day.date():
            continue
        if start_hour <= ts.hour < end_hour:
            rows.append(candle)
    return rows


def session_range_for_day(
    candles: list[dict],
    *,
    day: datetime,
    session: str,
    start_hour: int,
    end_hour: int,
) -> SessionRange | None:
    rows = _bars_in_hour_window(candles, day=day, start_hour=start_hour, end_hour=end_hour)
    if not rows:
        return None
    return SessionRange(
        session=session,
        start_hour=start_hour,
        end_hour=end_hour,
        high=max(float(c["high"]) for c in rows),
        low=min(float(c["low"]) for c in rows),
        open=float(rows[0]["open"]),
        close=float(rows[-1]["close"]),
        bar_count=len(rows),
        vwap=_session_vwap(rows),
    )


def asian_session_range(candles: list[dict], *, as_of: datetime) -> SessionRange | None:
    """Asian initial balance: 00:00-07:00 UTC on the decision day."""
    return session_range_for_day(candles, day=as_of, session="asian", start_hour=0, end_hour=7)


def london_session_range(candles: list[dict], *, as_of: datetime) -> SessionRange | None:
    """London session range: 07:00-12:00 UTC on the decision day."""
    return session_range_for_day(candles, day=as_of, session="london", start_hour=7, end_hour=12)


def session_vwap(
    candles: list[dict],
    *,
    as_of: datetime,
    start_hour: int,
    end_hour: int,
) -> float | None:
    rows = _bars_in_hour_window(candles, day=as_of, start_hour=start_hour, end_hour=end_hour)
    if not rows:
        return None
    return _session_vwap(rows)


def session_quality_score(as_of: datetime) -> tuple[float, str]:
    """London/NY overlap > London solo > NY solo > Asian."""
    hour = as_of.hour
    if 13 <= hour < 16:
        return 1.0, "london_ny_overlap"
    if 7 <= hour < 13:
        return 0.85, "london_solo"
    if 16 <= hour < 21:
        return 0.75, "ny_solo"
    if 0 <= hour < 7:
        return 0.4, "asian"
    return 0.5, "off_hours"


def volume_spike_ratio(
    candles: list[dict],
    *,
    display: str | None = None,
    tf: str = "H1",
    lookback: int = 20,
) -> float:
    if display and "/" in display:
        try:
            from duka_volume import get_forex_vr
            vr = get_forex_vr(display, tf=tf, lookback=lookback)
            if vr is not None and vr != 1.0:
                return vr
        except Exception:
            pass

    if len(candles) < lookback + 1:
        return 0.0
    recent = max(float(candles[-1].get("vol") or candles[-1].get("volume") or 0.0), 0.0)
    baseline = [
        float(c.get("vol") or c.get("volume") or 0.0)
        for c in candles[-(lookback + 1):-1]
    ]
    mean_vol = fmean(baseline) if baseline else 0.0
    if mean_vol <= 0:
        return 0.0
    return recent / mean_vol


def in_london_open_window(as_of: datetime) -> bool:
    """London open breakout window: 07:00-08:30 UTC."""
    return as_of.hour == 7 or (as_of.hour == 8 and as_of.minute <= 30)
