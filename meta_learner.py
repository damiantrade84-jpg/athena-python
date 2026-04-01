"""Conservative meta-learning controller for Athena.

This is intentionally not a black-box model. It is a bounded trust manager that
uses recent realized performance and current context to make small, explainable
adjustments to engine trust. Engine C remains the consensus engine.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from calibration import calibration_quality_summary, predict_calibrated_prob
from stability_monitor import get_engine_stability_snapshot, get_signal_stability_index

_ENGINES = ("engine_a", "engine_b", "engine_c", "scalp")
_MIN_SAMPLES_FOR_ADAPTATION = 8
_MIN_SAMPLES_FOR_SUSPENSION = 12
_MAX_WEIGHT_STEP = 0.08
_MIN_CAL_EXPECTANCY_WEIGHT = 0.20
_MAX_CAL_EXPECTANCY_WEIGHT = 0.75

# Explicit boundary: these are the only adaptive alpha surfaces this module controls.
_ADAPTIVE_ALPHA_SURFACES = (
    "engine_trust",
    "regime_style_weighting",
    "calibrated_expectancy_weighting",
)

# Explicit boundary: these safety surfaces are fixed and must NOT be adapted here.
FIXED_SAFETY_INVARIANTS = (
    "risk_caps",
    "timing_bar_state_invariants",
    "lookahead_protections",
    "order_lifecycle_safety",
)

_BASE_WEIGHTS_BY_STYLE = {
    "auto": {"engine_a": 0.30, "engine_b": 0.25, "engine_c": 0.35, "scalp": 0.10},
    "swing": {"engine_a": 0.34, "engine_b": 0.20, "engine_c": 0.41, "scalp": 0.05},
    "intraday": {"engine_a": 0.28, "engine_b": 0.27, "engine_c": 0.35, "scalp": 0.10},
    "scalp": {"engine_a": 0.15, "engine_b": 0.20, "engine_c": 0.15, "scalp": 0.50},
}
_MIN_WEIGHT_CAPS = {"engine_a": 0.12, "engine_b": 0.12, "engine_c": 0.18, "scalp": 0.05}
_MAX_WEIGHT_CAPS = {"engine_a": 0.42, "engine_b": 0.40, "engine_c": 0.48, "scalp": 0.55}


def _default_db_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.db")


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _norm_text(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text or None


def _signal_engine(signal: dict | None) -> str:
    sig = signal if isinstance(signal, dict) else {}
    raw = _norm_text(sig.get("engine") or sig.get("sourceEngine"))
    if raw in _ENGINES:
        return raw
    if raw == "consensus":
        return "engine_c"
    style = _norm_text(sig.get("style") or sig.get("tradeStyle"))
    if style == "scalp":
        return "scalp"
    if sig.get("conviction") is not None:
        return "engine_c"
    return "engine_a"


def _signal_style(signal: dict | None) -> str:
    sig = signal if isinstance(signal, dict) else {}
    style = _norm_text(sig.get("style") or sig.get("tradeStyle"))
    return style if style in _BASE_WEIGHTS_BY_STYLE else "auto"


def _signal_regime(signal: dict | None) -> str | None:
    sig = signal if isinstance(signal, dict) else {}
    regime = sig.get("regime")
    if isinstance(regime, dict):
        return _norm_text(regime.get("label") or regime.get("regime"))
    return _norm_text(sig.get("regimeName") or regime or sig.get("trendState"))


def _signal_asset_class(signal: dict | None) -> str | None:
    sig = signal if isinstance(signal, dict) else {}
    return _norm_text(sig.get("type") or sig.get("asset_class") or sig.get("assetClass"))


def _regime_style_weight_multipliers(context: dict, qualities: dict[str, dict]) -> dict[str, Any]:
    regime = _norm_text((context or {}).get("regime"))
    style = _norm_text((context or {}).get("style")) or "auto"
    base = {engine: 1.0 for engine in _ENGINES}

    regime_map = {
        "trending": {"engine_a": 1.05, "engine_b": 0.95, "engine_c": 1.04, "scalp": 0.96},
        "ranging": {"engine_a": 0.95, "engine_b": 1.05, "engine_c": 1.03, "scalp": 0.97},
        "high_volatility": {"engine_a": 0.95, "engine_b": 0.96, "engine_c": 1.07, "scalp": 1.04},
        "low_volatility": {"engine_a": 1.02, "engine_b": 1.03, "engine_c": 0.98, "scalp": 0.90},
    }
    if regime in regime_map:
        base.update(regime_map[regime])

    if style == "scalp":
        base["scalp"] = min(1.12, base["scalp"] * 1.06)
        base["engine_c"] = max(0.92, base["engine_c"] * 0.97)
    elif style == "swing":
        base["engine_c"] = min(1.10, base["engine_c"] * 1.03)
        base["scalp"] = max(0.86, base["scalp"] * 0.92)

    adaptive_count = sum(1 for e in _ENGINES if (qualities.get(e) or {}).get("adaptive"))
    strength = 0.0 if adaptive_count == 0 else (0.40 + 0.15 * adaptive_count)
    strength = _clamp(strength, 0.0, 1.0)
    multipliers = {}
    for engine in _ENGINES:
        raw = float(base.get(engine, 1.0))
        centered = 1.0 + ((raw - 1.0) * strength)
        multipliers[engine] = round(_clamp(centered, 0.90, 1.10), 6)

    return {
        "regime": regime,
        "style": style,
        "strength": round(strength, 6),
        "multipliers": multipliers,
        "adaptive": adaptive_count > 0,
    }


def _engine_calibrated_expectancy_weight(engine: str, context: dict, adaptive: bool) -> float:
    if not adaptive or engine != (context or {}).get("signal_engine"):
        return 0.0
    cal_q = _safe_float(((context or {}).get("calibration_quality") or {}).get("quality_score"))
    cal = (context or {}).get("calibration") or {}
    bucket_samples = _safe_float(cal.get("bucket_samples")) or 0.0
    sample_count = _safe_float(cal.get("sample_count")) or 0.0
    quality = _clamp(cal_q if cal_q is not None else 0.5, 0.0, 1.0)

    # More calibration trust only when scope is empirically supported.
    sample_boost = min(0.12, (sample_count / 100.0) * 0.12)
    bucket_boost = min(0.08, (bucket_samples / 25.0) * 0.08)
    weight = 0.30 + (quality * 0.25) + sample_boost + bucket_boost
    return round(_clamp(weight, _MIN_CAL_EXPECTANCY_WEIGHT, _MAX_CAL_EXPECTANCY_WEIGHT), 6)


def _signal_raw_score(signal: dict | None, engine: str) -> tuple[float | None, float | None]:
    sig = signal if isinstance(signal, dict) else {}
    if engine == "engine_c":
        return _safe_float(sig.get("conviction")), 1.0
    if engine == "scalp":
        score = _safe_float(sig.get("ai_score"))
        if score is not None:
            return score / 100.0 if score > 1.0 else score, 1.0
        return _safe_float(sig.get("confluenceScore")), _safe_float(sig.get("maxScore")) or 1.0
    combined = _safe_float(sig.get("combinedConviction"))
    if combined is not None:
        return combined, 1.0
    return _safe_float(sig.get("confluenceScore")), _safe_float(sig.get("maxScore"))


def _extract_vms_state(signal: dict | None) -> dict:
    sig = signal if isinstance(signal, dict) else {}
    vms = (
        sig.get("volume_momentum_spread")
        or (sig.get("factor_indicators") or {}).get("volume_momentum_spread")
        or (sig.get("indicators") or {}).get("volume_momentum_spread")
        or (sig.get("engine_a_raw") or {}).get("volume_momentum_spread")
    )
    vms_f = _safe_float(vms)
    direction = str(sig.get("direction") or "").upper()
    if vms_f is None:
        return {"available": False, "value": None, "state": "missing", "aligned": None}
    aligned = (direction == "LONG" and vms_f > 0) or (direction == "SHORT" and vms_f < 0)
    magnitude = abs(vms_f)
    if magnitude >= 1.0:
        state = "aligned_strong" if aligned else "counter_strong"
    elif magnitude >= 0.35:
        state = "aligned_soft" if aligned else "counter_soft"
    else:
        state = "neutral"
    return {
        "available": True,
        "value": round(vms_f, 6),
        "state": state,
        "aligned": aligned,
    }


def _heuristic_spread_percentile(signal: dict | None) -> float | None:
    sig = signal if isinstance(signal, dict) else {}
    spread = _safe_float(sig.get("spread_pips"))
    asset_class = _signal_asset_class(sig)
    if spread is None:
        spread = _safe_float(sig.get("spread"))
    if spread is None:
        return None
    # Coarse heuristic only when empirical spread history is unavailable.
    if asset_class == "forex":
        return round(_clamp((spread / 4.0) * 100.0, 0.0, 100.0), 2)
    if asset_class == "commodity":
        return round(_clamp((spread / 3.0) * 100.0, 0.0, 100.0), 2)
    return round(_clamp((spread / 2.0) * 100.0, 0.0, 100.0), 2)


def _recent_slippage_samples(engine: str, db_path: str, limit: int = 80) -> list[float]:
    if not db_path or not os.path.exists(db_path):
        return []

    def _infer_engine(style: Any, max_score: Any) -> str | None:
        style_norm = _norm_text(style)
        if style_norm == "scalp":
            return "scalp"
        if style_norm in ("engine_c", "consensus"):
            return "engine_c"
        max_f = _safe_float(max_score)
        if max_f is not None:
            return "engine_a"
        if style_norm in ("structural", "naked"):
            return "engine_b"
        return None

    try:
        with sqlite3.connect(db_path, timeout=15.0) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """
                SELECT style, max_score, slippage_bps
                FROM audit_log
                WHERE slippage_bps IS NOT NULL
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(10, int(limit) * 3),),
            ).fetchall()
    except Exception:
        rows = []

    values = []
    for row in rows:
        if _infer_engine(row["style"], row["max_score"]) != engine:
            continue
        slip = _safe_float(row["slippage_bps"])
        if slip is None:
            continue
        values.append(abs(slip))
        if len(values) >= limit:
            break
    return values


