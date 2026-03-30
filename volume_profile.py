"""Previous-session fixed-range volume profile helpers for Engine B."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import logging
from typing import Any

import numpy as np

log = logging.getLogger("sentinel")


def _parse_utc_timestamp(raw: Any) -> datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, (int, float)):
        stamp = float(raw)
        if abs(stamp) > 1_000_000_000_000:
            stamp /= 1000.0
        try:
            dt = datetime.fromtimestamp(stamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = str(raw).strip()
        if not text:
            return None
        text = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                dt = datetime.fromisoformat(text.replace(" ", "T"))
            except ValueError:
                return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _candle_time(candle: dict) -> datetime | None:
    return _parse_utc_timestamp(candle.get("t", candle.get("time")))


def _candle_num(candle: dict, *keys: str) -> float | None:
    for key in keys:
        value = candle.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def split_completed_sessions(candles: list, asset_type: str) -> dict:
    """Split candles into current session and immediately preceding completed session.

    Session boundaries are determined from the latest candle date in the provided
    dataset, not the wall clock, so historical backtests remain correct.
    """
    _ = asset_type
    grouped: OrderedDict = OrderedDict()
    parsed = []
    for candle in candles or []:
        ts = _candle_time(candle)
        if ts is None:
            continue
        parsed.append((ts, candle))
    if not parsed:
        return {
            "prev_session_candles": [],
            "current_session_candles": [],
            "prev_session_date": None,
            "current_session_date": None,
        }
    parsed.sort(key=lambda item: item[0])
    for ts, candle in parsed:
        grouped.setdefault(ts.date(), []).append(candle)

    session_dates = list(grouped.keys())
    current_session_date = session_dates[-1]
    prev_session_date = session_dates[-2] if len(session_dates) >= 2 else None
    return {
        "prev_session_candles": list(grouped.get(prev_session_date, [])) if prev_session_date else [],
        "current_session_candles": list(grouped.get(current_session_date, [])),
        "prev_session_date": prev_session_date.isoformat() if prev_session_date else None,
        "current_session_date": current_session_date.isoformat() if current_session_date else None,
    }


def compute_fixed_range_volume_profile(
    session_candles: list,
    bins: int = 64,
    value_area_pct: float = 0.70,
) -> dict:
    """Compute previous-session POC/VAH/VAL from candle OHLCV."""
    out = {
        "poc": None,
        "vah": None,
        "val": None,
        "profile_valid": False,
        "total_volume": 0.0,
        "bin_count": int(max(1, bins)),
        "session_high": None,
        "session_low": None,
        "session_start": None,
        "session_end": None,
    }
    if not session_candles:
        return out

    parsed_times = [ts for ts in (_candle_time(c) for c in session_candles) if ts is not None]
    if parsed_times:
        out["session_start"] = min(parsed_times).isoformat()
        out["session_end"] = max(parsed_times).isoformat()

    highs = []
    lows = []
    total_input_volume = 0.0
    for candle in session_candles:
        high = _candle_num(candle, "high", "h")
        low = _candle_num(candle, "low", "l")
        vol = _candle_num(candle, "vol", "v") or 0.0
        if high is None or low is None:
            continue
        if high < low:
            high, low = low, high
        highs.append(high)
        lows.append(low)
        if vol > 0:
            total_input_volume += vol

    if not highs or not lows:
        return out

    out["session_high"] = max(highs)
    out["session_low"] = min(lows)
    if total_input_volume <= 0:
        log.debug("volume_profile: no usable volume for session profile")
        return out

    session_high = float(out["session_high"])
    session_low = float(out["session_low"])
    if session_high == session_low:
        out.update({
            "poc": session_low,
            "vah": session_low,
            "val": session_low,
            "profile_valid": True,
            "total_volume": round(total_input_volume, 4),
            "bin_count": 1,
        })
        return out

    bins = int(max(8, bins))
    edges = np.linspace(session_low, session_high, bins + 1)
    volumes = np.zeros(bins, dtype=float)

    for candle in session_candles:
        high = _candle_num(candle, "high", "h")
        low = _candle_num(candle, "low", "l")
        vol = _candle_num(candle, "vol", "v") or 0.0
        if high is None or low is None or vol <= 0:
            continue
        if high < low:
            high, low = low, high
        if high == low:
            idx = int(np.searchsorted(edges, high, side="right") - 1)
            idx = max(0, min(bins - 1, idx))
            volumes[idx] += vol
            continue

        candle_range = float(high - low)
        overlap_total = 0.0
        for idx in range(bins):
            overlap = max(0.0, min(high, edges[idx + 1]) - max(low, edges[idx]))
            if overlap <= 0:
                continue
            share = vol * (overlap / candle_range)
            volumes[idx] += share
            overlap_total += share

        if overlap_total <= 0:
            mid = (high + low) / 2.0
            idx = int(np.searchsorted(edges, mid, side="right") - 1)
            idx = max(0, min(bins - 1, idx))
            volumes[idx] += vol

    total_volume = float(volumes.sum())
    if total_volume <= 0:
        log.debug("volume_profile: usable volume collapsed to zero after binning")
        return out

    poc_idx = int(np.argmax(volumes))
    target_volume = total_volume * float(max(0.1, min(0.95, value_area_pct)))
    included = {poc_idx}
    cumulative = float(volumes[poc_idx])
    left = poc_idx - 1
    right = poc_idx + 1
    while cumulative < target_volume and (left >= 0 or right < bins):
        left_vol = float(volumes[left]) if left >= 0 else -1.0
        right_vol = float(volumes[right]) if right < bins else -1.0
        if left_vol >= right_vol:
            include_idx = left
            left -= 1
        else:
            include_idx = right
            right += 1
        if include_idx < 0 or include_idx >= bins:
            continue
        if include_idx in included:
            continue
        included.add(include_idx)
        cumulative += float(volumes[include_idx])

    low_idx = min(included)
    high_idx = max(included)
    out.update({
        "poc": round(float((edges[poc_idx] + edges[poc_idx + 1]) / 2.0), 6),
        "vah": round(float(edges[high_idx + 1]), 6),
        "val": round(float(edges[low_idx]), 6),
        "profile_valid": True,
        "total_volume": round(total_volume, 4),
        "bin_count": bins,
    })
    return out


def classify_profile_interaction(
    current_price: float,
    recent_candles: list,
    direction: str,
    poc: float,
    vah: float,
    val: float,
    atr: float,
) -> dict:
    """Classify objective interaction between price and prior-session profile levels."""
    lookback = [c for c in (recent_candles or []) if c]
    if not lookback:
        lookback = []
    tail = lookback[-5:]
    atr_val = float(atr or 0.0)
    if atr_val <= 0:
        atr_val = max(abs(float(vah) - float(val)) * 0.1, 1e-6)

    last_close = _candle_num(lookback[-1], "close", "c") if lookback else float(current_price or 0.0)
    prev_close = _candle_num(lookback[-2], "close", "c") if len(lookback) >= 2 else last_close

    def _touch_info(level: float) -> tuple[bool, int | None]:
        touched = False
        last_idx = None
        for idx, candle in enumerate(tail):
            low = _candle_num(candle, "low", "l")
            high = _candle_num(candle, "high", "h")
            if low is None or high is None:
                continue
            if high < low:
                high, low = low, high
            if low <= level <= high:
                touched = True
                last_idx = idx
        return touched, last_idx

    def _rejected_from(level: float, touched: bool, touch_idx: int | None) -> bool:
        if not touched or touch_idx is None or not lookback:
            return False
        global_touch_idx = len(lookback) - len(tail) + touch_idx
        ref_idx = max(0, global_touch_idx - 1)
        ref_close = _candle_num(lookback[ref_idx], "close", "c")
        if ref_close is None or last_close is None:
            return False
        move_min = 0.1 * atr_val
        approached_from_above = ref_close > level
        approached_from_below = ref_close < level
        if approached_from_above and last_close > level and abs(last_close - level) > move_min:
            return True
        if approached_from_below and last_close < level and abs(last_close - level) > move_min:
            return True
        return False

    touched_poc, poc_idx = _touch_info(float(poc))
    touched_vah, vah_idx = _touch_info(float(vah))
    touched_val, val_idx = _touch_info(float(val))

    rejected_from_poc = _rejected_from(float(poc), touched_poc, poc_idx)
    rejected_from_vah = _rejected_from(float(vah), touched_vah, vah_idx)
    rejected_from_val = _rejected_from(float(val), touched_val, val_idx)

    inside_prev_value_area = float(val) <= float(current_price) <= float(vah)
    above_prev_value_area = float(current_price) > float(vah)
    below_prev_value_area = float(current_price) < float(val)
    accepted_at_poc = bool(touched_poc and last_close is not None and abs(float(last_close) - float(poc)) <= (0.15 * atr_val))

    recent_closes = [
        close for close in (_candle_num(candle, "close", "c") for candle in lookback[-2:]) if close is not None
    ]
    accepted_inside_value = bool(
        recent_closes
        and all(float(val) <= float(close) <= float(vah) for close in recent_closes)
    )
    returned_to_value = bool(
        prev_close is not None
        and last_close is not None
        and (
            (float(prev_close) > float(vah) and float(val) <= float(last_close) <= float(vah))
            or (float(prev_close) < float(val) and float(val) <= float(last_close) <= float(vah))
        )
    )
    failed_return_to_value = bool(
        prev_close is not None
        and last_close is not None
        and (
            (float(prev_close) > float(vah) and touched_vah and float(last_close) > float(vah))
            or (float(prev_close) < float(val) and touched_val and float(last_close) < float(val))
        )
    )

    touch_priority = {"POC": 2, "VAH": 1, "VAL": 0}
    touches = []
    if touched_poc:
        touches.append((poc_idx if poc_idx is not None else -1, touch_priority["POC"], "POC"))
    if touched_vah:
        touches.append((vah_idx if vah_idx is not None else -1, touch_priority["VAH"], "VAH"))
    if touched_val:
        touches.append((val_idx if val_idx is not None else -1, touch_priority["VAL"], "VAL"))
    profile_level_in_play = None
    if touches:
        touches.sort(key=lambda item: (item[0], item[1]), reverse=True)
        profile_level_in_play = touches[0][2]

    profile_in_play = bool(touched_poc or touched_vah or touched_val)
    direction_key = str(direction or "").upper()
    if above_prev_value_area or (inside_prev_value_area and direction_key == "LONG" and accepted_at_poc):
        profile_bias = "bullish"
    elif below_prev_value_area or (inside_prev_value_area and direction_key == "SHORT" and accepted_at_poc):
        profile_bias = "bearish"
    else:
        profile_bias = "neutral"

    if rejected_from_poc:
        profile_reaction_strength = 1.0
    elif rejected_from_vah or rejected_from_val:
        profile_reaction_strength = 0.8
    elif accepted_at_poc:
        profile_reaction_strength = 0.4
    elif inside_prev_value_area:
        profile_reaction_strength = 0.2
    else:
        profile_reaction_strength = 0.0

    if rejected_from_poc:
        profile_notes = "Rejected from previous-session POC"
    elif rejected_from_vah:
        profile_notes = "Rejected from previous-session VAH"
    elif rejected_from_val:
        profile_notes = "Rejected from previous-session VAL"
    elif returned_to_value:
        profile_notes = "Returned to previous-session value area"
    elif failed_return_to_value:
        profile_notes = "Failed return to previous-session value area"
    elif accepted_at_poc:
        profile_notes = "Accepted at previous-session POC"
    elif inside_prev_value_area:
        profile_notes = "Rotating inside previous-session value area"
    elif above_prev_value_area:
        profile_notes = "Trading above previous-session value area"
    elif below_prev_value_area:
        profile_notes = "Trading below previous-session value area"
    else:
        profile_notes = "Previous-session profile not in play"

    return {
        "profile_in_play": profile_in_play,
        "profile_level_in_play": profile_level_in_play,
        "inside_prev_value_area": inside_prev_value_area,
        "above_prev_value_area": above_prev_value_area,
        "below_prev_value_area": below_prev_value_area,
        "touched_poc": touched_poc,
        "touched_vah": touched_vah,
        "touched_val": touched_val,
        "rejected_from_poc": rejected_from_poc,
        "rejected_from_vah": rejected_from_vah,
        "rejected_from_val": rejected_from_val,
        "accepted_at_poc": accepted_at_poc,
        "accepted_inside_value": accepted_inside_value,
        "returned_to_value": returned_to_value,
        "failed_return_to_value": failed_return_to_value,
        "profile_bias": profile_bias,
        "profile_reaction_strength": round(float(profile_reaction_strength), 2),
        "profile_notes": profile_notes,
    }
