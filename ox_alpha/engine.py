"""OX Alpha scoring core — pure, side-effect free.

Inputs are market states (per-timeframe confirmed/forming candle splits), an
optional live quote, the group profile, and settings. Output is a complete
signal dict. No config import, no feed access, no broker access.

The composite blends six orthogonal axes:

  kinetic   signed  volatility-normalized momentum cascade (M15 x H1)
  spring    0..1    compression depth + expansion ignition
  flow      signed  effort-vs-result: volume thrust x path efficiency
  velocity  0..1    session-hour travel vs the pair's own hourly baseline
  magnet    signed  location vs session/round-number liquidity magnets
  structure signed  M15 pivot sequence + last break direction

Conviction (direction quality) and opportunity (environment quality) are
weighted by the group profile, combined into one 0..100 score, then passed
through deterministic fail-closed gates.
"""
from __future__ import annotations

import math
import time as _time
from typing import Any, Mapping, Sequence

from ox_alpha.config import OxSettings
from ox_alpha.indicators import (
    clamp01,
    closes,
    hour_of_day_range_profile,
    path_efficiency,
    percentile_rank,
    round_number_levels,
    roc,
    swing_pivots,
    tanh_scale,
    true_ranges,
    volumes,
    wilder_atr,
    zscore,
)
from ox_alpha.profiles import OxProfile

Candle = dict[str, Any]
MarketStates = Mapping[str, Mapping[str, Any]]

_FAST_TF = "M15"
_SLOW_TF = "H1"


def _confirmed(states: MarketStates, tf: str) -> list[Candle]:
    state = states.get(tf) or {}
    confirmed = state.get("confirmed")
    return list(confirmed) if isinstance(confirmed, Sequence) else []