def _percentile_rank(value: float | None, samples: list[float]) -> float | None:
    if value is None or not samples:
        return None
    ordered = sorted(float(x) for x in samples)
    count = sum(1 for x in ordered if x <= value)
    return round((count / len(ordered)) * 100.0, 2)


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    engine = _norm_text(record.get("engine")) or "engine_a"
    return {
        "engine": engine if engine in _ENGINES else "engine_a",
        "won": record.get("won"),
        "expectancy_r": _safe_float(record.get("expectancy_r", record.get("resultR", record.get("r_multiple")))),
        "slippage_bps": _safe_float(record.get("slippage_bps")),
        "regime": _norm_text(record.get("regime")),
    }


def _performance_from_records(records: list[dict[str, Any]] | None) -> dict[str, dict]:
    performance = {engine: {"sample_count": 0, "win_rate": None, "expectancy": None, "slippage_bps": None} for engine in _ENGINES}
    for engine in _ENGINES:
        subset = [_normalize_record(r) for r in (records or []) if _norm_text(r.get("engine")) == engine]
        if not subset:
            continue
        wins = [1.0 if bool(r.get("won")) else 0.0 for r in subset if r.get("won") is not None]
        expectancy = [r["expectancy_r"] for r in subset if r["expectancy_r"] is not None]
        slippage = [abs(r["slippage_bps"]) for r in subset if r["slippage_bps"] is not None]
        performance[engine] = {
            "sample_count": len(subset),
            "win_rate": round(sum(wins) / len(wins), 6) if wins else None,
            "expectancy": round(sum(expectancy) / len(expectancy), 6) if expectancy else None,
            "slippage_bps": round(sum(slippage) / len(slippage), 6) if slippage else None,
        }
    return performance


