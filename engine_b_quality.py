"""Weighted quality scoring for Engine B (config-gated, gates unchanged)."""

from __future__ import annotations

from typing import Any

from engine_b_subsystems import compute_subsystem_orderflow_score

_DEFAULT_COMPONENT_MAX: dict[str, float] = {
    "structure_alignment": 1.0,
    "ob_confluence": 1.0,
    "fvg_confluence": 1.0,
    "bag_continuation": 1.0,
    "liquidity_proximity": 0.75,
    "bos_followthrough": 1.5,
    "volume_confirmation": 1.0,
    "profile_reaction": 1.0,
    "session_context": 0.5,
    "orderflow": 1.0,
    # Tuning Lab (athena_experiment) optional oscillator confluence — see
    # _momentum_oscillator_confluence_score below. Present in the max map so a
    # non-zero COMPONENT_WEIGHTS override normalizes correctly.
    "momentum_oscillator_confluence": 1.0,
    "pullback_quality": 1.0,
    "sweep_quality": 1.0,
}

_DEFAULT_COMPONENT_WEIGHTS: dict[str, float] = {
    "structure_alignment": 0.18,
    "ob_confluence": 0.11,
    "fvg_confluence": 0.09,
    "bag_continuation": 0.06,
    "liquidity_proximity": 0.06,
    "bos_followthrough": 0.11,
    "volume_confirmation": 0.08,
    "profile_reaction": 0.07,
    "session_context": 0.06,
    "orderflow": 0.04,
    # Default 0.0 — inert. ENGINE_B_WEIGHTED_SCORING itself defaults disabled
    # (weighted_scoring_enabled()), and even when a group enables it, this
    # component contributes nothing until a config.local.yaml override (via the
    # Tuning Lab "push to default" action) gives it a non-zero weight. The ten
    # active weights above, including BAG continuation, sum to 1.0.
    "momentum_oscillator_confluence": 0.0,
    "pullback_quality": 0.08,
    "sweep_quality": 0.06,
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


def weighted_scoring_config_for_group(score_group: str | None) -> dict[str, Any]:
    """``weighted_scoring_config()`` with ``COMPONENT_WEIGHTS_BY_GROUP[score_group]``
    merged over ``COMPONENT_WEIGHTS`` for this one group.

    Tuning Lab (athena_experiment) per-group override point — the same "tune
    on one group, push only for that group" contract as
    ``ENGINE_A_QUANT_WEIGHTS_BY_GROUP`` on the Engine A side. A group with no
    ``COMPONENT_WEIGHTS_BY_GROUP`` entry gets exactly the process-wide config
    unchanged.
    """
    cfg = dict(weighted_scoring_config())
    group_key = str(score_group or "").strip().lower()
    if not group_key:
        return cfg
    by_group = cfg.get("COMPONENT_WEIGHTS_BY_GROUP")
    if not isinstance(by_group, dict):
        return cfg
    override = by_group.get(group_key)
    if not isinstance(override, dict) or not override:
        return cfg
    merged_weights = dict(cfg.get("COMPONENT_WEIGHTS") or {})
    for key, value in override.items():
        try:
            merged_weights[str(key)] = max(0.0, float(value))
        except (TypeError, ValueError):
            continue
    cfg["COMPONENT_WEIGHTS"] = merged_weights
    return cfg


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


def macro_sequence_is_independent(res: dict[str, Any]) -> bool:
    """Whether the macro sequence is a genuinely separate timeframe read.

    Several policy profiles (broad crosses, exotics, thin metals/base/softs,
    thin crypto) resolve bias TF == structure TF, so ``macro_swing_sequence``
    is computed from the same series as ``current_swing_sequence``. Crediting
    both as independent confluence would score one timeframe twice. Unknown
    timeframes are treated as independent (historic behaviour).
    """
    structure_tf = str(res.get("structure_tf") or "").strip().upper()
    macro_tf = str(res.get("macro_sequence_tf") or "").strip().upper()
    if not structure_tf or not macro_tf:
        return True
    return structure_tf != macro_tf


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
    if not macro_sequence_is_independent(res):
        # Same series on both rungs: keep the micro read, drop the duplicate
        # macro read so the "both timeframes agree" tier stays unreachable.
        macro_aligned = False
    bos = bool(res.get("bos_confirmed", False))
    choch = bool(res.get("choch_confirmed", False))
    raw_sweep = bool(res.get("liquidity_sweep", False))
    # Direction-aligned sweep only (mirrors calculate_confidence._sweep_aligned):
    # an opposing sweep is evidence for the other side, not this candidate.
    _sweep_dir = str(res.get("sweep_direction") or "").upper() or None
    aligned_sweep = raw_sweep and (_sweep_dir is None or _sweep_dir == dir_u)
    # bos_mtf_confirmed is direction-blind; only credit the multi-TF break when
    # it is in the trade direction (bos_confirmed is direction-aware), so an
    # opposing MTF BOS cannot inflate a counter-trend candidate's alignment.
    bos_mtf = bool(res.get("bos_mtf_confirmed", False)) and bos

    try:
        from config import CONFIG

        _seq_dir = bool(CONFIG.get("ENGINE_B_SWING_SEQUENCE_DIRECTION_ENABLED", False))
    except Exception:
        _seq_dir = False
    if _seq_dir:
        # Legacy ladder: lagging HH_HL/LH_LL outranks break evidence.
        if micro_aligned and macro_aligned:
            score = 1.0
        elif micro_aligned:
            score = 0.65
        elif macro_aligned:
            score = 0.55
        elif bos or raw_sweep:
            score = 0.35
        else:
            score = 0.0
    else:
        # Break/character evidence is direction authority. The lagging swing
        # sequence never regains direction authority or a veto here.
        if bos:
            score = 0.85
        elif choch:
            score = 0.70
        elif aligned_sweep:
            score = 0.45
        else:
            score = 0.0

        # ...but a *fresh* aligned sequence is still genuine confluence. Retiring
        # it from direction (EURNZD: stale HH_HL maxed this component while H4/D1
        # structure was selling) also removed it from scoring entirely, so the
        # classic market-structure read stopped contributing anywhere and
        # Engine B's structure score rests solely on BOS/CHoCH. Staleness — not
        # the read itself — was the defect, and it is now measured directly
        # (current_swing_sequence_age). Credit is a bounded bonus on top of break
        # evidence, decaying to zero as the defining swing ages out.
        bonus, freshness = _sequence_freshness_bonus(res, micro_aligned, macro_aligned)
        if bonus > 0 and score > 0:
            score = min(1.0, score + bonus)
        _ = freshness

    if bos_mtf and score > 0:
        score = min(1.0, score + 0.15)
    return round(_clamp01(score), 4)


def _sequence_freshness_bonus(
    res: dict[str, Any], micro_aligned: bool, macro_aligned: bool
) -> tuple[float, float]:
    """Bounded confluence bonus for a *fresh* direction-aligned swing sequence.

    Returns (bonus, freshness) where freshness is 1.0 at age 0 and decays
    linearly to 0.0 at ``FRESH_BARS``. Only the micro (structure-rung) sequence
    is credited when the macro rung is not an independent timeframe, so the same
    series is never scored twice. Disabled via
    ``ENGINE_B_SWING_SEQUENCE_CONFLUENCE_ENABLED: false``.
    """
    try:
        from config import CONFIG

        if not bool(CONFIG.get("ENGINE_B_SWING_SEQUENCE_CONFLUENCE_ENABLED", True)):
            return 0.0, 0.0
        fresh_bars = float(CONFIG.get("ENGINE_B_SWING_SEQUENCE_FRESH_BARS", 10) or 10)
        max_bonus = float(CONFIG.get("ENGINE_B_SWING_SEQUENCE_CONFLUENCE_BONUS", 0.10) or 0.10)
    except Exception:
        return 0.0, 0.0
    if fresh_bars <= 0 or max_bonus <= 0:
        return 0.0, 0.0
    aligned = micro_aligned or (macro_aligned and macro_sequence_is_independent(res))
    if not aligned:
        return 0.0, 0.0
    age_key = (
        "current_swing_sequence_age" if micro_aligned else "macro_swing_sequence_age"
    )
    raw_age = res.get(age_key)
    if raw_age is None:
        # Unmeasured age is not evidence of freshness.
        return 0.0, 0.0
    try:
        age = float(raw_age)
    except (TypeError, ValueError):
        return 0.0, 0.0
    freshness = _clamp01(1.0 - max(0.0, age) / fresh_bars)
    return round(max_bonus * freshness, 4), round(freshness, 4)


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
    aligned_seen = False
    typed_seen = False
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").lower()
            if block_type:
                typed_seen = True
            if directional and block_type != aligned_type:
                continue
            aligned_seen = True
            try:
                strength = float(block.get("strength") or 0.0)
            except (TypeError, ValueError):
                strength = 0.0
            best = max(best, strength)
    if best > 0:
        return round(_clamp01(best / 100.0), 4)
    if directional and typed_seen and not aligned_seen:
        # Every typed block at the zone opposes the trade. The 0.5 "strength
        # unknown" default previously awarded half credit here, so the
        # directional filter had no effect in exactly the case it was added for.
        # 0.5 is still returned when an aligned block exists but carries no
        # usable strength, and when no block is typed at all.
        return 0.0
    return 0.5


def _fvg_confluence_score(res: dict[str, Any], direction: str) -> float:
    if not bool(res.get("fvg_overlap", False)):
        return 0.0
    context = res.get("fvg_context")
    if isinstance(context, dict):
        # The producer has already selected direction-aligned FVGs. A reaction
        # at CE/inside the aligned gap earns more than passive zone overlap.
        base = 0.65 if context.get("reaction_confirmed") else 0.45
        nearest = context.get("nearest") if isinstance(context.get("nearest"), dict) else {}
        size_atr = _float_mapping(nearest.get("gap_size_atr"), 0.0)
        displacement = _float_mapping(nearest.get("displacement_body_atr"), 0.0)
        fill = _float_mapping(nearest.get("fill_fraction"), 0.0)
        quality = base + min(0.2, size_atr * 0.2) + min(0.15, displacement * 0.1)
        quality *= max(0.0, 1.0 - min(1.0, fill))
        return round(_clamp01(quality), 4)
    zone = _nearest_zone(res, direction)
    size_atr = 0.0
    if zone is not None:
        size_atr = _float_mapping(zone.get("fvg_size_atr"), 0.0)
    return round(_clamp01(0.5 + (size_atr / 2.0)), 4)


def _bag_continuation_score(res: dict[str, Any], direction: str) -> float:
    context = res.get("fvg_context")
    if not isinstance(context, dict):
        return 0.0
    if str(context.get("direction") or "").upper() != str(direction or "").upper():
        return 0.0
    state = str(context.get("bag_state") or "none").lower()
    bag = context.get("bag") if isinstance(context.get("bag"), dict) else {}
    if state == "confirmed":
        continuation = _float_mapping(bag.get("continuation_atr"), 0.0)
        return round(_clamp01(0.75 + min(0.25, continuation * 0.1)), 4)
    if state == "candidate":
        return 0.35
    return 0.0


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


def _momentum_oscillator_confluence_score(
    candles: list[dict] | None, direction: str
) -> float:
    """Tuning Lab (athena_experiment) optional Stochastic/CCI/Williams %R
    composite. Inert by construction: this key's default weight in
    ``_DEFAULT_COMPONENT_WEIGHTS`` is 0.0 and ``ENGINE_B_WEIGHTED_SCORING`` is
    disabled by default, so the value computed here has no effect on the
    aggregate quality score until a group's config.local.yaml (via the Tuning
    Lab "push to default" action) both enables weighted scoring and gives this
    component a non-zero weight.

    0.5 = neutral (mirrors ``_session_context_score``'s signed-to-[0,1]
    mapping); returned whenever no candles are supplied or none of the three
    oscillators have a valid latest reading.
    """
    if not candles or len(candles) < 20:
        return 0.5
    try:
        from indicators import calc_cci, calc_stochastic, calc_williams_r
    except Exception:
        return 0.5

    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    terms: list[float] = []
    stoch = calc_stochastic(candles, 14, 3, 3)
    stoch_k = next((v for v in reversed(stoch.get("k") or []) if v is not None), None)
    if stoch_k is not None:
        terms.append(_clamp((stoch_k - 50.0) / 40.0, -1.0, 1.0))
    cci = next((v for v in reversed(calc_cci(candles, 20)) if v is not None), None)
    if cci is not None:
        terms.append(_clamp(cci / 150.0, -1.0, 1.0))
    williams_r = next(
        (v for v in reversed(calc_williams_r(candles, 14)) if v is not None), None
    )
    if williams_r is not None:
        terms.append(_clamp((williams_r + 50.0) / 40.0, -1.0, 1.0))
    if not terms:
        return 0.5
    mean_term = sum(terms) / len(terms)
    aligned = mean_term if str(direction).upper() == "LONG" else -mean_term
    return round(_clamp01(0.5 + aligned / 2.0), 4)


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
    oscillator_candles: list[dict] | None = None,
    pruned_out: list[str] | None = None,
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

    phase2 = res.get("phase2_quality") if isinstance(res.get("phase2_quality"), dict) else {}
    graded_volume = phase2.get("volume_confirmation") if isinstance(phase2.get("volume_confirmation"), dict) else {}
    pullback_quality = phase2.get("pullback_quality") if isinstance(phase2.get("pullback_quality"), dict) else {}
    sweep_quality = phase2.get("sweep_quality") if isinstance(phase2.get("sweep_quality"), dict) else {}
    subscores = {
        "structure_alignment": compute_structure_alignment_score(res, direction),
        "ob_confluence": _ob_confluence_score(res, direction),
        "fvg_confluence": _fvg_confluence_score(res, direction),
        "bag_continuation": _bag_continuation_score(res, direction),
        "liquidity_proximity": _liquidity_proximity_score(res, atr),
        "bos_followthrough": round(_clamp01(bos_followthrough_norm), 4),
        # 2026-08-05 Phase 2: moderate/weak authoritative volume is a graded
        # quality penalty, not a new hard rejection. Legacy boolean remains fallback.
        "volume_confirmation": round(
            _clamp01(_float_mapping(graded_volume.get("score"), 1.0 if volume_ok else 0.0)), 4
        ),
        "profile_reaction": _profile_reaction_score(res, direction),
        "session_context": _session_context_score(res),
        "orderflow": round(_clamp01(orderflow), 4),
        "momentum_oscillator_confluence": _momentum_oscillator_confluence_score(
            oscillator_candles, direction
        ),
        "pullback_quality": round(_clamp01(_float_mapping(pullback_quality.get("score"), 0.0)), 4),
        "sweep_quality": round(_clamp01(_float_mapping(sweep_quality.get("score"), 0.0)), 4),
    }
    # Drop components no feed can ever supply for this asset class, rather than
    # scoring them 0 and leaving their weight in the quality denominator — an
    # unearnable component is a permanent handicap, not an anti-inflation guard.
    # Components that are merely zero *this bar* (e.g. profile_reaction with no
    # valid session profile) stay in, so a signal cannot inflate its ratio by
    # having less evidence.
    for name in _inapplicable_quality_components(res, asset_lower):
        if subscores.pop(name, None) is not None and pruned_out is not None:
            pruned_out.append(name)
    return subscores


def _quality_applicability_pruning_enabled() -> bool:
    return bool(
        weighted_scoring_config().get("PRUNE_INAPPLICABLE_COMPONENTS", True)
    )


def _inapplicable_quality_components(
    res: dict[str, Any], asset_lower: str
) -> tuple[str, ...]:
    """Quality components that cannot be earned for this asset class at all."""
    if not _quality_applicability_pruning_enabled():
        # Legacy behaviour kept only forex volume_confirmation pruned.
        return ("volume_confirmation",) if asset_lower == "forex" else ()

    out: list[str] = []

    # The producer stamps source eligibility after applying the asset-class
    # volume policy. Missing provenance is not evidence of an eligible source.
    if not bool(res.get("bos_volume_source_applicable", False)):
        out.append("volume_confirmation")

    # Spot FX/MT5 volume is tick activity rather than centralized traded volume,
    # so an authoritative volume feed cannot exist for forex.
    if asset_lower == "forex":
        out.append("volume_confirmation")

    # Profile scoring is deliberately enabled only for trusted asset/group
    # sources. A disabled profile cannot be earned and therefore cannot remain
    # in the denominator for that pair.
    profile_context = res.get("profile_context")
    if isinstance(profile_context, dict) and not bool(profile_context.get("enabled")):
        out.append("profile_reaction")

    # orderflow: crypto reads aggTrade CVD; other classes read carry/COT via
    # engine_b_subsystems, whose weight table is empty for stock/index/etf/
    # unknown — those families scored a hard 0 while still consuming weight.
    if asset_lower != "crypto":
        try:
            from engine_b_subsystems import (
                resolve_subsystem_weights,
                subsystems_enabled,
            )

            weights = resolve_subsystem_weights(asset_lower or "unknown")
            if not subsystems_enabled() or not any(
                w > 0 for w in (weights or {}).values()
            ):
                out.append("orderflow")
        except Exception:
            out.append("orderflow")

    # session_context: only the forex/equity session payloads carry a score
    # influence. Crypto (24/7), commodity and index rows have no payload, so the
    # component could never move off 0.
    if not any(
        isinstance(res.get(key), dict)
        and bool(res[key].get("score_influence_enabled", False))
        for key in ("forex_session_structure", "equity_session_structure")
    ):
        out.append("session_context")

    return tuple(dict.fromkeys(out))


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
    """Weighted average of 0-1 subscores -> (points, max_points, per-component).

    ``APPLY_COMPONENT_MAX`` (default false) controls whether COMPONENT_MAX also
    multiplies COMPONENT_WEIGHTS. Subscores are already 0-1, so COMPONENT_MAX
    added nothing but a second, undeclared weight: the effective weight was
    ``max * weight``, which pushed bos_followthrough (0.16 declared, 1.5 max ->
    23.2% effective) above structure_alignment (0.22 declared -> 21.3%) and cut
    session_context and liquidity_proximity roughly in half. COMPONENT_WEIGHTS
    sums to exactly 1.0, so it is clearly meant to be the weight table on its
    own. Reversible: ENGINE_B_WEIGHTED_SCORING.APPLY_COMPONENT_MAX: true.
    """
    scoring_cfg = cfg if isinstance(cfg, dict) else weighted_scoring_config()
    max_map = _component_max_map(scoring_cfg)
    weight_map = _component_weight_map(scoring_cfg)
    apply_max = bool(scoring_cfg.get("APPLY_COMPONENT_MAX", False))

    component_points: dict[str, float] = {}
    quality_points = 0.0
    quality_max = 0.0
    for name, subscore in subscores.items():
        max_pts = max_map.get(name, 0.0)
        weight = weight_map.get(name, 0.0)
        if max_pts <= 0 or weight <= 0:
            continue
        scale = (max_pts * weight) if apply_max else weight
        points = round(subscore * scale, 4)
        component_points[name] = points
        quality_points += points
        quality_max += scale

    return round(quality_points, 4), round(quality_max, 4), component_points


def engine_b_conviction_basis() -> str:
    """``quality`` (default) or ``total`` — see :func:`engine_b_conviction_norm`."""
    try:
        from config import CONFIG

        basis = str(CONFIG.get("ENGINE_B_CONVICTION_BASIS", "quality") or "quality")
    except Exception:
        basis = "quality"
    basis = basis.strip().lower()
    return basis if basis in {"quality", "total"} else "quality"


def engine_b_conviction_norm(conf: dict[str, Any] | None) -> float:
    """Single 0-1 Engine B conviction for blends, ranking and execution gates.

    ``total`` basis (``score / max_possible``) is saturated: Engine B only marks
    a signal ``passed`` when every mandatory gate is true, so gate_score always
    equals gate_max_possible and the ratio starts near 0.83. Ranking, the A/B
    conviction blend and AUTO_TRADE_MIN_CONVICTION all read a number whose lower
    ~83% is constant. The ``quality`` basis reads the earned quality layer only
    (``calculate_confidence`` -> ``quality_pct``), which spans the full 0-1 range
    for passing signals. Reversible: ENGINE_B_CONVICTION_BASIS: total.

    Callers must keep using ``passed`` / ``checklist_passed`` for the gate
    decision — this function ranks, it never gates.
    """
    data = conf if isinstance(conf, dict) else {}

    def _num(*keys: str) -> float | None:
        for key in keys:
            raw = data.get(key)
            if raw is None or raw == "":
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value != value:  # NaN
                continue
            return value
        return None

    def _total_norm() -> float:
        score = _num("score", "total_score")
        max_possible = _num("max_possible", "maxScore")
        if score is not None and max_possible and max_possible > 0:
            return _clamp01(score / max_possible)
        pct = _num("pct")
        return _clamp01(pct / 100.0) if pct is not None else 0.0

    if engine_b_conviction_basis() == "total":
        return round(_total_norm(), 4)

    quality_pct = _num("quality_pct")
    if quality_pct is not None:
        return round(_clamp01(quality_pct / 100.0), 4)
    points = _num("quality_points_net", "quality_score", "bonus_points")
    denom = _num("quality_denominator", "quality_max_possible")
    if points is not None and denom and denom > 0:
        return round(_clamp01(points / denom), 4)
    # Pre-quality_pct payload (cached scan row, replayed backtest): fall back to
    # the total basis rather than reporting zero conviction.
    return round(_total_norm(), 4)


def normalize_followthrough_bonus(ft_bonus: float, ft_max: float) -> float:
    if ft_max <= 0:
        return 0.0
    return round(_clamp01(max(0.0, ft_bonus) / ft_max), 4)
