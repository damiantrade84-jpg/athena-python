"""Weighted quality scoring for Engine B (config-gated, gates unchanged)."""

from __future__ import annotations

from typing import Any

from engine_b_subsystems import compute_subsystem_orderflow_score

_DEFAULT_COMPONENT_MAX: dict[str, float] = {
    "structure_alignment": 1.0,
    "ob_confluence": 1.0,
    "fvg_confluence": 1.0,
    "liquidity_proximity": 0.75,
    "bos_followthrough": 1.5,
    "volume_confirmation": 1.0,
    "profile_reaction": 1.0,
    "session_context": 0.5,
    "orderflow": 1.0,
}

_DEFAULT_COMPONENT_WEIGHTS: dict[str, float] = {
    "structure_alignment": 0.22,
    "ob_confluence": 0.14,
    "fvg_confluence": 0.12,
    "liquidity_proximity": 0.08,
    "bos_followthrough": 0.16,
    "volume_confirmation": 0.08,
    "profile_reaction": 0.10,
    "session_context": 0.05,
    "orderflow": 0.05,
}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _float_mapping(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def weighted_scoring_config() -> dict[str, Any]:
    try:
        from config import CONFIG

        cfg = CONFIG.get("ENGINE_B_WEIGHTED_SCORING") or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def weighted_scoring_enabled() -> bool:
    return bool(weighted_scoring_config().get("ENABLED", False))


def _component_max_map(cfg: dict[str, Any]) -> dict[str, float]:
    raw = cfg.get("COMPONENT_MAX") or {}
    out = dict(_DEFAULT_COMPONENT_MAX)
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                out[str(key)] = max(0.0, float(value))
            except (TypeError, ValueError):
                continue
    return out


def _component_weight_map(cfg: dict[str, Any]) -> dict[str, float]:
    raw = cfg.get("COMPONENT_WEIGHTS") or {}
    out = dict(_DEFAULT_COMPONENT_WEIGHTS)
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                out[str(key)] = max(0.0, float(value))
            except (TypeError, ValueError):
                continue
    return out


def compute_structure_alignment_score(res: dict[str, Any], direction: str) -> float:
    h1_seq = str(res.get("current_swing_sequence") or "")
    h4_seq = str(res.get("macro_swing_sequence") or "")
    dir_u = str(direction or "").upper()
    micro_aligned = (dir_u == "LONG" and h1_seq == "HH_HL") or (
        dir_u == "SHORT" and h1_seq == "LH_LL"
    )
    macro_aligned = (dir_u == "LONG" and h4_seq == "HH_HL") or (
        dir_u == "SHORT" and h4_seq == "LH_LL"
    )
    bos = bool(res.get("bos_confirmed", False))
    sweep = bool(res.get("liquidity_sweep", False))
    bos_mtf = bool(res.get("bos_mtf_confirmed", False))

    if micro_aligned and macro_aligned:
        score = 1.0
    elif micro_aligned:
        score = 0.65
    elif macro_aligned:
        score = 0.55
    elif bos or sweep:
        score = 0.35
    else:
        score = 0.0

    if bos_mtf and score > 0:
        score = min(1.0, score + 0.15)
    return round(_clamp01(score), 4)


def _nearest_zone(res: dict[str, Any], direction: str) -> dict[str, Any] | None:
    if str(direction).upper() == "LONG":
        zone = res.get("nearest_support_zone")
    else:
        zone = res.get("nearest_resistance_zone")
    return zone if isinstance(zone, dict) else None


def _ob_confluence_directional() -> bool:
    try:
        from config import CONFIG

        return bool(CONFIG.get("ENGINE_B_OB_CONFLUENCE_DIRECTIONAL", True))
    except Exception:
        return True


def _ob_confluence_score(res: dict[str, Any], direction: str) -> float:
    if not bool(res.get("ob_at_zone", False)):
        return 0.0
    blocks = res.get("order_blocks") or []
    # 2026-07-10 audit: only direction-aligned order blocks count as confluence
    # (bullish for LONG, bearish for SHORT) — an opposing OB's strength must not
    # inflate this component. Same flag as the _ob_at_zone location filter.
    directional = _ob_confluence_directional()
    aligned_type = "bullish" if str(direction).upper() == "LONG" else "bearish"
    best = 0.0
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if directional and str(block.get("type") or "").lower() != aligned_type:
                continue
            try:
                strength = float(block.get("strength") or 0.0)
            except (TypeError, ValueError):
                strength = 0.0
            best = max(best, strength)
    if best > 0:
        return round(_clamp01(best / 100.0), 4)
    return 0.5


def _fvg_confluence_score(res: dict[str, Any], direction: str) -> float:
    if not bool(res.get("fvg_overlap", False)):
        return 0.0
    zone = _nearest_zone(res, direction)
    size_atr = 0.0
    if zone is not None:
        size_atr = _float_mapping(zone.get("fvg_size_atr"), 0.0)
    return round(_clamp01(0.5 + (size_atr / 2.0)), 4)


def _liquidity_proximity_score(res: dict[str, Any], atr: float) -> float:
    try:
        dist = float(res.get("active_zone_distance"))
    except (TypeError, ValueError):
        dist = None
    if dist is None or atr <= 0:
        base = 0.0
    else:
        base = _clamp01(1.0 - (dist / (atr * 2.0)))
    if bool(res.get("has_equal_extrema", False)):
        base = min(1.0, base + 0.15)
    return round(_clamp01(base), 4)


def _profile_reaction_score(res: dict[str, Any], direction: str) -> float:
    if not bool(res.get("prev_session_profile_valid", False)):
        return 0.0
    if not bool(res.get("profile_in_play", False)):
        return 0.0
    react = _float_mapping(res.get("profile_reaction_strength"), 0.0)
    if react <= 0:
        return 0.0
    bias = str(res.get("profile_bias") or "neutral").lower()
    aligned = (bias == "bullish" and str(direction).upper() == "LONG") or (
        bias == "bearish" and str(direction).upper() == "SHORT"
    )
    if not aligned:
        return 0.0
    if react >= 0.6:
        return round(_clamp01(react), 4)
    if react >= 0.3:
        return round(_clamp01(react * 0.5), 4)
    return 0.0


def _session_context_score(res: dict[str, Any]) -> float:
    for key in ("forex_session_structure", "equity_session_structure"):
        payload = res.get(key)
        if not isinstance(payload, dict):
            continue
        if not bool(payload.get("score_influence_enabled", False)):
            continue
        # Continuous map: negative bonus < 0.5 < positive bonus, neutral = 0.5.
        # The old `bonus == 0 -> 0.0` special case made a penalized session
        # (~0.25-0.49) outscore a neutral one (0.0). max_abs comes from the
        # session payload (forex 0.04 / equity 0.03) with 0.04 fallback.
        bonus = _float_mapping(payload.get("score_bonus"), 0.0)
        max_abs = abs(_float_mapping(payload.get("max_abs_score_bonus"), 0.04))
        if max_abs <= 0:
            max_abs = 0.04
        return round(_clamp01(0.5 + (bonus / (2.0 * max_abs))), 4)
    return 0.0


def _crypto_orderflow_score(
    *,
    aggtrade_available: bool,
    aggtrade_cvd_aligned: bool,
    aggtrade_cvd_opposed: bool,
    aggtrade_cvd_direction: str | None,
) -> float:
    if not aggtrade_available:
        return 0.0
    if aggtrade_cvd_aligned:
        return 1.0
    if aggtrade_cvd_opposed:
        return 0.0
    if aggtrade_cvd_direction:
        return 0.5
    return 0.0


def compute_confluence_subscores(
    res: dict[str, Any],
    direction: str,
    atr: float,
    *,
    bos_followthrough_norm: float = 0.0,
    volume_ok: bool = False,
    aggtrade_available: bool = False,
    aggtrade_cvd_aligned: bool = False,
    aggtrade_cvd_opposed: bool = False,
    aggtrade_cvd_direction: str | None = None,
    asset_type: str = "",
    pair_display: str | None = None,
    as_of_date: str | None = None,
) -> dict[str, float]:
    asset_lower = str(asset_type or res.get("asset_type") or "").lower()
    display = pair_display or res.get("pair_display")
    if asset_lower == "crypto":
        orderflow = _crypto_orderflow_score(
            aggtrade_available=aggtrade_available,
            aggtrade_cvd_aligned=aggtrade_cvd_aligned,
            aggtrade_cvd_opposed=aggtrade_cvd_opposed,
            aggtrade_cvd_direction=aggtrade_cvd_direction,
        )
    else:
        # as_of_date is set only by backtests (point-in-time carry/COT);
        # live passes None and keeps the mem-cached current snapshot.
        orderflow = compute_subsystem_orderflow_score(
            asset_lower, display, direction, as_of_date=as_of_date
        )

    return {
        "structure_alignment": compute_structure_alignment_score(res, direction),
        "ob_confluence": _ob_confluence_score(res, direction),
        "fvg_confluence": _fvg_confluence_score(res, direction),
        "liquidity_proximity": _liquidity_proximity_score(res, atr),
        "bos_followthrough": round(_clamp01(bos_followthrough_norm), 4),
        "volume_confirmation": 1.0 if volume_ok else 0.0,
        "profile_reaction": _profile_reaction_score(res, direction),
        "session_context": _session_context_score(res),
        "orderflow": round(_clamp01(orderflow), 4),
    }


def apply_regime_component_weights(
    subscores: dict[str, float],
    regime: str | None,
    asset_type: str = "",
) -> dict[str, float]:
    cfg = weighted_scoring_config()
    regime_key = str(regime or "").upper()
    mults_raw = cfg.get("REGIME_COMPONENT_MULT") or {}
    regime_mults = mults_raw.get(regime_key, {}) if isinstance(mults_raw, dict) else {}
    if not isinstance(regime_mults, dict) or not regime_mults:
        return dict(subscores)

    out: dict[str, float] = {}
    for name, value in subscores.items():
        try:
            mult = float(regime_mults.get(name, 1.0))
        except (TypeError, ValueError):
            mult = 1.0
        out[name] = round(_clamp01(value * mult), 4)
    return out


def aggregate_quality_score(
    subscores: dict[str, float],
    cfg: dict[str, Any] | None = None,
) -> tuple[float, float, dict[str, float]]:
    scoring_cfg = cfg if isinstance(cfg, dict) else weighted_scoring_config()
    max_map = _component_max_map(scoring_cfg)
    weight_map = _component_weight_map(scoring_cfg)

    component_points: dict[str, float] = {}
    quality_points = 0.0
    quality_max = 0.0
    for name, subscore in subscores.items():
        max_pts = max_map.get(name, 0.0)
        weight = weight_map.get(name, 0.0)
        if max_pts <= 0 or weight <= 0:
            continue
        points = round(subscore * max_pts * weight, 4)
        component_points[name] = points
        quality_points += points
        quality_max += max_pts * weight

    return round(quality_points, 4), round(quality_max, 4), component_points


def normalize_followthrough_bonus(ft_bonus: float, ft_max: float) -> float:
    if ft_max <= 0:
        return 0.0
    return round(_clamp01(max(0.0, ft_bonus) / ft_max), 4)