def get_engine_context(signal: dict, db_path: str | None = None, records: list[dict[str, Any]] | None = None) -> dict:
    """Collect the current context block used by the meta-policy."""
    path = db_path if db_path else (_default_db_path() if records is None else None)
    signal_engine = _signal_engine(signal)
    asset_class = _signal_asset_class(signal)
    style = _signal_style(signal)
    regime = _signal_regime(signal)
    raw_score, raw_max = _signal_raw_score(signal, signal_engine)

    calibration = signal.get("calibration") if isinstance(signal.get("calibration"), dict) else None
    if calibration is None:
        if path:
            calibration = predict_calibrated_prob(
                raw_score,
                engine=signal_engine,
                asset_class=asset_class,
                style=style,
                max_score=raw_max,
                db_path=path,
            )
        else:
            calibration = {
                "available": False,
                "calibrated_prob": raw_score,
                "raw_score_norm": raw_score,
                "fallback_reason": "meta_no_db_path",
                "sample_count": 0,
                "bucket_samples": 0,
                "scope_used": None,
            }
    calibration_quality = calibration_quality_summary(calibration)

    if path:
        system_ssi = get_signal_stability_index(db_path=path)
        snapshots = {engine: get_engine_stability_snapshot(engine, db_path=path) for engine in _ENGINES}
    else:
        system_ssi = {"ssi": None}
        snapshots = {
            engine: {
                "engine": engine,
                "available": False,
                "current": {},
                "baseline": {},
                "updatedAt": None,
                "ssi": None,
                "band": None,
                "warnings": [],
                "window": {"current_events": 0, "baseline_events": 0},
            }
            for engine in _ENGINES
        }
    performance = {}
    record_perf = _performance_from_records(records)
    for engine in _ENGINES:
        snap = snapshots[engine]
        current = snap.get("current", {}) if isinstance(snap, dict) else {}
        perf = {
            "sample_count": int((snap.get("window") or {}).get("current_events", 0) or 0),
            "win_rate": _safe_float(current.get("realized_rate", current.get("pass_rate"))),
            "expectancy": _safe_float(current.get("expectancy_mean")),
            "slippage_bps": _safe_float(current.get("adverse_slippage_bps")),
            "ssi": _safe_float(snap.get("ssi")),
            "band": snap.get("band"),
        }
        if record_perf.get(engine, {}).get("sample_count", 0) > 0:
            perf.update(record_perf[engine])
            perf["band"] = snap.get("band")
            perf["ssi"] = _safe_float(snap.get("ssi"))
        performance[engine] = perf

    current_engine_perf = performance.get(signal_engine, {})
    friction_score = _clamp((_safe_float(current_engine_perf.get("slippage_bps")) or 0.0) / 20.0, 0.0, 1.0)
    current_slippage = _safe_float(signal.get("slippageBps") or signal.get("expectedSlippageBps"))
    slippage_percentile = _percentile_rank(current_slippage, _recent_slippage_samples(signal_engine, path)) if path else None
    spread_or_slippage_percentile = slippage_percentile
    if spread_or_slippage_percentile is None:
        spread_or_slippage_percentile = _heuristic_spread_percentile(signal)

    return {
        "signal_engine": signal_engine,
        "asset_class": asset_class,
        "style": style,
        "regime": regime,
        "direction": str(signal.get("direction") or "").upper() or None,
        "raw_score": raw_score,
        "raw_max_score": raw_max,
        "signal_stability_index": {
            "system": _safe_float(system_ssi.get("ssi")),
            "per_engine": {engine: _safe_float(snapshots[engine].get("ssi")) for engine in _ENGINES},
        },
        "stability_snapshots": snapshots,
        "calibration": calibration,
        "calibration_quality": calibration_quality,
        "recent_performance_by_engine": performance,
        "execution_friction_state": {
            "current_engine": signal_engine,
            "slippage_bps": current_engine_perf.get("slippage_bps"),
            "friction_score": round(friction_score, 6),
        },
        "spread_slippage_percentile": spread_or_slippage_percentile,
        "vms_state": _extract_vms_state(signal) if asset_class == "crypto" else {"available": False, "state": "n/a"},
        "warnings": [],
    }


