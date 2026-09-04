"""Meridian tide clock for MUSE (New York time, tide/surge/drift/slack)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import MuseConfig


def _zone_from_name(name: str, fallback_hours: float):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=fallback_hours))


def _zone(config: MuseConfig):
    name = str(config.sessions.get("timezone") or "America/New_York")
    offset = float(config.sessions.get("fallback_utc_offset_hours") or -4.0)
    return _zone_from_name(name, offset)


def _display_zone(config: MuseConfig):
    name = str(config.sessions.get("display_timezone") or "Africa/Johannesburg")
    offset = float(config.sessions.get("display_fallback_utc_offset_hours") or 2.0)
    return _zone_from_name(name, offset)


def tide_datetime(epoch: float, config: MuseConfig) -> datetime:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone(_zone(config))


def display_datetime(epoch: float, config: MuseConfig) -> datetime:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone(_display_zone(config))


def _hm(local: datetime) -> str:
    return local.strftime("%H:%M")


def _minute_of_day(local: datetime) -> int:
    return local.hour * 60 + local.minute


def _in_window(minute: int, start: int, end: int) -> bool:
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def tide_state(epoch: float, config: MuseConfig) -> dict[str, Any]:
    """Classify now into the tide schedule; fringe softens window edges."""
    local = tide_datetime(epoch, config)
    minute = _minute_of_day(local)
    windows = config.sessions["windows"]
    fringe = int(config.sessions.get("fringe_minutes") or 0)
    fringe_quality = float(config.sessions.get("fringe_quality") or 0.5)
    off_quality = float(config.sessions.get("off_tide_quality") or 0.14)
    best: dict[str, Any] | None = None
    for name, window in windows.items():
        start = int(window["start_minute"])
        end = int(window["end_minute"])
        quality = float(window["quality"])
        if _in_window(minute, start, end):
            candidate = {"window": name, "kind": str(window["kind"]), "quality": quality, "fringe": False}
            if best is None or candidate["quality"] > best["quality"]:
                best = candidate
            continue
        if fringe > 0:
            # Fringe: within N minutes outside either edge counts at fringe quality.
            dist = min((minute - start) % 1440, (end - minute) % 1440)
            if dist <= fringe:
                candidate = {"window": name, "kind": str(window["kind"]), "quality": min(quality, fringe_quality), "fringe": True}
                if best is None or candidate["quality"] > best["quality"]:
                    best = candidate
    if best is None:
        return {"window": "off_tide", "kind": "slack", "quality": off_quality, "fringe": False,
                "nyTime": _hm(local), "weekday": local.weekday()}
    return {**best, "nyTime": _hm(local), "weekday": local.weekday()}


def market_is_closed(epoch: float, config: MuseConfig, asset_type: str) -> tuple[bool, str | None]:
    gated = config.sessions.get("apply_weekend_gate_to") or []
    if str(asset_type or "").lower() not in {str(v).lower() for v in gated}:
        return False, None
    local = tide_datetime(epoch, config)
    weekday = local.weekday()
    hour = local.hour
    close_wd = int(config.sessions.get("weekend_close_weekday"))
    close_h = int(config.sessions.get("weekend_close_hour"))
    open_wd = int(config.sessions.get("weekend_open_weekday"))
    open_h = int(config.sessions.get("weekend_open_hour"))
    # Closed Fri close_h -> Sun open_h (Mon=0..Sun=6).
    if weekday == close_wd and hour >= close_h:
        return True, "WEEKEND_CLOSED"
    if weekday > close_wd or weekday < open_wd:
        # Saturday (5) is fully inside the window; Friday handled above.
        if weekday in (5,):
            return True, "WEEKEND_CLOSED"
        if weekday == open_wd and hour < open_h:
            return True, "WEEKEND_CLOSED"
    if weekday == open_wd and hour < open_h:
        return True, "WEEKEND_CLOSED"
    return False, None


def window_schedule(epoch: float, config: MuseConfig) -> list[dict[str, Any]]:
    now_ny = tide_datetime(epoch, config)
    midnight = now_ny.replace(hour=0, minute=0, second=0, microsecond=0)
    display_zone = _display_zone(config)
    rows: list[dict[str, Any]] = []
    for name, window in config.sessions["windows"].items():
        start_minute = int(window["start_minute"])
        end_minute = int(window["end_minute"])
        start_ny = midnight + timedelta(minutes=start_minute)
        if start_minute >= end_minute:
            end_ny = (midnight + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=end_minute)
        else:
            end_ny = midnight + timedelta(minutes=end_minute)
        rows.append({
            "name": name,
            "kind": str(window["kind"]),
            "quality": float(window["quality"]),
            "nyStart": _hm(start_ny),
            "nyEnd": _hm(end_ny),
            "displayStart": _hm(start_ny.astimezone(display_zone)),
            "displayEnd": _hm(end_ny.astimezone(display_zone)),
        })
    return rows
