"""regime.py — Standalone regime detection module.

Extracts ADX-based market regime classification from scoring.py into a
reusable function. Imported by scoring.py and backtest utilities.
"""

from config import CONFIG


def detect_regime(
    h4_snap: dict,
    pair_type: str,
    config: dict | None = None,
    bb_width_pct: float | None = None,
) -> dict:
    """Classify the current market regime from H4 ADX indicators + BB width confirmation.

    Args:
        h4_snap:      The 'snap' dict from H4 indicators (contains adx, adxMomentum, adxSlope).
        pair_type:    Asset class string ('crypto', 'forex', 'commodity', 'stock', 'index').
        config:       Optional config override; defaults to global CONFIG.
        bb_width_pct: BB width percentile (0-100). Used as second confirmation signal.

    Returns:
        dict with keys:
            state            int   0=TRENDING, 1=RANGING, 2=HIGH_VOLATILITY, 3=LOW_VOLATILITY
            label            str   human-readable state name
            regime           str   config-compatible key (TRENDING/RANGING/HIGH_VOLATILITY/LOW_VOLATILITY)
            adx_value        float|None
            adx_momentum     str   'strengthening'|'stable'|'exhausting'|'collapsing'
            ranging_penalty  float  calculated score penalty
            confidence       str   'high'|'medium'|'low'
            bb_width_pct     float|None  BB width percentile for transparency
    """
    cfg = config or CONFIG
    _rng = cfg["RANGING"].get(
        pair_type,
        cfg["RANGING"].get(
            "commodity", {"dead": 15, "choppy": 20, "dead_pen": 3.0, "choppy_pen": 1.5}
        ),
    )

    adx_val = h4_snap.get("adx")
    adx_mom = h4_snap.get("adxMomentum", "stable")
    ranging_penalty = 0.0

    if adx_val is not None:
        if adx_val < _rng["dead"]:
            ranging_penalty = _rng["dead_pen"]
        elif adx_val < _rng["choppy"]:
            ranging_penalty = _rng["choppy_pen"]

    # Crypto-specific transition penalty on top of range penalty (capped at 2.0 total)
    if pair_type == "crypto" and adx_mom in ("collapsing", "exhausting"):
        _trans_pen = 1.5 if adx_mom == "collapsing" else 0.8
        ranging_penalty = min(ranging_penalty + _trans_pen, 2.0)

    # Determine state from ADX
    if adx_val is None:
        state = 1
        label = "RANGING"
        confidence = "low"
    elif adx_val < _rng["dead"] or adx_mom == "collapsing":
        state = 1
        label = "RANGING"
        confidence = (
            "medium" if adx_val is not None and adx_val < _rng["dead"] else "low"
        )
    elif adx_val >= _rng["choppy"] and adx_mom in ("stable", "strengthening"):
        state = 0
        label = "TRENDING"
        confidence = "high" if adx_val >= 35 else "medium"
    else:
        state = 1
        label = "RANGING"
        confidence = "medium" if adx_val >= _rng["dead"] else "low"

    # BB width percentile confirmation — upgrade/downgrade confidence
    if bb_width_pct is not None:
        if state == 0 and bb_width_pct <= 25:
            # ADX says trending but BB says compressed — disagreement → downgrade
            confidence = "low"
        elif state == 0 and bb_width_pct >= 60:
            # ADX says trending AND BB is wide — both agree → upgrade
            confidence = "high"
        elif state == 2 and bb_width_pct >= 75:
            # ADX says high_vol AND BB is wide — both agree → high confidence
            confidence = "high"
        elif state == 2 and bb_width_pct <= 25:
            # ADX says high_vol but BB is compressed — may be a squeeze
            confidence = "low"
        # HIGH_VOLATILITY: ranging/choppy + wide BB = true volatility expansion
        if state == 1 and bb_width_pct >= 75:
            state = 2
            label = "HIGH_VOLATILITY"
            confidence = "high"
        # LOW_VOLATILITY: low ADX + compressed BB
        elif state == 1 and bb_width_pct <= 20:
            state = 3
            label = "LOW_VOLATILITY"
            confidence = "medium"

    return {
        "state": state,
        "label": label,
        "regime": label,
        "adx_value": adx_val,
        "adx_momentum": adx_mom,
        "ranging_penalty": ranging_penalty,
        "confidence": confidence,
        "bb_width_pct": bb_width_pct,
    }