def compute_engine_quality(engine: str, context: dict, records: list[dict[str, Any]] | None = None) -> dict:
    """Score one engine's recent quality using bounded, explainable adjustments."""
    perf = ((context or {}).get("recent_performance_by_engine") or {}).get(engine, {})
    sample_count = int(perf.get("sample_count", 0) or 0)
    ssi = _safe_float(perf.get("ssi"))
    win_rate = _safe_float(perf.get("win_rate"))
    expectancy = _safe_float(perf.get("expectancy"))
    slippage = _safe_float(perf.get("slippage_bps"))
    friction_score = _clamp((slippage or 0.0) / 20.0, 0.0, 1.0)
    adaptive = sample_count >= _MIN_SAMPLES_FOR_ADAPTATION

    adjustments = {}
    if adaptive and ssi is not None:
        adjustments["stability"] = round(_clamp((ssi - 50.0) / 50.0, -1.0, 1.0) * 0.10, 6)
    else:
        adjustments["stability"] = 0.0
    if adaptive and win_rate is not None:
        adjustments["win_rate"] = round(_clamp((win_rate - 0.5) / 0.20, -1.0, 1.0) * 0.12, 6)
    else:
        adjustments["win_rate"] = 0.0
    cal_prob = _safe_float(((context or {}).get("calibration") or {}).get("calibrated_prob"))
    cal_weight = _engine_calibrated_expectancy_weight(engine, context, adaptive)
    if adaptive and (expectancy is not None or cal_prob is not None):
        exp_component = _clamp((expectancy or 0.0) / 0.50, -1.0, 1.0)
        cal_component = _clamp(((cal_prob - 0.5) / 0.35), -1.0, 1.0) if cal_prob is not None else exp_component
        blended_component = ((1.0 - cal_weight) * exp_component) + (cal_weight * cal_component)
        adjustments["expectancy"] = round(blended_component * 0.10, 6)
    else:
        adjustments["expectancy"] = 0.0
    adjustments["friction"] = round((-friction_score * 0.10) if adaptive else 0.0, 6)

    cal_quality = ((context or {}).get("calibration_quality") or {}).get("quality_score")
    if adaptive and engine == (context or {}).get("signal_engine") and cal_quality is not None:
        adjustments["calibration_quality"] = round((_clamp(float(cal_quality), 0.0, 1.0) - 0.5) * 0.08, 6)
    else:
        adjustments["calibration_quality"] = 0.0

    vms_state = (context or {}).get("vms_state") or {}
    if adaptive and (context or {}).get("asset_class") == "crypto" and engine in ("engine_a", "engine_c"):
        if vms_state.get("state") == "aligned_strong":
            adjustments["vms"] = 0.04
        elif vms_state.get("state") == "counter_strong":
            adjustments["vms"] = -0.04
        elif vms_state.get("state") == "aligned_soft":
            adjustments["vms"] = 0.02
        elif vms_state.get("state") == "counter_soft":
            adjustments["vms"] = -0.02
        else:
            adjustments["vms"] = 0.0
    else:
        adjustments["vms"] = 0.0

    total_adjustment = round(sum(adjustments.values()), 6)
    quality_score = round(_clamp(0.50 + total_adjustment, 0.15, 0.85), 6)
    suspended = bool(
        sample_count >= _MIN_SAMPLES_FOR_SUSPENSION
        and (ssi is not None and ssi < 35.0)
        and ((win_rate is not None and win_rate < 0.35) or (expectancy is not None and expectancy < -0.10))
        and friction_score >= 0.70
    )

    threshold_delta = 0.0
    if adaptive:
        if suspended:
            threshold_delta = 0.05
        elif quality_score < 0.40:
            threshold_delta = 0.03
        elif quality_score > 0.70:
            threshold_delta = -0.02

    notes = []
    if not adaptive:
        notes.append(f"insufficient_samples ({sample_count} < {_MIN_SAMPLES_FOR_ADAPTATION})")
    if suspended:
        notes.append("temporary_suspension_candidate")

    return {
        "engine": engine,
        "adaptive": adaptive,
        "sample_count": sample_count,
        "quality_score": quality_score,
        "adjustments": adjustments,
        "threshold_delta": round(_clamp(threshold_delta, -0.03, 0.05), 6),
        "calibrated_expectancy_weight": cal_weight,
        "suspended": suspended,
        "win_rate": win_rate,
        "expectancy": expectancy,
        "ssi": ssi,
        "friction_score": round(friction_score, 6),
        "notes": notes,
    }


