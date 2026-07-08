"""Config-gated carry/COT orderflow factors for Engine B quality scoring."""

from __future__ import annotations

import math
from typing import Any

SUBSYSTEM_FACTORS = ("carry", "sentiment")

ST_AVAILABLE = "available"
ST_NEUTRAL = "neutral"
ST_UNAVAILABLE = "unavailable"
ST_NA = "na"

_DEFAULT_WEIGHTS_BY_FAMILY: dict[str, dict[str, float]] = {
    "forex": {"carry": 0.6, "sentiment": 0.4},
    "commodity": {"carry": 0.7, "sentiment": 0.3},
    "crypto": {},
    "stock": {},
    "index": {},
    "etf": {},
    "unknown": {},
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _clamp01(value: float) -> float:
    return _clamp(value, 0.0, 1.0)


def subsystems_enabled() -> bool:
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_B_SUBSYSTEMS") or {}
        return bool(cfg.get("ENABLED", False))
    except Exception:
        return False


def resolve_subsystem_weights(family: str) -> dict[str, float]:
    defaults = dict(_DEFAULT_WEIGHTS_BY_FAMILY.get(family, _DEFAULT_WEIGHTS_BY_FAMILY["unknown"]))
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_B_SUBSYSTEMS") or {}
        overrides = cfg.get("WEIGHTS_BY_FAMILY") or {}
        family_override = overrides.get(family) if isinstance(overrides, dict) else None
        if isinstance(family_override, dict):
            for name in SUBSYSTEM_FACTORS:
                if name in family_override:
                    try:
                        defaults[name] = max(0.0, float(family_override[name]))
                    except (TypeError, ValueError):
                        pass
    except Exception:
        pass
    return defaults


def z_to_subsystem_entry(z: float | None, *, neutral_eps: float = 0.05) -> dict[str, Any]:
    if z is None:
        return {"state": ST_UNAVAILABLE}
    z = float(z)
    if abs(z) < neutral_eps:
        return {"signal": 0.0, "quality": 0.0, "state": ST_NEUTRAL, "z": round(z, 3)}
    return {
        "signal": _clamp(math.tanh(z / 1.5), -1.0, 1.0),
        "quality": _clamp01(abs(z) / 2.0),
        "state": ST_AVAILABLE,
        "z": round(z, 3),
    }


def _carry_entry(display: str, *, as_of_date: str | None = None) -> dict[str, Any]:
    try:
        from carry_feed import _PAIR_CARRY_FORMULA, get_carry_z

        if not _PAIR_CARRY_FORMULA.get(display):
            return {"state": ST_NA}
        return z_to_subsystem_entry(get_carry_z(display, as_of_date=as_of_date))
    except Exception:
        return {"state": ST_UNAVAILABLE}


def _sentiment_entry(display: str, *, as_of_date: str | None = None) -> dict[str, Any]:
    try:
        from cot_feed import _PAIR_FORMULA, get_cot_z

        if not _PAIR_FORMULA.get(display):
            return {"state": ST_NA}
        return z_to_subsystem_entry(get_cot_z(display, as_of_date=as_of_date))
    except Exception:
        return {"state": ST_UNAVAILABLE}


def _entry_alignment_score(entry: dict[str, Any], direction: str) -> float:
    if not isinstance(entry, dict):
        return 0.0
    state = str(entry.get("state") or "").lower()
    if state in {ST_UNAVAILABLE, ST_NA}:
        return 0.0
    try:
        signal = float(entry.get("signal") or 0.0)
    except (TypeError, ValueError):
        signal = 0.0
    try:
        quality = float(entry.get("quality") or 0.0)
    except (TypeError, ValueError):
        quality = 0.0
    if quality <= 0.0:
        return 0.0
    dir_sign = 1.0 if str(direction).upper() == "LONG" else -1.0
    aligned = max(0.0, signal * dir_sign)
    return _clamp01(aligned * quality)


def compute_subsystem_orderflow_score(
    asset_type: str,
    display: str | None,
    direction: str,
    *,
    as_of_date: str | None = None,
) -> float:
    """Return 0-1 orderflow alignment from carry/COT feeds (quality-only, never gates)."""
    if not subsystems_enabled() or not display:
        return 0.0
    family = str(asset_type or "").lower() or "unknown"
    weights = resolve_subsystem_weights(family)
    if not weights:
        return 0.0

    entries = {
        "carry": _carry_entry(display, as_of_date=as_of_date),
        "sentiment": _sentiment_entry(display, as_of_date=as_of_date),
    }
    weight_sum = 0.0
    score_sum = 0.0
    for name, weight in weights.items():
        if weight <= 0:
            continue
        component = _entry_alignment_score(entries.get(name, {}), direction)
        if component <= 0:
            continue
        weight_sum += weight
        score_sum += weight * component
    if weight_sum <= 0:
        return 0.0
    return round(_clamp01(score_sum / weight_sum), 4)


def engine_b_pick_directional_candidate(
    candidates: list[dict[str, Any]],
    *,
    min_gap: float = 0.0,
    score_key: str = "score",
) -> dict[str, Any] | None:
    """Pick the best directional candidate, optionally requiring a minimum score gap."""
    if not candidates:
        return None

    def _score(candidate: dict[str, Any]) -> float:
        try:
            return float(candidate.get(score_key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    ranked = sorted(candidates, key=_score, reverse=True)
    if len(ranked) >= 2 and min_gap > 0:
        if _score(ranked[0]) - _score(ranked[1]) < min_gap:
            return None
    return ranked[0]


def engine_b_direction_min_score_gap() -> float:
    try:
        from config import CONFIG

        return max(0.0, float(CONFIG.get("ENGINE_B_DIRECTION_MIN_SCORE_GAP", 0.0) or 0.0))
    except (TypeError, ValueError):
        return 0.0
