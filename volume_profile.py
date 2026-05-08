"""Previous-session fixed-range volume profile helpers for Engine B."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import logging
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

log = logging.getLogger("sentinel")

_TZ_LONDON = ZoneInfo("Europe/London")
_TZ_NEW_YORK = ZoneInfo("America/New_York")


def compute_bucketed_volume_profile(
    buckets: list[dict],
    value_area_pct: float = 0.70,
    lvn_threshold: float = 0.15,
) -> dict:
    """Compute VP levels from price-level trade buckets.

    Expected bucket keys: price_bucket, total_volume, delta, buy_volume,
    sell_volume. This is the live/backtest orderflow path; no candle range
    allocation is performed.
    """
    out = {
        "poc": None,
        "vah": None,
        "val": None,
        "profile_valid": False,
        "total_volume": 0.0,
        "bin_count": 0,
        "session_high": None,
        "session_low": None,
        "lvn_levels": [],
        "distribution": [],
        "delta": 0.0,
        "cvd_value": 0.0,
        "source": "trade_buckets",
    }
    parsed = []
    for row in buckets or []:
        try:
            price = float(row.get("price_bucket"))
            volume = float(row.get("total_volume", row.get("volume", 0)) or 0)
            delta = float(row.get("delta", 0) or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0 or volume <= 0:
            continue
        parsed.append((price, volume, delta))
    if not parsed:
        return out

    parsed.sort(key=lambda item: item[0])
    prices = [p for p, _, _ in parsed]
    volumes = [v for _, v, _ in parsed]
    deltas = [d for _, _, d in parsed]
    total_volume = float(sum(volumes))
    if total_volume <= 0:
        return out

    poc_idx = int(np.argmax(np.asarray(volumes, dtype=float)))
    target_volume = total_volume * float(max(0.1, min(0.95, value_area_pct)))
    included = {poc_idx}
    cumulative = float(volumes[poc_idx])
    left = poc_idx - 1
    right = poc_idx + 1
    while cumulative < target_volume and (left >= 0 or right < len(volumes)):
        left_vol = float(volumes[left]) if left >= 0 else -1.0
        right_vol = float(volumes[right]) if right < len(volumes) else -1.0
        if left_vol >= right_vol:
            idx = left
            left -= 1
        else:
            idx = right
            right += 1
        if 0 <= idx < len(volumes) and idx not in included:
            included.add(idx)
            cumulative += float(volumes[idx])

    poc_vol = float(volumes[poc_idx])
    low_idx = min(included)
    high_idx = max(included)
    lvn_cutoff = poc_vol * float(max(0.0, lvn_threshold))
    lvn_levels = [
        round(float(prices[i]), 10)
        for i in range(low_idx, high_idx + 1)
        if float(volumes[i]) < lvn_cutoff
    ]
    delta_total = float(sum(deltas))
    out.update(
        {
            "poc": round(float(prices[poc_idx]), 10),
            "vah": round(float(prices[high_idx]), 10),
            "val": round(float(prices[low_idx]), 10),
            "profile_valid": True,
            "total_volume": round(total_volume, 4),
            "bin_count": len(parsed),
            "session_high": round(float(max(prices)), 10),
            "session_low": round(float(min(prices)), 10),
            "lvn_levels": lvn_levels,
            "distribution": [round(float(v), 4) for v in volumes],
            "delta": round(delta_total, 4),
            "cvd_value": round(delta_total, 4),
        }
    )
    return out


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


def _normalize_session_mode(raw: Any) -> str:
    mode = str(raw or "london_ny").strip().lower()
    key = mode.replace("-", "_").replace(" ", "_").replace("/", "_")
    while "__" in key:
        key = key.replace("__", "_")
    aliases = {
        "ny": "new_york",
        "newyork": "new_york",
        "london_new_york": "london_ny",
        "londonny": "london_ny",
        "asia_london_new_york": "asia_london_ny",
        "crypto_major": "asia_london_ny",
        "all_major": "asia_london_ny",
    }
    return aliases.get(key, key)


def _session_label_for_ts(ts: datetime, asset_type: str, session_mode: str) -> tuple[str, str] | None:
    asset = str(asset_type or "").strip().lower()
    mode = _normalize_session_mode(session_mode)
    utc = ts.astimezone(timezone.utc)
    london = utc.astimezone(_TZ_LONDON)
    new_york = utc.astimezone(_TZ_NEW_YORK)

    def _in_window(local_dt: datetime, start_h: int, end_h: int) -> bool:
        minute = local_dt.hour * 60 + local_dt.minute
        return start_h * 60 <= minute < end_h * 60

    if mode in {"all", "any", "disabled", "off"}:
        return utc.date().isoformat(), "utc_day"

    if asset == "stock":
        if _in_window(new_york, 8, 16):
            return new_york.date().isoformat(), "new_york"
        return None

    if asset == "crypto":
        labels = []
        if mode in {"asia", "asia_london_ny"} and _in_window(utc, 1, 9):
            labels.append(("asia", utc.date().isoformat()))
        if mode in {"london", "london_ny", "asia_london_ny"} and _in_window(london, 7, 16):
            labels.append(("london", london.date().isoformat()))
        if mode in {"new_york", "london_ny", "asia_london_ny"} and _in_window(new_york, 8, 17):
            labels.append(("new_york", new_york.date().isoformat()))
        if not labels:
            return None
        session_date = labels[0][1]
        label = "asia_london_ny" if mode == "asia_london_ny" else labels[0][0]
        return session_date, label

    if mode == "london" and _in_window(london, 7, 16):
        return london.date().isoformat(), "london"
    if mode == "new_york" and _in_window(new_york, 8, 16):
        return new_york.date().isoformat(), "new_york"
    if mode in {"london_ny", "asia_london_ny"}:
        in_london = _in_window(london, 7, 16)
        in_ny = _in_window(new_york, 8, 16)
        if in_london or in_ny:
            return (london.date() if in_london else new_york.date()).isoformat(), "london_ny"
    return None


def split_completed_sessions(candles: list, asset_type: str, session_mode: str | None = None) -> dict:
    """Split candles into current session and immediately preceding completed session.

    Session boundaries are determined from the latest candle date in the provided
    dataset, not the wall clock, so historical backtests remain correct.
    """
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
            "prev_session_name": None,
            "current_session_name": None,
            "session_basis": "asset_session_window",
        }
    parsed.sort(key=lambda item: item[0])
    for ts, candle in parsed:
        session_key = _session_label_for_ts(ts, asset_type, session_mode or "london_ny")
        if session_key is None:
            continue
        grouped.setdefault(session_key, []).append(candle)

    session_basis = "asset_session_window"
    if not grouped:
        for ts, candle in parsed:
            grouped.setdefault((ts.date().isoformat(), "utc_day"), []).append(candle)
        session_basis = "utc_date_fallback"

    session_keys = list(grouped.keys())
    current_session_key = session_keys[-1]
    prev_session_key = session_keys[-2] if len(session_keys) >= 2 else None
    current_session_date, current_session_name = current_session_key
    prev_session_date, prev_session_name = prev_session_key if prev_session_key else (None, None)
    return {
        "prev_session_candles": list(grouped.get(prev_session_key, [])) if prev_session_key else [],
        "current_session_candles": list(grouped.get(current_session_key, [])),
        "prev_session_date": prev_session_date,
        "current_session_date": current_session_date,
        "prev_session_name": prev_session_name,
        "current_session_name": current_session_name,
        "session_basis": session_basis,
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
        "volume_source": "unknown",
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
    used_range_proxy = total_input_volume <= 0
    if used_range_proxy:
        # Fallback: use candle range as proxy volume (equal-weighted VP).
        # Forex / index MT5 data often has zero tick_volume on historical bars.
        log.debug("volume_profile: zero tick volume — using range proxy for VP")
        for candle in session_candles:
            high = _candle_num(candle, "high", "h")
            low = _candle_num(candle, "low", "l")
            if high is None or low is None:
                continue
            if high < low:
                high, low = low, high
            total_input_volume += max(float(high) - float(low), 1e-10)
        if total_input_volume <= 0:
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
            "volume_source": "range_proxy" if used_range_proxy else "candle_volume",
        })
        return out

    bins = int(max(8, bins))
    edges = np.linspace(session_low, session_high, bins + 1)
    volumes = np.zeros(bins, dtype=float)

    for candle in session_candles:
        high = _candle_num(candle, "high", "h")
        low = _candle_num(candle, "low", "l")
        vol = _candle_num(candle, "vol", "v") or 0.0
        if high is None or low is None:
            continue
        if high < low:
            high, low = low, high
        # Use candle range as proxy volume when tick volume is zero
        if vol <= 0:
            vol = max(float(high) - float(low), 1e-10)
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
        "volume_source": "range_proxy" if used_range_proxy else "candle_volume",
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