def _allocate_bounded_weights(raw: dict[str, float], base: dict[str, float]) -> dict[str, float]:
    remaining = list(_ENGINES)
    fixed = {}
    target = 1.0
    raw_scores = {engine: max(0.0001, float(raw.get(engine, base[engine]))) for engine in _ENGINES}

    while remaining:
        subtotal = sum(raw_scores[engine] for engine in remaining) or float(len(remaining))
        changed = False
        provisional = {engine: target * (raw_scores[engine] / subtotal) for engine in remaining}
        for engine in list(remaining):
            lower = max(_MIN_WEIGHT_CAPS[engine], base[engine] - _MAX_WEIGHT_STEP)
            upper = min(_MAX_WEIGHT_CAPS[engine], base[engine] + _MAX_WEIGHT_STEP)
            if provisional[engine] < lower:
                fixed[engine] = lower
                target -= lower
                remaining.remove(engine)
                changed = True
            elif provisional[engine] > upper:
                fixed[engine] = upper
                target -= upper
                remaining.remove(engine)
                changed = True
        if not changed:
            for engine in remaining:
                fixed[engine] = provisional[engine]
            break

    total = sum(fixed.values()) or 1.0
    return {engine: round(fixed.get(engine, base[engine]) / total, 6) for engine in _ENGINES}


