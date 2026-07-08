"""Config-gated orthogonal subsystem factors for Engine A V3 quant scoring."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

SUBSYSTEM_FACTORS = ("intermarket", "carry", "sentiment", "macro", "microstructure")

ST_AVAILABLE = "available"
ST_NEUTRAL = "neutral"
ST_UNAVAILABLE = "unavailable"
ST_NA = "na"

_DEFAULT_WEIGHTS_BY_FAMILY: dict[str, dict[str, float]] = {
    "forex": {
        "intermarket": 0.0,
        "carry": 0.14,
        "sentiment": 0.08,
        "macro": 0.0,
        "microstructure": 0.0,
    },
    "crypto": {
        "intermarket": 0.0,
        "carry": 0.0,
        "sentiment": 0.09,
        "macro": 0.0,
        "microstructure": 0.0,
    },
    "commodity": {
        "intermarket": 0.0,
        "carry": 0.16,
        "sentiment": 0.09,
        "macro": 0.0,
        "microstructure": 0.0,
    },
    "index": {
        "intermarket": 0.0,
        "carry": 0.12,
        "sentiment": 0.08,
        "macro": 0.0,
        "microstructure": 0.0,
    },
    "equity_etf": {
        "intermarket": 0.0,
        "carry": 0.12,
        "sentiment": 0.09,
        "macro": 0.0,
        "microstructure": 0.0,
    },
    "unknown": {
        "intermarket": 0.0,
        "carry": 0.0,
        "sentiment": 0.0,
        "macro": 0.0,
        "microstructure": 0.0,
    },
}


def subsystems_enabled() -> bool:
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_SUBSYSTEMS") or {}
        return bool(cfg.get("ENABLED", False))
    except Exception:
        return False


def resolve_subsystem_weights(family: str) -> dict[str, float]:
    defaults = dict(_DEFAULT_WEIGHTS_BY_FAMILY.get(family, _DEFAULT_WEIGHTS_BY_FAMILY["unknown"]))
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_A_V3_SUBSYSTEMS") or {}
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


def subsystem_component(entry: Any) -> tuple[Any, str]:
    """Map a context entry to (Component, state)."""
    from engine_a_v3.quant_scorer import Component, _clamp, _clamp01

    if not isinstance(entry, Mapping):
        return Component(0.0, 0.0, available=False), ST_UNAVAILABLE
    state = str(entry.get("state") or "").lower()
    if state == ST_NA:
        return Component(0.0, 0.0, available=False), ST_NA
    if state == ST_UNAVAILABLE:
        return Component(0.0, 0.0, available=False), ST_UNAVAILABLE
    signal = _clamp(float(entry.get("signal") or 0.0), -1.0, 1.0)
    quality = _clamp(float(entry.get("quality") or 0.0), 0.0, 1.0)
    if not state:
        state = ST_NEUTRAL if (abs(signal) < 1e-9 or quality < 1e-9) else ST_AVAILABLE
    return Component(signal, quality, available=True), state


def z_to_subsystem_entry(z: float | None, *, neutral_eps: float = 0.05) -> dict[str, Any]:
    from engine_a_v3.quant_scorer import _clamp, _clamp01

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
