"""Institutional session clock for FABLE (New York scoring clock, SAST display)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .config import FableConfig
from .structure import ny_zone


def _zone(config: FableConfig):
    sessions = config.sessions
    return ny_zone(str(sessions.get("timezone") or "America/New_York"), float(sessions.get("fallback_utc_offset_hours") or -4.0))


def _display_zone(config: FableConfig):
    sessions = config.sessions
    return ny_zone(
        str(sessions.get("display_timezone") or "Africa/Johannesburg"),
        float(sessions.get("display_fallback_utc_offset_hours") or 2.0),
    )


def ny_datetime(epoch: float, config: FableConfig) -> datetime:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone(_zone(config))


def display_datetime(epoch: float, config: FableConfig) -> datetime:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone(_display_zone(config))


def _minute_in_window(minute: int, start: int, end: int) -> bool:
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end  # wraps midnight


def _distance_to_window(minute: int, start: int, end: int) -> int:
    """Minutes from ``minute`` to the nearest edge of a window it is outside of."""
    candidates = []
    for edge in (start, end):
        delta = abs(minute - edge)
        candidates.append(min(delta, 1440 - delta))
    return min(candidates)


def session_state(epoch: float, config: FableConfig) -> dict[str, Any]:
    """Describe the active institutional window at ``epoch``.

    Returns the best window quality in force, the name of that window, and the
    fringe/off-window fallback qualities when nothing is active.
    """
    sessions = config.sessions
    local = ny_datetime(epoch, config)
    minute = local.hour * 60 + local.minute
    best_name: str | None = None
    best_quality = -1.0
    windows = sessions["windows"]
    for name, window in windows.items():
        if _minute_in_window(minute, int(window["start_minute"]), int(window["end_minute"])):
            quality = float(window["quality"])
            if quality > best_quality:
                best_quality = quality
                best_name = str(name)
    fringe = False
    if best_name is None:
        fringe_minutes = int(sessions.get("fringe_minutes") or 0)
        nearest = None
        for name, window in windows.items():
            distance = _distance_to_window(minute, int(window["start_minute"]), int(window["end_minute"]))
            if float(window["quality"]) >= 0.5 and distance <= fringe_minutes:
                if nearest is None or distance < nearest[0]:
                    nearest = (distance, str(name))
        if nearest is not None:
            fringe = True
            best_name = nearest[1]
            best_quality = float(sessions.get("fringe_quality") or 0.0)
        else:
            best_quality = float(sessions.get("off_window_quality") or 0.0)
    # Weekend closure comes from the configured NY-clock window, not a
    # hardcoded Friday 17:00 rule.
    weekend = any(close_at <= float(epoch) < open_at for close_at, open_at in _weekend_windows(epoch, epoch, config))
    display = display_datetime(epoch, config)
    return {
        "nyClock": local.strftime("%a %H:%M"),
        "displayClock": display.strftime("%a %H:%M"),
        "displayTimezone": str(sessions.get("display_timezone")),
        "window": best_name,
        "quality": round(max(0.0, best_quality), 4),
        "fringe": fringe,
        "weekend": weekend,
        "minuteOfDay": minute,
    }


def _weekend_gated(asset_type: str, config: FableConfig) -> bool:
    gated = config.sessions.get("apply_weekend_gate_to") or []
    return str(asset_type or "").strip().lower() in {str(item).strip().lower() for item in gated}


def _weekend_windows(start: float, end: float, config: FableConfig) -> list[tuple[float, float]]:
    """Weekend closures (NY clock) that overlap ``[start, end]`` as (close_epoch, open_epoch)."""
    sessions = config.sessions
    zone = _zone(config)
    close_weekday = int(sessions.get("weekend_close_weekday", 4))
    close_hour = int(sessions.get("weekend_close_hour", 17))
    open_weekday = int(sessions.get("weekend_open_weekday", 6))
    open_hour = int(sessions.get("weekend_open_hour", 17))
    span_days = (open_weekday - close_weekday) % 7 or 7
    first = datetime.fromtimestamp(float(start), tz=timezone.utc).astimezone(zone).date() - timedelta(days=7)
    last = datetime.fromtimestamp(float(end), tz=timezone.utc).astimezone(zone).date() + timedelta(days=1)
    windows: list[tuple[float, float]] = []
    day = first
    while day <= last:
        if day.weekday() == close_weekday:
            close_at = datetime(day.year, day.month, day.day, close_hour, tzinfo=zone)
            open_day = day + timedelta(days=span_days)
            open_at = datetime(open_day.year, open_day.month, open_day.day, open_hour, tzinfo=zone)
            windows.append((close_at.timestamp(), open_at.timestamp()))
        day += timedelta(days=1)
    return windows


def _holiday_keys(config: FableConfig) -> tuple[set[str], set[str]]:
    """(recurring MM-DD keys, explicit ISO dates) of full-day NY-calendar closures."""
    holidays = config.sessions.get("holidays") or {}
    if not isinstance(holidays, dict):
        return set(), set()
    recurring = {str(item) for item in (holidays.get("recurring") or [])}
    dates = {str(item) for item in (holidays.get("dates") or [])}
    return recurring, dates


def _closed_intervals(start: float, end: float, config: FableConfig) -> list[tuple[float, float]]:
    """Weekend and holiday closures overlapping ``[start, end]`` (unmerged)."""
    intervals = list(_weekend_windows(start, end, config))
    recurring, dates = _holiday_keys(config)
    if recurring or dates:
        zone = _zone(config)
        day = datetime.fromtimestamp(float(start), tz=timezone.utc).astimezone(zone).date() - timedelta(days=1)
        last = datetime.fromtimestamp(float(end), tz=timezone.utc).astimezone(zone).date() + timedelta(days=1)
        while day <= last:
            if day.isoformat() in dates or day.strftime("%m-%d") in recurring:
                day_start = datetime(day.year, day.month, day.day, tzinfo=zone)
                intervals.append((day_start.timestamp(), (day_start + timedelta(days=1)).timestamp()))
            day += timedelta(days=1)
    return intervals


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[list[float]]:
    merged: list[list[float]] = []
    for low, high in sorted(intervals):
        if merged and low <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([low, high])
    return merged


def market_closed_seconds(start: float, end: float, asset_type: str, config: FableConfig) -> float:
    """Seconds inside weekend/holiday closures between ``start`` and ``end`` for weekend-gated assets.

    Weekend windows and holiday days are merged into one union first, so a
    holiday that falls on a weekend (or spans one) is counted exactly once.
    """
    if end <= start or not _weekend_gated(asset_type, config):
        return 0.0
    closed = 0.0
    for low, high in _merge_intervals(_closed_intervals(start, end, config)):
        overlap = min(end, high) - max(start, low)
        if overlap > 0:
            closed += overlap
    return closed


def market_is_closed(epoch: float, asset_type: str, config: FableConfig) -> tuple[bool, str | None]:
    """Weekend/holiday closure check for weekend-gated assets. Crypto and ungated types are always open."""
    if not _weekend_gated(asset_type, config):
        return False, None
    epoch = float(epoch)
    for close_at, open_at in _weekend_windows(epoch, epoch, config):
        if close_at <= epoch < open_at:
            return True, "WEEKEND"
    local_date = datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(_zone(config)).date()
    recurring, dates = _holiday_keys(config)
    if local_date.isoformat() in dates or local_date.strftime("%m-%d") in recurring:
        return True, "HOLIDAY"
    return False, None


def window_schedule(epoch: float, config: FableConfig) -> list[dict[str, Any]]:
    """Today's windows rendered in the display timezone for the UI."""
    local = ny_datetime(epoch, config)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    display_zone = _display_zone(config)
    rows: list[dict[str, Any]] = []
    for name, window in config.sessions["windows"].items():
        start = midnight + timedelta(minutes=int(window["start_minute"]))
        end = midnight + timedelta(minutes=int(window["end_minute"]))
        if int(window["start_minute"]) >= int(window["end_minute"]):
            end += timedelta(days=1)
        rows.append(
            {
                "name": str(name),
                "quality": float(window["quality"]),
                "startNy": start.strftime("%H:%M"),
                "endNy": end.strftime("%H:%M"),
                "startDisplay": start.astimezone(display_zone).strftime("%H:%M"),
                "endDisplay": end.astimezone(display_zone).strftime("%H:%M"),
                "active": start <= local < end,
            }
        )
    rows.sort(key=lambda row: row["startNy"])
    return rows
