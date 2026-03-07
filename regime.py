"""regime.py — Standalone regime detection module.

Extracts ADX-based market regime classification from scoring.py into a
reusable function. Imported by scoring.py and backtest utilities.
"""
from config import CONFIG


def detect_regime(h4_snap: dict, pair_type: str, config: dict | None = None) -> dict:
    """Classify the current market regime from H4 ADX indicators.

    Args:
        h4_snap:   The 'snap' dict from H4 indicators (contains adx, adxMomentum, adxSlope).
        pair_type: Asset class string ('crypto', 'forex', 'commodity', 'stock', 'index').
        config:    Optional config override; defaults to global CONFIG.

    Returns:
        dict with keys:
            state            int   0=TRENDING, 1=TRANSITIONAL, 2=CHAOTIC
            label            str   human-readable state name
            adx_value        float|None
            adx_momentum     str   'rising'|'stable'|'exhausting'|'collapsing'
            ranging_penalty  float  calculated score penalty
            confidence       str   'high'|'medium'|'low'
    """
    cfg = config or CONFIG
    _rng = cfg["RANGING"].get(pair_type, cfg["RANGING"].get("commodity", {"dead": 15, "choppy": 20, "dead_pen": 3.0, "choppy_pen": 1.5}))

    adx_val    = h4_snap.get("adx")
    adx_mom    = h4_snap.get("adxMomentum", "stable")
    adx_slope  = h4_snap.get("adxSlope", 0)

    ranging_penalty = 0.0

    if adx_val is not None:
        if adx_val < _rng["dead"]:
            ranging_penalty = _rng["dead_pen"]
        elif adx_val < _rng["choppy"]:
            ranging_penalty = _rng["choppy_pen"]

    # Crypto-specific transition penalty on top of range penalty
    if pair_type == "crypto" and adx_mom in ("collapsing", "exhausting"):
        _trans_pen = 1.5 if adx_mom == "collapsing" else 0.8
        ranging_penalty += _trans_pen

    # Determine state
    if adx_val is None:
        state = 1
        label = "TRANSITIONAL"
        confidence = "low"
    elif adx_val < _rng["dead"] or adx_mom == "collapsing":
        state = 2
        label = "CHAOTIC"
        confidence = "high" if adx_val is not None and adx_val < _rng["dead"] else "medium"
    elif adx_val >= _rng["choppy"] and adx_mom in ("stable", "rising"):
        state = 0
        label = "TRENDING"
        confidence = "high" if adx_val >= 35 else "medium"
    else:
        state = 1
        label = "TRANSITIONAL"
        confidence = "medium" if adx_val >= _rng["dead"] else "low"

    return {
        "state": state,
        "label": label,
        "adx_value": adx_val,
        "adx_momentum": adx_mom,
        "ranging_penalty": ranging_penalty,
        "confidence": confidence,
    }
