"""Engine A direction conflict and overlay helpers."""

from __future__ import annotations

from typing import Any


def _norm_direction(value: Any) -> str | None:
    direction = str(value or "").upper()
    return direction if direction in ("LONG", "SHORT") else None


def _config_bool(key: str, default: bool = False) -> bool:
    try:
        from config import CONFIG
    except Exception:
        return default
    return bool(CONFIG.get(key, default))


def _config_float(key: str, default: float) -> float:
    try:
        from config import CONFIG
    except Exception:
        return default
    try:
        return float(CONFIG.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def engine_a_direction_conflict_flags(
    direction: str | None,
    *,
    intermarket_confirmation: dict[str, Any] | None = None,
    btc_bias: str | None = None,
    btc_correlation: float | None = None,
    btc_mult: float | None = None,
    intermarket_guard_enabled: bool | None = None,
    btc_guard_enabled: bool | None = None,
) -> dict[str, bool]:
    """Detect contra-trend intermarket/BTC conflicts without clearing direction."""
    dir_u = _norm_direction(direction)
    flags = {
        "directionConflictedWithIntermarket": False,
        "directionConflictedWithBtc": False,
        "directionConflicted": False,
    }
    if dir_u is None:
        return flags

    if intermarket_guard_enabled is None:
        intermarket_guard_enabled = _config_bool(
            "ENGINE_A_DIRECTION_INTERMARKET_CONFLICT_GUARD_ENABLED", False
        )
    if btc_guard_enabled is None:
        btc_guard_enabled = _config_bool(
            "ENGINE_A_DIRECTION_BTC_CONFLICT_GUARD_ENABLED", False
        )

    if intermarket_guard_enabled and isinstance(intermarket_confirmation, dict):
        verdict = str(intermarket_confirmation.get("verdict") or "").lower()
        try:
            raw_score = float(intermarket_confirmation.get("score", 0.0) or 0.0)
        except (TypeError, ValueError):
            raw_score = 0.0
        min_score = _config_float(
            "ENGINE_A_DIRECTION_INTERMARKET_CONFLICT_MIN_SCORE", -0.12
        )
        if verdict == "contradictory" or raw_score <= min_score:
            flags["directionConflictedWithIntermarket"] = True

    if btc_guard_enabled:
        bias = str(btc_bias or "").lower()
        try:
            corr = float(btc_correlation) if btc_correlation is not None else None
        except (TypeError, ValueError):
            corr = None
        try:
            mult = float(btc_mult) if btc_mult is not None else 1.0
        except (TypeError, ValueError):
            mult = 1.0
        min_corr = _config_float("ENGINE_A_DIRECTION_BTC_CONFLICT_MIN_CORRELATION", 0.80)
        opposed = (
            (bias == "bullish" and dir_u == "SHORT")
            or (bias == "bearish" and dir_u == "LONG")
        )
        if (
            bias in ("bullish", "bearish")
            and opposed
            and corr is not None
            and corr >= min_corr
            and mult < 1.0
        ):
            flags["directionConflictedWithBtc"] = True

    flags["directionConflicted"] = (
        flags["directionConflictedWithIntermarket"]
        or flags["directionConflictedWithBtc"]
    )
    return flags


def apply_direction_conflict_to_result(
    result: dict[str, Any],
    *,
    intermarket_confirmation: dict[str, Any] | None = None,
    btc_bias: str | None = None,
    btc_correlation: float | None = None,
    btc_mult: float | None = None,
) -> dict[str, Any]:
    """Merge conflict flags into a factor/scan result dict in-place."""
    flags = engine_a_direction_conflict_flags(
        result.get("direction"),
        intermarket_confirmation=intermarket_confirmation
        or result.get("intermarket_confirmation"),
        btc_bias=btc_bias,
        btc_correlation=btc_correlation,
        btc_mult=btc_mult if btc_mult is not None else result.get("btc_bias_applied"),
    )
    result.update(flags)
    if flags["directionConflicted"]:
        result["signalExecutable"] = False
    return result
