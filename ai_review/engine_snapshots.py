"""Read-only engine score snapshots from analyze_pair signal (no scoring changes)."""

from __future__ import annotations

from typing import Any


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_float(*values: Any) -> float | None:
    for value in values:
        parsed = _to_float(value)
        if parsed is not None:
            return parsed
    return None


def _norm(score: float | None, max_score: float | None) -> float | None:
    if score is None or max_score is None or max_score <= 0:
        return None
    return round(min(100.0, max(0.0, (score / max_score) * 100.0)), 2)


def _engine_dict(signal: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        raw = signal.get(key)
        if isinstance(raw, dict):
            return raw
    return {}


def _active_factors_from_signal(signal: dict[str, Any]) -> list[str] | None:
    fs = signal.get("factorScores") or signal.get("factor_scores")
    if not isinstance(fs, dict):
        fd = signal.get("factorDiagnostics") or signal.get("factor_diagnostics")
        if isinstance(fd, dict):
            fs = fd.get("factorScores") or fd.get("factor_scores")
    if not isinstance(fs, dict):
        return None
    active = [
        str(k)
        for k, v in fs.items()
        if _to_float(v) is not None and abs(float(v)) > 1e-9
    ]
    return active or None


def extract_engine_a_snapshot(
    engine_a_ctx: dict[str, Any],
    signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    score = _first_float(
        engine_a_ctx.get("confluence_score"),
        (signal or {}).get("confluenceScore"),
    )
    max_score = _first_float(
        engine_a_ctx.get("max_score_override"),
        (signal or {}).get("maxScore"),
    )
    threshold = _first_float(
        engine_a_ctx.get("threshold"),
        (signal or {}).get("threshold"),
        (signal or {}).get("liveThreshold"),
        (signal or {}).get("scanThresholdEffective"),
    )
    passed = engine_a_ctx.get("passed")
    if passed is None and signal:
        direction = str(signal.get("direction") or "NONE").upper()
        if score is not None and threshold is not None and direction in ("LONG", "SHORT"):
            passed = score >= threshold
        else:
            passed = None

    active = _active_factors_from_signal(signal or {})
    if active is None and isinstance(engine_a_ctx.get("factor_diagnostics"), dict):
        fd = engine_a_ctx["factor_diagnostics"]
        pseudo = {
            k: fd.get(k)
            for k in ("trend", "momentum", "addon", "research_lab", "mean_reversion")
            if k in fd
        }
        if pseudo:
            active = [
                str(k)
                for k, v in pseudo.items()
                if _to_float(v) is not None and abs(float(v)) > 1e-9
            ] or None

    return {
        "score": score,
        "maxScore": max_score,
        "threshold": threshold,
        "normalizedScore": _norm(score, max_score),
        "passed": passed if passed is None else bool(passed),
        "direction": engine_a_ctx.get("direction") or (signal or {}).get("direction"),
        "activeFactors": active,
    }


def extract_engine_b_snapshot(signal: dict[str, Any]) -> dict[str, Any]:
    eb = _engine_dict(signal, "engine_b", "engine_b_overlay", "naked_data", "engineB")
    conf_b = signal.get("_threshold_audit_b_conf")
    if not isinstance(conf_b, dict):
        conf_b = {}

    score = _first_float(
        eb.get("score"),
        eb.get("confidence_score"),
        eb.get("structure_score"),
        conf_b.get("score"),
        signal.get("engine_b_score"),
    )
    max_score = _first_float(
        eb.get("max_possible"),
        eb.get("max_score"),
        conf_b.get("max_possible"),
        signal.get("engine_b_max"),
    )
    threshold = _first_float(
        signal.get("engine_b_min_score_scaled"),
        eb.get("min_score"),
        (signal.get("_threshold_audit_b_style_profile") or {}).get("min_score")
        if isinstance(signal.get("_threshold_audit_b_style_profile"), dict)
        else None,
    )

    passed = eb.get("confidence_passed")
    if passed is None:
        passed = eb.get("passed")
    if passed is None:
        passed = eb.get("checklist_passed")
    if passed is None:
        passed = eb.get("is_actionable")
    if isinstance(passed, bool):
        passed_val: bool | None = passed
    else:
        passed_val = None

    direction = (
        eb.get("direction")
        or eb.get("independent_direction")
        or eb.get("engine_b_independent_direction")
        or signal.get("engine_b_direction")
    )

    return {
        "score": score,
        "maxScore": max_score,
        "threshold": threshold,
        "normalizedScore": _norm(score, max_score),
        "passed": passed_val,
        "direction": str(direction).upper() if direction else None,
    }


def extract_engine_c_snapshot(signal: dict[str, Any]) -> dict[str, Any]:
    ec = _engine_dict(signal, "engine_c", "engineC")
    decision = (
        ec.get("decision_state")
        or ec.get("decisionState")
        or signal.get("decision_state")
        or signal.get("decisionState")
    )
    score = _first_float(
        ec.get("score"),
        ec.get("combined_conviction"),
        ec.get("combinedConviction"),
        ec.get("conviction"),
        signal.get("combinedConviction"),
        signal.get("conviction"),
    )
    max_score = _first_float(ec.get("max_score"), ec.get("maxScore"))
    threshold = _first_float(
        ec.get("threshold"),
        ec.get("execute_threshold"),
        ec.get("rel_execute"),
        signal.get("engine_c_threshold"),
    )
    direction = ec.get("direction") or signal.get("engine_c_direction")

    return {
        "score": score,
        "maxScore": max_score,
        "threshold": threshold,
        "normalizedScore": _norm(score, max_score),
        "decisionState": str(decision) if decision else None,
        "direction": str(direction).upper() if direction else None,
    }


def extract_engine_d_snapshot(signal: dict[str, Any]) -> dict[str, Any]:
    ed = _engine_dict(signal, "engine_d", "engineD", "scalp")
    score = _first_float(
        ed.get("score"),
        ed.get("scalp_score"),
        signal.get("scalp_score"),
        signal.get("ai_score"),
        signal.get("engine_d_score"),
    )
    max_score = _first_float(ed.get("max_score"), ed.get("maxScore"))
    threshold = _first_float(
        ed.get("threshold"),
        ed.get("min_score"),
        signal.get("scalp_min_score"),
    )
    setup_type = (
        ed.get("setup_type")
        or ed.get("setupType")
        or signal.get("setup_type")
        or signal.get("setupType")
        or signal.get("zone_type")
        or signal.get("zoneType")
    )
    direction = (
        ed.get("direction")
        or signal.get("scalp_direction")
        or signal.get("direction")
    )

    return {
        "score": score,
        "maxScore": max_score,
        "threshold": threshold,
        "normalizedScore": _norm(score, max_score),
        "setupType": str(setup_type) if setup_type else None,
        "direction": str(direction).upper() if direction else None,
    }


def extract_engine_snapshots(
    signal: dict[str, Any],
    engine_a_ctx: dict[str, Any],
) -> dict[str, Any]:
    return {
        "engineA": extract_engine_a_snapshot(engine_a_ctx, signal),
        "engineB": extract_engine_b_snapshot(signal),
        "engineC": extract_engine_c_snapshot(signal),
        "engineD": extract_engine_d_snapshot(signal),
    }