def get_dynamic_engine_weights(context: dict, records: list[dict[str, Any]] | None = None) -> dict:
    """Return conservative dynamic engine weights plus threshold/suspension hints."""
    style = (context or {}).get("style") or "auto"
    base_weights = dict(_BASE_WEIGHTS_BY_STYLE.get(style, _BASE_WEIGHTS_BY_STYLE["auto"]))
    qualities = {engine: compute_engine_quality(engine, context, records) for engine in _ENGINES}
    regime_style_weighting = _regime_style_weight_multipliers(context, qualities)

    raw_weights = {}
    for engine in _ENGINES:
        quality = qualities[engine]["quality_score"]
        adaptive = qualities[engine]["adaptive"]
        multiplier = 1.0 + ((quality - 0.5) * 0.5) if adaptive else 1.0
        if qualities[engine]["suspended"]:
            multiplier *= 0.50
        multiplier *= float((regime_style_weighting.get("multipliers") or {}).get(engine, 1.0))
        raw_weights[engine] = base_weights[engine] * multiplier

    weights = _allocate_bounded_weights(raw_weights, base_weights)
    cal_expectancy_weighting = {
        engine: qualities[engine].get("calibrated_expectancy_weight", 0.0) for engine in _ENGINES
    }
    return {
        "weights": weights,
        "baseWeights": base_weights,
        "engineQualities": qualities,
        "regimeStyleWeighting": regime_style_weighting,
        "calibratedExpectancyWeighting": cal_expectancy_weighting,
        "thresholdAdjustments": {engine: qualities[engine]["threshold_delta"] for engine in _ENGINES},
        "suspensionFlags": {engine: qualities[engine]["suspended"] for engine in _ENGINES},
        "adaptiveAlpha": {
            "surfaces": list(_ADAPTIVE_ALPHA_SURFACES),
            "enabled": True,
        },
        "fixedSafetyBoundary": {
            "nonAdaptiveInvariants": list(FIXED_SAFETY_INVARIANTS),
            "guardian_enforced": True,
            "notes": [
                "risk controls and execution safety are fixed outside meta policy",
                "adaptive policy can tune trust/weighting but not safety caps",
            ],
        },
        "constraints": {
            "minWeightCaps": dict(_MIN_WEIGHT_CAPS),
            "maxWeightCaps": dict(_MAX_WEIGHT_CAPS),
            "minSamplesForAdaptation": _MIN_SAMPLES_FOR_ADAPTATION,
            "maxWeightStep": _MAX_WEIGHT_STEP,
            "calibratedExpectancyWeightRange": [
                _MIN_CAL_EXPECTANCY_WEIGHT,
                _MAX_CAL_EXPECTANCY_WEIGHT,
            ],
        },
        "notes": [
            "bounded_explainable_policy",
            "no_black_box_model",
            "engine_c_remains_consensus_engine",
            "adaptive_alpha_and_fixed_safety_explicitly_separated",
        ],
    }


def apply_meta_policy(signal: dict, weights: dict | None) -> dict:
    """Attach meta-policy outputs to a signal/result without mutating raw score fields."""
    policy = weights if isinstance(weights, dict) else {}
    signal_engine = _signal_engine(signal)
    out = dict(signal or {})
    out["metaDynamicWeights"] = dict(policy.get("weights", {}))
    out["metaThresholdAdjustments"] = dict(policy.get("thresholdAdjustments", {}))
    out["metaSuspensionFlags"] = dict(policy.get("suspensionFlags", {}))
    out["metaEngineWeight"] = (policy.get("weights") or {}).get(signal_engine)
    out["metaThresholdDelta"] = (policy.get("thresholdAdjustments") or {}).get(signal_engine, 0.0)
    out["metaSuspended"] = bool((policy.get("suspensionFlags") or {}).get(signal_engine, False))
    out["metaPolicy"] = policy
    return out


def meta_report(
    signal: dict,
    records: list[dict[str, Any]] | None = None,
    db_path: str | None = None,
) -> dict:
    """Build a compact auditable meta-policy report."""
    context = get_engine_context(signal, db_path=db_path, records=records)
    policy = get_dynamic_engine_weights(context, records=records)
    return {
        "context": {
            "signal_engine": context.get("signal_engine"),
            "asset_class": context.get("asset_class"),
            "style": context.get("style"),
            "regime": context.get("regime"),
            "system_ssi": ((context.get("signal_stability_index") or {}).get("system")),
            "spread_slippage_percentile": context.get("spread_slippage_percentile"),
            "calibration_quality": context.get("calibration_quality"),
            "vms_state": context.get("vms_state"),
        },
        **policy,
    }
