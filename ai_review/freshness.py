"""TF-aware ATR freshness classification for AI chart review."""

from __future__ import annotations

from typing import Any

from config import CONFIG

_D1_CONFIRMED_LAG_CAP_SEC = 172800


def _max_age_for_tf(atr_tf: str | None, cfg: dict[str, Any]) -> int | None:
    override = cfg.get("ATR_FRESHNESS_MAX_AGE_SEC")
    if isinstance(override, dict):
        key = str(atr_tf or "").upper()
        if key in override:
            return int(override[key])
    policy = CONFIG.get("VISION_FRESHNESS_POLICY") or {}
    ages = policy.get("max_age_sec") or {}
    if not isinstance(ages, dict):
        return None
    return ages.get(str(atr_tf or "").upper())


def classify_atr_freshness(
    atr_tf: str | None,
    atr_age_seconds: float | None,
    confirmed_only: bool | None,
) -> dict[str, Any]:
    max_age = _max_age_for_tf(atr_tf, CONFIG.get("AI_CHART_REVIEW") or {})
    tf = str(atr_tf or "").upper()

    if atr_age_seconds is None:
        return {
            "status": "unknown",
            "reason": "ATR age unavailable",
            "max_expected_age_seconds": max_age,
        }

    age = float(atr_age_seconds)
    if max_age is None:
        return {
            "status": "unknown",
            "reason": "ATR TF max age unavailable",
            "max_expected_age_seconds": None,
        }

    if tf == "D1" and confirmed_only is True and age <= _D1_CONFIRMED_LAG_CAP_SEC:
        return {
            "status": "expected_lag",
            "reason": "D1 confirmed-only ATR within natural close window",
            "max_expected_age_seconds": max_age,
        }

    if age <= max_age:
        return {
            "status": "fresh",
            "reason": "ATR within TF window",
            "max_expected_age_seconds": max_age,
        }

    if age <= 2 * max_age:
        return {
            "status": "expected_lag",
            "reason": "ATR slightly past TF window",
            "max_expected_age_seconds": max_age,
        }

    return {
        "status": "stale",
        "reason": "ATR exceeds TF freshness window",
        "max_expected_age_seconds": max_age,
    }