def _f(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _atr_pct(candles: Sequence[Candle], period: int = 14) -> tuple[float | None, float | None]:
    atr = wilder_atr(candles, period)
    cl = closes(candles)
    if atr is None or not cl:
        return None, None
    last = cl[-1]
    if last <= 0:
        return None, atr
    return atr / last, atr


def _component_kinetic(m15: list[Candle], h1: list[Candle]) -> float | None:
    fast_atr_pct, _ = _atr_pct(m15)
    slow_atr_pct, _ = _atr_pct(h1)
    cl_fast = closes(m15)
    cl_slow = closes(h1)
    if fast_atr_pct is None or slow_atr_pct is None:
        return None
    r_fast = roc(cl_fast, 8)
    r_slow = roc(cl_slow, 6)
    if r_fast is None or r_slow is None:
        return None
    k_fast = tanh_scale(r_fast / max(fast_atr_pct, 1e-9), 1.4)
    k_slow = tanh_scale(r_slow / max(slow_atr_pct, 1e-9), 1.2)
    return max(-1.0, min(1.0, 0.62 * k_fast + 0.38 * k_slow))


def _component_spring(m15: list[Candle]) -> float | None:
    trs = true_ranges(m15)
    atr = wilder_atr(m15, 14)
    if atr is None or atr <= 0 or len(trs) < 30:
        return None
    window = trs[-96:]
    ratios = [tr / atr for tr in window]
    last_ratio = ratios[-1]
    rank = percentile_rank(ratios[:-1], last_ratio)
    if rank is None:
        return None
    compression = clamp01(1.0 - rank)
    ignition = 1.0 if last_ratio >= 1.3 else 0.0
    return clamp01(0.7 * compression + 0.3 * ignition)


def _component_flow(
    m15: list[Candle], lookback: int, volume_axis: str
) -> tuple[float | None, bool]:
    """Returns (flow, volume_used). flow is None only when data is unusable."""
    eff = path_efficiency(m15, 12)
    if eff is None:
        return None, False
    vols = volumes(m15)
    if len(vols) >= lookback // 2:
        vz = zscore(vols, lookback)
        if vz is not None:
            thrust = tanh_scale(vz / 2.5, 1.0)
            return max(-1.0, min(1.0, thrust * eff)), True
    if volume_axis == "required":
        # Volume promised by profile but absent: axis abstains rather than
        # inventing participation evidence.
        return None, False
    # No usable volume: efficiency alone carries a damped flow signal.
    return max(-1.0, min(1.0, 0.6 * eff)), False


def _component_velocity(
    m15: list[Candle], h1: list[Candle], time_now: float | None
) -> float | None:
    profile = hour_of_day_range_profile(h1, 3600)
    if not profile:
        return None
    ts = _f(m15[-1].get("time")) if m15 else None
    if ts is None:
        ts = time_now if time_now is not None else _time.time()
    hour = int((float(ts) // 3600) % 24)
    expected_hourly = profile.get(hour)
    if not expected_hourly or expected_hourly <= 0:
        return 0.35  # known-quiet hour for this pair's own fingerprint
    # Baseline is per-H1-bar travel; normalize to the M15 bucket scale.
    expected = expected_hourly * 0.25
    recent = m15[-4:]
    travels = []
    for c in recent:
        h = _f(c.get("high"))
        l = _f(c.get("low"))
        cl = _f(c.get("close"))
        if h is None or l is None or cl is None or cl <= 0:
            continue
        travels.append((h - l) / cl)
    if not travels:
        return None
    actual = sum(travels) / len(travels)
    return clamp01(actual / (expected * 1.25))


def _session_magnets(
    m15: list[Candle], price: float, atr: float | None
) -> dict[str, Any]:
    """Session range extremes, prior-day extremes, and round numbers."""
    day_key = lambda ts: int(float(ts) // 86400)
    current_day = day_key(_f(m15[-1].get("time")) or 0)
    session_hi = -math.inf
    session_lo = math.inf
    per_day: dict[int, list[Candle]] = {}
    for c in m15[-576:]:
        ts = _f(c.get("time"))
        if ts is None:
            continue
        per_day.setdefault(day_key(ts), []).append(c)
        if day_key(ts) == current_day:
            h = _f(c.get("high"))
            l = _f(c.get("low"))
            if h is not None:
                session_hi = max(session_hi, h)
            if l is not None:
                session_lo = min(session_lo, l)
    prior_hi = prior_lo = prior_close = None
    prior_days = sorted(d for d in per_day if d < current_day)
    if prior_days:
        latest_prior = prior_days[-1]
        ph, pl = -math.inf, math.inf
        pc = None
        for c in per_day[latest_prior]:
            h = _f(c.get("high"))
            l = _f(c.get("low"))
            cl = _f(c.get("close"))
            if h is not None:
                ph = max(ph, h)
            if l is not None:
                pl = min(pl, l)
            if cl is not None:
                pc = cl
        prior_hi = ph if ph != -math.inf else None
        prior_lo = pl if pl != math.inf else None
        prior_close = pc
    levels = [x for x in (session_hi, session_lo, prior_hi, prior_lo, prior_close)
              if x is not None and math.isfinite(x)]
    if price > 0 and (atr is None or atr > 0):
        levels.extend(round_number_levels(price))
    return {
        "sessionHigh": session_hi if session_hi != -math.inf else None,
        "sessionLow": session_lo if session_lo != math.inf else None,
        "priorDayHigh": prior_hi,
        "priorDayLow": prior_lo,
        "priorDayClose": prior_close,
        "levels": sorted(set(round(x, 10) for x in levels)),
    }


def _component_magnet(magnets: dict[str, Any], price: float) -> float | None:
    hi = magnets.get("sessionHigh")
    lo = magnets.get("sessionLow")
    if hi is None or lo is None or hi <= lo or price <= 0:
        return None
    mid = (hi + lo) / 2.0
    half = (hi - lo) / 2.0
    return max(-1.0, min(1.0, (price - mid) / half))


def _component_structure(m15: list[Candle]) -> float | None:
    highs, lows = swing_pivots(m15, 2, 2)
    if len(highs) < 2 or len(lows) < 2:
        return None
    hh = highs[-1][1] > highs[-2][1]
    hl = lows[-1][1] > lows[-2][1]
    lh = highs[-1][1] < highs[-2][1]
    ll = lows[-1][1] < lows[-2][1]
    seq = 0.0
    if hh and hl:
        seq = 1.0
    elif lh and ll:
        seq = -1.0
    cl = closes(m15)
    if not cl:
        return seq
    last_close = cl[-1]
    break_boost = 0.0
    if highs and last_close > highs[-1][1]:
        break_boost = 0.4
    elif lows and last_close < lows[-1][1]:
        break_boost = -0.4
    return max(-1.0, min(1.0, seq + break_boost))


def _resolve_direction(
    components: dict[str, float | None], profile: OxProfile
) -> tuple[str | None, float]:
    signed_axes = ("kinetic", "flow", "structure", "magnet")
    weights = {
        "kinetic": profile.w_kinetic,
        "flow": profile.w_flow,
        "structure": profile.w_structure,
        "magnet": profile.w_magnet * 0.5,  # location is context, not a vote equal to trend
    }
    net = 0.0
    total = 0.0
    for axis in signed_axes:
        value = components.get(axis)
        weight = weights[axis]
        if value is None:
            continue
        net += weight * value
        total += weight
    if total <= 0:
        return None, 0.0
    share = abs(net) / total
    direction = "LONG" if net > 0 else "SHORT" if net < 0 else None
    if share < profile.direction_agreement:
        return None, share
    return direction, share


def _targets_for_direction(
    direction: str,
    entry: float,
    sl: float,
    magnets: dict[str, Any],
    atr: float,
    profile: OxProfile,
    settings: OxSettings,
) -> tuple[float | None, float | None, float | None]:
    sl_dist = abs(entry - sl)
    if sl_dist <= 0 or entry <= 0:
        return None, None, None
    sign = 1.0 if direction == "LONG" else -1.0
    candidates = [
        lvl for lvl in magnets.get("levels") or []
        if (lvl - entry) * sign > 0
    ]
    tp1 = None
    for lvl in candidates:
        dist = abs(lvl - entry)
        if dist >= profile.min_rr * sl_dist:
            tp1 = lvl
            break
    fallback_dist = max(
        profile.tp_atr_fallback * atr,
        profile.min_rr * sl_dist,
    )
    fallback_tp = entry + sign * fallback_dist
    if tp1 is None:
        tp1 = fallback_tp
    tp1_dist = abs(tp1 - entry)
    tp2_dist = tp1_dist * settings.tp2_rr_extension
    beyond = [lvl for lvl in candidates if abs(lvl - entry) > tp1_dist]
    tp2 = None
    for lvl in beyond:
        if abs(lvl - entry) >= tp2_dist:
            tp2 = lvl
            break
    if tp2 is None:
        tp2 = entry + sign * tp2_dist
    rr1 = tp1_dist / sl_dist
    return round(tp1, 10), round(tp2, 10), round(rr1, 3)


def _stop_for_direction(
    direction: str,
    m15: list[Candle],
    atr: float,
    profile: OxProfile,
    settings: OxSettings,
) -> float | None:
    cl = closes(m15)
    if not cl:
        return None
    entry = cl[-1]
    _, lows_piv = swing_pivots(m15[-60:], 2, 2)
    highs_piv = swing_pivots(m15[-60:], 2, 2)[0]
    buffer = settings.sl_buffer_atr * atr
    if direction == "LONG":
        swing = min((p for _, p in lows_piv), default=None)
        raw = swing if swing is not None and swing < entry else entry - profile.sl_atr_min * atr
        dist = entry - raw + buffer
        dist = max(profile.sl_atr_min * atr, min(dist, profile.sl_atr_max * atr))
        return entry - dist
    swing = max((p for _, p in highs_piv), default=None)
    raw = swing if swing is not None and swing > entry else entry + profile.sl_atr_min * atr
    dist = raw - entry + buffer
    dist = max(profile.sl_atr_min * atr, min(dist, profile.sl_atr_max * atr))
    return entry + dist


def analyze_pair(
    pair: Mapping[str, Any],
    states: MarketStates,
    *,
    profile: OxProfile,
    settings: OxSettings,
    freshness: Mapping[str, bool] | None = None,
    quote: Mapping[str, Any] | None = None,
    time_now: float | None = None,
) -> dict[str, Any]:
    """Score one pair. Always returns a signal dict; bad data yields NO_SIGNAL."""
    display = str(pair.get("display") or pair.get("symbol") or "")
    now_s = time_now if time_now is not None else _time.time()
    signal: dict[str, Any] = {
        "engine": "OX_ALPHA",
        "engineLabel": "OX Alpha",
        "oxVersion": "1.0.0",
        "symbol": pair.get("symbol") or display,
        "display": display,
        "type": pair.get("type"),
        "source": pair.get("source"),
        "scoreGroup": pair.get("score_group") or pair.get("scoreGroup"),
        "profile": profile.group,
        "timestamp": now_s,
        "decision": "NO_SIGNAL",
        "direction": None,
        "playType": None,
        "score": 0.0,
        "gates": [],
        "reasons": [],
    }

    m15 = _confirmed(states, _FAST_TF)
    h1 = _confirmed(states, _SLOW_TF)

    gates: list[dict[str, Any]] = signal["gates"]
    reasons: list[str] = signal["reasons"]

    def _gate(name: str, passed: bool, detail: str = "", hard: bool = True) -> bool:
        gates.append({"name": name, "passed": bool(passed), "detail": detail, "hard": hard})
        return bool(passed)

    fresh_map = dict(freshness or {})
    if fresh_map:
        bad_tf = sorted(tf for tf, ok in fresh_map.items() if not ok)
        _gate("freshness", not bad_tf, ",".join(bad_tf) if bad_tf else "all_required_fresh")

    bars_m15_ok = len(m15) >= settings.min_bars_m15
    bars_h1_ok = len(h1) >= settings.min_bars_h1
    _gate("bars_m15", bars_m15_ok, f"{len(m15)}")
    _gate("bars_h1", bars_h1_ok, f"{len(h1)}")
    if not (bars_m15_ok and bars_h1_ok):
        signal["reason"] = "insufficient_history"
        return signal

    atr_pct_m15, atr_m15 = _atr_pct(m15)
    if atr_m15 is None or atr_pct_m15 is None:
        signal["reason"] = "atr_unavailable"
        return signal
    _gate(
        "atr_sane",
        settings.atr_pct_min <= atr_pct_m15 <= settings.atr_pct_max,
        f"atrPct={atr_pct_m15:.5f}",
    )

    kinetic = _component_kinetic(m15, h1)
    spring = _component_spring(m15)
    flow, volume_used = _component_flow(m15, settings.volume_lookback, profile.volume_axis)
    velocity = _component_velocity(m15, h1, now_s)

    quote_price = None
    if isinstance(quote, Mapping):
        bid = _f(quote.get("bid"))
        ask = _f(quote.get("ask"))
        last = _f(quote.get("last") or quote.get("price"))
        if bid and ask and ask >= bid:
            quote_price = (bid + ask) / 2.0
        elif last and last > 0:
            quote_price = last
    entry_ref = quote_price or closes(m15)[-1]

    magnets = _session_magnets(m15, entry_ref, atr_m15)
    magnet = _component_magnet(magnets, entry_ref)
    structure = _component_structure(m15)

    components: dict[str, float | None] = {
        "kinetic": kinetic,
        "spring": spring,
        "flow": flow,
        "velocity": velocity,
        "magnet": magnet,
        "structure": structure,
    }

    # Reweight when the flow axis cannot speak for this group.
    weights = {
        "kinetic": profile.w_kinetic,
        "spring": profile.w_spring,
        "flow": profile.w_flow,
        "velocity": profile.w_velocity,
        "magnet": profile.w_magnet,
        "structure": profile.w_structure,
    }
    if flow is None:
        forfeit = weights.pop("flow", 0.0)
        if forfeit > 0:
            weights["kinetic"] += forfeit * 0.6
            weights["structure"] += forfeit * 0.4
    weight_total = sum(weights.values())
    if weight_total <= 0:
        signal["reason"] = "no_usable_components"
        return signal

    direction, agreement_share = _resolve_direction(components, profile)
    signal["directionAgreement"] = round(agreement_share, 3)
    dir_gate_passed = _gate(
        "direction_resolved",
        direction is not None,
        f"share={agreement_share:.2f} need={profile.direction_agreement:.2f}",
    )
    if direction is None:
        signal["components"] = {k: (None if v is None else round(v, 4)) for k, v in components.items()}
        signal["reason"] = "direction_unresolved"
        return signal

    side = 1.0 if direction == "LONG" else -1.0
    conviction_num = 0.0
    conviction_den = 0.0
    for axis in ("kinetic", "flow", "structure"):
        value = components.get(axis)
        if value is None or axis not in weights:
            continue
        conviction_num += weights[axis] * clamp01((value * side) / 2.0 + 0.5)
        conviction_den += weights[axis]
    conviction = conviction_num / conviction_den if conviction_den > 0 else 0.0

    opportunity_num = 0.0
    opportunity_den = 0.0
    for axis in ("spring", "velocity"):
        value = components.get(axis)
        if value is None or axis not in weights:
            continue
        opportunity_num += weights[axis] * value
        opportunity_den += weights[axis]
    room_up = room_down = None
    hi = magnets.get("sessionHigh")
    lo = magnets.get("sessionLow")
    if hi is not None and lo is not None and hi > lo:
        room_up = max(0.0, hi - entry_ref)
        room_down = max(0.0, entry_ref - lo)
    room = room_up if direction == "LONG" else room_down
    if room is not None and atr_m15 > 0:
        room_score = clamp01(tanh_scale(room / (3.0 * atr_m15), 1.0))
        opportunity_num += weights.get("magnet", 0.0) * room_score
        opportunity_den += weights.get("magnet", 0.0)
    opportunity = opportunity_num / opportunity_den if opportunity_den > 0 else 0.0

    score = 100.0 * (0.62 * conviction + 0.38 * opportunity)
    signal["convictionPct"] = round(conviction * 100.0, 1)
    signal["opportunityPct"] = round(opportunity * 100.0, 1)
    signal["components"] = {k: (None if v is None else round(v, 4)) for k, v in components.items()}
    signal["volumeAxisUsed"] = bool(volume_used)

    sl = _stop_for_direction(direction, m15, atr_m15, profile, settings)
    if sl is None or sl == entry_ref:
        signal["reason"] = "stop_unavailable"
        return signal
    tp1, tp2, rr1 = _targets_for_direction(
        direction, entry_ref, sl, magnets, atr_m15, profile, settings
    )
    if tp1 is None or rr1 is None:
        signal["reason"] = "targets_unavailable"
        return signal
    _gate("rr_floor", rr1 >= profile.min_rr, f"rr1={rr1:.2f} min={profile.min_rr:.2f}")

    trigger_confirmed = False
    if len(m15) >= 2:
        c0 = m15[-2]
        c1 = m15[-1]
        o1 = _f(c1.get("open"))
        h1v = _f(c1.get("high"))
        l1v = _f(c1.get("low"))
        c1v = _f(c1.get("close"))
        c0h = _f(c0.get("high"))
        c0l = _f(c0.get("low"))
        if all(v is not None for v in (o1, h1v, l1v, c1v, c0h, c0l)):
            body = abs(c1v - o1)
            range_ = max(h1v - l1v, 1e-12)
            strong_body = body / range_ >= 0.45
            if direction == "LONG":
                trigger_confirmed = bool(c1v > c0h and c1v > o1 and strong_body)
            else:
                trigger_confirmed = bool(c1v < c0l and c1v < o1 and strong_body)
    signal["triggerConfirmed"] = trigger_confirmed

    session_ok = True
    if profile.session_mode == "day" and velocity is not None:
        session_ok = velocity >= 0.25
        _gate("session_active", session_ok, f"velocity={velocity:.2f}", hard=False)

    trade_threshold = profile.trade_threshold
    watch_threshold = profile.watch_threshold
    extra = settings.extra or {}
    try:
        overrides = (extra.get("THRESHOLD_OVERRIDES") or {}).get(profile.group) or {}
        trade_threshold = float(overrides.get("trade", trade_threshold))
        watch_threshold = float(overrides.get("watch", watch_threshold))
    except (TypeError, ValueError):
        pass

    hard_ok = all(g["passed"] for g in gates if g.get("hard"))
    soft_ok = all(g["passed"] for g in gates if not g.get("hard"))

    if hard_ok and session_ok and score >= trade_threshold:
        decision = "TRADE"
    elif hard_ok and score >= watch_threshold:
        decision = "WATCH"
        if not soft_ok:
            reasons.append("soft_gate_downgrade")
    else:
        decision = "NO_SIGNAL"

    play_type = None
    if decision in {"TRADE", "WATCH"}:
        loc = magnet if magnet is not None else 0.0
        spr = spring if spring is not None else 0.0
        stc = structure if structure is not None else 0.0
        if spr >= 0.55 and abs(loc) >= 0.7 and loc * side > 0 and stc * side > 0:
            play_type = "BREAKOUT"
        elif loc * side < -0.75 and kinetic is not None and kinetic * side < 0:
            play_type = "FADE"
        else:
            play_type = "PULLBACK"

    signal.update(
        {
            "decision": decision,
            "direction": direction,
            "playType": play_type,
            "score": round(score, 1),
            "entryRef": round(entry_ref, 10),
            "priceSource": "live_quote_mid" if quote_price else "confirmed_close",
            "sl": round(sl, 10),
            "tp1": round(tp1, 10),
            "tp2": round(tp2, 10),
            "rr1": rr1,
            "atr": round(atr_m15, 10),
            "atrTf": _FAST_TF,
            "atrPct": round(atr_pct_m15, 6),
            "magnets": {
                k: (round(v, 10) if isinstance(v, float) else v)
                for k, v in magnets.items()
                if k != "levels"
            },
            "triggerConfirmed": trigger_confirmed,
            "thresholds": {"trade": trade_threshold, "watch": watch_threshold},
        }
    )
    return signal
