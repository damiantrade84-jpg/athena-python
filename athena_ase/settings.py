"""ASE runtime settings loaded from app CONFIG (config-gated, default-safe)."""

from __future__ import annotations

import os

from typing import Any, FrozenSet

_DEFAULT_INTRADAY_FAMILIES = frozenset({"forex", "crypto", "commodity"})


def _cfg() -> dict[str, Any]:
    # Offline ASE research must not import the full application configuration:
    # a live-mode checkout correctly fails its real-order confirmation guard.
    # Explicit research runs use conservative ASE defaults instead of bypassing
    # that production safety contract.
    if os.environ.get("ATHENA_ASE_RESEARCH_CONFIG_DEFAULTS", "").strip() == "1":
        return {}
    try:
        from config import CONFIG

        return CONFIG
    except BaseException:
        return {}


def arbitration_mode() -> str:
    block = _cfg().get("ASE_ARBITRATION") or {}
    mode = str(block.get("MODE", "weighted")).strip().lower()
    return mode if mode in ("max", "weighted") else "weighted"


def arbitration_strength_floor() -> float:
    block = _cfg().get("ASE_ARBITRATION") or {}
    try:
        return float(block.get("STRENGTH_FLOOR", 0.3))
    except (TypeError, ValueError):
        return 0.3


def monitor_min_samples() -> int:
    block = _cfg().get("ASE_MONITOR") or {}
    try:
        return max(1, int(block.get("MIN_SAMPLES", 5)))
    except (TypeError, ValueError):
        return 5


def monitor_max_live_rows() -> int:
    block = _cfg().get("ASE_MONITOR") or {}
    try:
        return max(5, int(block.get("MAX_LIVE_ROWS", 200)))
    except (TypeError, ValueError):
        return 200


def payoff_win_quantile() -> str:
    block = _cfg().get("ASE_PAYOFF") or {}
    q = str(block.get("WIN_QUANTILE", "q50")).strip().lower()
    return q if q in ("q50", "q25", "q10") else "q50"


def payoff_realized_cap_enabled() -> bool:
    block = _cfg().get("ASE_PAYOFF") or {}
    return bool(block.get("REALIZED_CAP_ENABLED", False))


def payoff_realized_cap() -> float:
    block = _cfg().get("ASE_PAYOFF") or {}
    try:
        return float(block.get("REALIZED_CAP", 1.0))
    except (TypeError, ValueError):
        return 1.0


def payoff_use_realized_exit_r() -> bool:
    block = _cfg().get("ASE_PAYOFF") or {}
    return bool(block.get("USE_REALIZED_EXIT_R", False))


def payoff_realized_min_trades() -> int:
    block = _cfg().get("ASE_PAYOFF") or {}
    try:
        return max(3, int(block.get("REALIZED_MIN_TRADES", 5)))
    except (TypeError, ValueError):
        return 5


def carry_cvr_scale(family: str) -> float:
    block = _cfg().get("ASE_CARRY") or {}
    scales = block.get("CVR_SCALE") or {}
    try:
        return float(scales.get(family, scales.get("default", 10.0)))
    except (TypeError, ValueError):
        return 10.0


def carry_slope_enabled() -> bool:
    block = _cfg().get("ASE_CARRY") or {}
    return bool(block.get("SLOPE_ENABLED", False))


def intraday_families() -> FrozenSet[str]:
    raw = _cfg().get("ASE_INTRADAY_FAMILIES")
    if not raw:
        return _DEFAULT_INTRADAY_FAMILIES
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return frozenset(parts) if parts else _DEFAULT_INTRADAY_FAMILIES
    try:
        return frozenset(str(x).strip() for x in raw if str(x).strip())
    except TypeError:
        return _DEFAULT_INTRADAY_FAMILIES


def layer1_vol_regime_filter_enabled() -> bool:
    block = _cfg().get("ASE_LAYER1_VOL_REGIME_FILTER") or {}
    return bool(block.get("ENABLED", False))


def layer1_vol_regime_extreme_ordinal() -> int:
    block = _cfg().get("ASE_LAYER1_VOL_REGIME_FILTER") or {}
    try:
        return int(block.get("EXTREME_ORDINAL", 2))
    except (TypeError, ValueError):
        return 2


def layer1_vol_regime_min_strength() -> float:
    block = _cfg().get("ASE_LAYER1_VOL_REGIME_FILTER") or {}
    try:
        return float(block.get("MIN_STRENGTH", 0.5))
    except (TypeError, ValueError):
        return 0.5


def xsec_continuous_enabled() -> bool:
    return bool(_cfg().get("ASE_XSEC_CONTINUOUS", False))


def xsec_z_cap() -> float:
    block = _cfg().get("ASE_XSEC") or {}
    try:
        return float(block.get("Z_CAP", 2.0))
    except (TypeError, ValueError):
        return 2.0


def commodity_xsec_enabled() -> bool:
    """Research gate; operational default remains off until validation passes."""
    if os.environ.get("ATHENA_ASE_RESEARCH_COMMODITY_XSEC", "").strip() == "1":
        return True
    block = _cfg().get("ASE_XSEC") or {}
    return bool(block.get("COMMODITY_ENABLED", False))


def features_v2_enabled() -> bool:
    return bool(_cfg().get("ASE_FEATURES_V2_ENABLED", False))


def microstructure_enabled() -> bool:
    block = _cfg().get("ASE_MICROSTRUCTURE") or {}
    return bool(block.get("ENABLED", False))


def auto_scan_enabled() -> bool:
    return bool(_cfg().get("ASE_AUTO_SCAN_ENABLED", False))
