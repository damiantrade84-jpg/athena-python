"""Institutional session clock for GROK (New York time, ICT windows)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import GrokConfig
from .models import Candle, utc_iso


def _zone(config: GrokConfig):
    name = str(config.sessions.get("timezone") or "America/New_York")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        offset = float(config.sessions.get("fallback_utc_offset_hours") or -4.0)
        return timezone(timedelta(hours=offset))


def ny_datetime(epoch: float, config: GrokConfig) -> datetime:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone(_zone(config))


def _minute_of_day(local: datetime) -> int:
    return local.hour * 60 + local.minute


def _in_window(minute: int, start: int, end: int) -> bool:
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def _window_distance(minute: int, start: int, end: int) -> int:
    if _in_window(minute, start, end):
        return 0
    if start < end:
        return min(abs(minute - start), abs(minute - end))
    before_start = (start - minute) % 1440
    after_end = (minute - end) % 1440
    return min(before_start, after_end)


def market_is_open(epoch: float, config: GrokConfig, asset_type: str) -> bool:
    gated = {str(item).strip().lower() for item in (config.sessions.get("apply_weekend_gate_to") or [])}
    if str(asset_type or "").strip().lower() not in gated:
        return True
    local = ny_datetime(epoch, config)
    close_day = int(config.sessions["weekend_close_weekday"])
    close_hour = int(config.sessions["weekend_close_hour"])
    open_day = int(config.sessions["weekend_open_weekday"])
    open_hour = int(config.sessions["weekend_open_hour"])
    weekday = local.weekday()
    hour = local.hour + local.minute / 60.0
    if weekday > close_day or (weekday == close_day and hour >= close_hour):
        if weekday < open_day or (weekday == open_day and hour < open_hour):
            return False
        if weekday > open_day:
            return weekday != 6 or hour >= open_hour
    if weekday == 5:
        return False
    if weekday == 6 and hour < open_hour:
        return False
    return True


def classify_session(epoch: float, config: GrokConfig) -> dict[str, Any]:
    local = ny_datetime(epoch, config)
    minute = _minute_of_day(local)
    windows = config.sessions["windows"]
    active: list[dict[str, Any]] = []
    nearest_name = None
    nearest_kind = "off"
    nearest_quality = float(config.sessions["off_session_quality"])
    nearest_distance = 10_000
    adjacent = int(config.sessions["adjacent_minutes"])
    for name, window in windows.items():
        start = int(window["start_minute"])
        end = int(window["end_minute"])
        distance = _window_distance(minute, start, end)
        quality = float(window["quality"])
        kind = str(window["kind"])
        inside = distance == 0
        if inside:
            active.append({"name": name, "kind": kind, "quality": quality})
            if quality > nearest_quality or (quality == nearest_quality and kind == "silver_bullet"):
                nearest_name = name
                nearest_kind = kind
                nearest_quality = quality
                nearest_distance = 0
        elif distance < nearest_distance:
            nearest_name = name
            nearest_kind = kind
            nearest_distance = distance
            if distance <= adjacent and kind != "dead":
                nearest_quality = float(config.sessions["adjacent_quality"])
            else:
                nearest_quality = float(config.sessions["off_session_quality"])
    if any(item["kind"] == "silver_bullet" for item in active):
        bullet = max((item for item in active if item["kind"] == "silver_bullet"), key=lambda item: item["quality"])
        nearest_name = bullet["name"]
        nearest_kind = bullet["kind"]
        nearest_quality = float(bullet["quality"])
        nearest_distance = 0
    return {
        "timezone": str(config.sessions.get("timezone")),
        "localIso": local.isoformat(),
        "weekday": local.strftime("%A"),
        "minuteOfDay": minute,
        "activeWindows": active,
        "primaryWindow": nearest_name,
        "primaryKind": nearest_kind,
        "inWindow": nearest_distance == 0,
        "adjacent": nearest_distance > 0 and nearest_distance <= adjacent,
        "distanceMinutes": nearest_distance if nearest_distance < 10_000 else None,
        "quality": round(float(nearest_quality), 4),
        "clock": utc_iso(epoch),
    }


def session_bounds(epoch: float, config: GrokConfig, name: str) -> tuple[float, float] | None:
    windows = config.sessions["windows"]
    window = windows.get(name)
    if not isinstance(window, dict):
        return None
    local = ny_datetime(epoch, config)
    start_minute = int(window["start_minute"])
    end_minute = int(window["end_minute"])
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=start_minute)
    if start_minute >= end_minute:
        # Overnight window (Asia): the range that completed most recently.
        if _minute_of_day(local) >= start_minute:
            end_local = start_local + timedelta(days=1)
            end_local = end_local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=end_minute)
        else:
            start_local = start_local - timedelta(days=1)
            end_local = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=end_minute)
    else:
        end_local = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=end_minute)
        if local.timestamp() < start_local.timestamp():
            start_local -= timedelta(days=1)
            end_local -= timedelta(days=1)
    return start_local.timestamp(), end_local.timestamp()


def envelope_for_window(
    candles: list[Candle],
    epoch: float,
    config: GrokConfig,
    name: str,
) -> dict[str, Any]:
    bounds = session_bounds(epoch, config, name)
    if not bounds or not candles:
        return {"available": False, "name": name, "reason": "WINDOW_UNAVAILABLE"}
    start, end = bounds
    rows = [candle for candle in candles if start <= candle.time < end]
    if not rows:
        return {
            "available": False,
            "name": name,
            "reason": "NO_BARS_IN_WINDOW",
            "start": utc_iso(start),
            "end": utc_iso(end),
        }
    return {
        "available": True,
        "name": name,
        "high": max(candle.high for candle in rows),
        "low": min(candle.low for candle in rows),
        "open": rows[0].open,
        "close": rows[-1].close,
        "bars": len(rows),
        "start": utc_iso(start),
        "end": utc_iso(end),
    }
