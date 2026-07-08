"""Cross-engine context assembly for signal cards and Marcus review payloads.

Engine A and Engine B remain independent. This module only mirrors native
fields into explicit namespaces and derives advisory cross-engine warnings.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any


CROSS_ENGINE_CONTEXT_COLUMNS = [
    "timestamp",
    "pair",
    "primary_card_engine",
    "primary_engine_direction",
    "primary_engine_score",
    "primary_engine_actionable",
    "secondary_engine_direction",
    "secondary_engine_actionable",
    "cross_engine_alignment",
    "cross_engine_conflict",
    "cross_engine_warnings",
    "native_score_changed_by_cross_engine",
    "native_actionable_changed_by_cross_engine",
    "card_hidden_by_cross_engine",
    "levels_removed_by_cross_engine",
    "consistency_pass",
    "inconsistency_reasons",
]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _norm_direction(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if text in {"LONG", "SHORT"} else None


def _zone_bounds(zone: Any) -> tuple[float | None, float | None]:
    zone = _as_dict(zone)
    low = _num(_first_present(zone.get("lower"), zone.get("low"), zone.get("bottom")))
    high = _num(_first_present(zone.get("upper"), zone.get("high"), zone.get("top")))
    if low is None and high is not None:
        low = high
    if high is None and low is not None:
        high = low
    return low, high


def _in_zone(price: float | None, zone: Any) -> bool:
    low, high = _zone_bounds(zone)
    if price is None or low is None or high is None:
        return False
    lo, hi = (low, high) if low <= high else (high, low)
    return lo <= price <= hi


def _engine_b_payload(signal: dict[str, Any]) -> dict[str, Any]:
    return (
        _as_dict(signal.get("engine_b"))
        or _as_dict(signal.get("engine_b_overlay"))
        or _as_dict(signal.get("naked_data"))
        or _as_dict(signal.get("engineB"))
    )


def _engine_a_payload(signal: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(signal.get("engine_a")) or _as_dict(signal.get("engineA"))


def _structure_direction(engine_b: dict[str, Any]) -> str | None:
    direct = _norm_direction(
        _first_present(
            engine_b.get("direction"),
            engine_b.get("independent_direction"),
            engine_b.get("engine_b_independent_direction"),
        )
    )
    if direct:
        return direct
    seq = str(
        _first_present(
            engine_b.get("current_swing_sequence"),
            engine_b.get("swing_sequence"),
            engine_b.get("macro_swing_sequence"),
        )
        or ""
    ).upper()
    if "HH_HL" in seq:
        return "LONG"
    if "LH_LL" in seq:
        return "SHORT"
    return None


def _engine_a_native(signal: dict[str, Any]) -> dict[str, Any]:
    engine_a = _engine_a_payload(signal)
    score = _num(
        _first_present(
            engine_a.get("engine_a_native_score"),
            engine_a.get("score"),
            engine_a.get("confluenceScore"),
            engine_a.get("final_score"),
            signal.get("engine_a_native_score"),
            signal.get("confluenceScore"),
            signal.get("score"),
        )
    )
    max_score = _num(_first_present(engine_a.get("max_score"), engine_a.get("maxScore"), signal.get("maxScore")))
    confidence = _first_present(
        engine_a.get("engine_a_native_confidence"),
        engine_a.get("confidence"),
        signal.get("engine_a_native_confidence"),
        signal.get("confidence"),
        signal.get("confidenceDetail", {}).get("confidence")
        if isinstance(signal.get("confidenceDetail"), dict)
        else None,
    )
    actionable = _first_present(
        engine_a.get("engine_a_native_actionable"),
        engine_a.get("actionable"),
        signal.get("engine_a_native_actionable"),
        signal.get("engineATradeEnabled"),
        signal.get("qualified"),
        signal.get("trade"),
        signal.get("executable"),
    )
    reasons = _first_present(
        engine_a.get("engine_a_native_reasons"),
        engine_a.get("reasons"),
        signal.get("engine_a_native_reasons"),
        signal.get("factorDiagnostics"),
        signal.get("factor_diagnostics"),
        signal.get("predicates"),
        signal.get("rejectionReasons"),
    )
    return {
        "engine_a_native_signal": _first_present(engine_a.get("signal"), signal.get("signalClass"), signal.get("decision")),
        "engine_a_native_direction": _norm_direction(
            _first_present(engine_a.get("direction"), signal.get("engine_a_native_direction"), signal.get("direction"))
        ),
        "engine_a_native_score": score,
        "engine_a_native_max_score": max_score,
        "engine_a_native_confidence": confidence,
        "engine_a_native_validation_status": _first_present(
            engine_a.get("validation_status"),
            engine_a.get("validationStatus"),
            signal.get("engine_a_native_validation_status"),
            signal.get("validationStatus"),
            signal.get("validation_status"),
        ),
        "engine_a_native_actionable": bool(actionable) if actionable is not None else None,
        "engine_a_native_reasons": reasons if reasons is not None else [],
        "engine_a_native_entry": _num(_first_present(engine_a.get("entry"), signal.get("entry"), signal.get("price"))),
        "engine_a_native_sl": _num(_first_present(engine_a.get("sl"), engine_a.get("stop_loss"), signal.get("sl"))),
        "engine_a_native_tp1": _num(_first_present(engine_a.get("tp1"), engine_a.get("tp"), signal.get("tp1"), signal.get("tp"))),
        "engine_a_native_tp2": _num(_first_present(engine_a.get("tp2"), signal.get("tp2"))),
    }


def _engine_b_native(signal: dict[str, Any]) -> dict[str, Any]:
    engine_b = _engine_b_payload(signal)
    score = _num(
        _first_present(
            engine_b.get("engine_b_native_score"),
            engine_b.get("score"),
            engine_b.get("structure_score"),
            signal.get("engine_b_native_score"),
            signal.get("engine_b_score"),
        )
    )
    max_score = _num(_first_present(engine_b.get("max_possible"), engine_b.get("maxScore"), signal.get("engine_b_max")))
    confidence = _first_present(
        engine_b.get("engine_b_native_confidence"),
        engine_b.get("confidence"),
        engine_b.get("confidence_score"),
        signal.get("engine_b_native_confidence"),
    )
    actionable = _first_present(
        engine_b.get("engine_b_native_actionable"),
        engine_b.get("engine_b_canonical_actionable"),
        engine_b.get("is_actionable"),
        signal.get("engine_b_native_actionable"),
        signal.get("engine_b_canonical_actionable"),
        signal.get("is_actionable"),
    )
    return {
        "engine_b_native_signal": _first_present(engine_b.get("signal"), engine_b.get("structural_verdict"), engine_b.get("verdict")),
        "engine_b_native_direction": _structure_direction(engine_b),
        "engine_b_native_score": score,
        "engine_b_native_max_score": max_score,
        "engine_b_native_confidence": confidence,
        "engine_b_native_actionable": bool(actionable) if actionable is not None else None,
        "engine_b_native_reasons": _first_present(
            engine_b.get("engine_b_native_reasons"),
            engine_b.get("engine_b_rejection_reasons"),
            engine_b.get("canonical_secondary_reject_reasons"),
            engine_b.get("reason_codes"),
            engine_b.get("engine_b_diagnostics", {}).get("reason_codes")
            if isinstance(engine_b.get("engine_b_diagnostics"), dict)
            else None,
            [],
        ),
        "engine_b_native_entry": _num(_first_present(engine_b.get("entry"), engine_b.get("current_price"), signal.get("entry"), signal.get("price"))),
        "engine_b_native_sl": _num(
            _first_present(engine_b.get("execution_sl"), engine_b.get("recommended_sl"), engine_b.get("recommended_stop_loss"), engine_b.get("sl"))
        ),
        "engine_b_native_tp1": _num(
            _first_present(engine_b.get("execution_tp1"), engine_b.get("execution_tp"), engine_b.get("recommended_tp"), engine_b.get("recommended_take_profit"), engine_b.get("tp1"), engine_b.get("tp"))
        ),
        "engine_b_native_tp2": _num(_first_present(engine_b.get("execution_tp2"), engine_b.get("tp2"))),
    }


def _risk_context(signal: dict[str, Any]) -> dict[str, Any]:
    rr1 = _num(_first_present(signal.get("rr1"), signal.get("rr")))
    style_min_rr = _num(_first_present(signal.get("min_rr"), signal.get("style_min_rr")))
    return {
        "rr1": rr1,
        "rr2": _num(signal.get("rr2")),
        "style_min_rr": style_min_rr,
        "engine_a_rr1_below_style_min": bool(
            rr1 is not None and style_min_rr is not None and rr1 < style_min_rr
        ),
    }


def _cross_context(
    signal: dict[str, Any],
    engine_a_native: dict[str, Any],
    engine_b_native: dict[str, Any],
) -> dict[str, Any]:
    engine_b = _engine_b_payload(signal)
    a_dir = engine_a_native.get("engine_a_native_direction")
    b_dir = engine_b_native.get("engine_b_native_direction")
    a_entry = engine_a_native.get("engine_a_native_entry")
    warnings: list[str] = []
    risk_notes: list[str] = []
    location_warning = None
    rr_warning = None
    structure_warning = None
    learning_warning = None

    if a_dir == "SHORT" and _in_zone(a_entry, engine_b.get("nearest_support_zone")):
        location_warning = "Engine B context: Engine A SHORT entry is inside Engine B support zone."
        warnings.append(location_warning)
    if a_dir == "LONG" and _in_zone(a_entry, engine_b.get("nearest_resistance_zone")):
        location_warning = "Engine B context: Engine A LONG entry is inside Engine B resistance zone."
        warnings.append(location_warning)

    conflict = None
    if a_dir and b_dir and a_dir != b_dir:
        conflict = "Engine direction conflict."
        structure_warning = "Engine structure disagreement between Engine A and Engine B."
        warnings.append(structure_warning)

    risk = _risk_context(signal)
    if risk["engine_a_rr1_below_style_min"]:
        rr_warning = "Engine A RR1 below configured style minimum."
        risk_notes.append(rr_warning)

    if engine_b.get("learning_context_negative"):
        learning_warning = "Engine B context: learning history adds caution."
        warnings.append(learning_warning)

    alignment = "aligned" if a_dir and b_dir and a_dir == b_dir else "conflict" if conflict else "unknown"
    return {
        "cross_engine_alignment": alignment,
        "cross_engine_conflict": conflict,
        "cross_engine_warnings": warnings,
        "cross_engine_risk_notes": risk_notes,
        "cross_engine_location_warning": location_warning,
        "cross_engine_rr_warning": rr_warning,
        "cross_engine_structure_warning": structure_warning,
        "cross_engine_learning_warning": learning_warning,
        "native_score_changed_by_cross_engine": False,
        "native_actionable_changed_by_cross_engine": False,
        "card_hidden_by_cross_engine": False,
        "levels_removed_by_cross_engine": False,
        "consistency_pass": True,
        "inconsistency_reasons": [],
    }


def build_cross_engine_context(signal: dict[str, Any], primary_card_engine: str | None = None) -> dict[str, Any]:
    """Return explicit native engine contexts plus advisory cross-engine context."""
    signal = signal if isinstance(signal, dict) else {}
    engine_a_native = _engine_a_native(signal)
    engine_b_native = _engine_b_native(signal)
    cross = _cross_context(signal, engine_a_native, engine_b_native)
    risk = _risk_context(signal)
    primary = str(primary_card_engine or signal.get("engine_source") or signal.get("engine") or "engine_a").lower()
    if primary in {"a", "engine_a", "engine-a"}:
        primary = "engine_a"
    elif primary in {"b", "engine_b", "engine-b", "naked"}:
        primary = "engine_b"
    output = {
        "timestamp": signal.get("timestamp") or signal.get("ts"),
        "pair": signal.get("pair") or signal.get("display") or signal.get("symbol"),
        "primary_card_engine": primary,
        "engine_a_native_context": engine_a_native,
        "engine_b_native_context": engine_b_native,
        "cross_engine_context": cross,
        "risk_context": risk,
        "learning_context": _as_dict(signal.get("learning_context")) or _as_dict(signal.get("learning_ctx")),
    }
    output["engine_a_native_warning"] = cross.get("cross_engine_rr_warning")
    return output


def _report_row(context: dict[str, Any]) -> dict[str, Any]:
    a = context.get("engine_a_native_context") or {}
    b = context.get("engine_b_native_context") or {}
    cross = context.get("cross_engine_context") or {}
    primary = context.get("primary_card_engine") or "engine_a"
    if primary == "engine_b":
        primary_ctx, secondary_ctx = b, a
        p_prefix, s_prefix = "engine_b", "engine_a"
    else:
        primary_ctx, secondary_ctx = a, b
        p_prefix, s_prefix = "engine_a", "engine_b"
    return {
        "timestamp": context.get("timestamp") or "",
        "pair": context.get("pair") or "",
        "primary_card_engine": primary,
        "primary_engine_direction": primary_ctx.get(f"{p_prefix}_native_direction"),
        "primary_engine_score": primary_ctx.get(f"{p_prefix}_native_score"),
        "primary_engine_actionable": primary_ctx.get(f"{p_prefix}_native_actionable"),
        "secondary_engine_direction": secondary_ctx.get(f"{s_prefix}_native_direction"),
        "secondary_engine_actionable": secondary_ctx.get(f"{s_prefix}_native_actionable"),
        "cross_engine_alignment": cross.get("cross_engine_alignment"),
        "cross_engine_conflict": cross.get("cross_engine_conflict"),
        "cross_engine_warnings": "; ".join(str(x) for x in _as_list(cross.get("cross_engine_warnings"))),
        "native_score_changed_by_cross_engine": cross.get("native_score_changed_by_cross_engine"),
        "native_actionable_changed_by_cross_engine": cross.get("native_actionable_changed_by_cross_engine"),
        "card_hidden_by_cross_engine": cross.get("card_hidden_by_cross_engine"),
        "levels_removed_by_cross_engine": cross.get("levels_removed_by_cross_engine"),
        "consistency_pass": cross.get("consistency_pass"),
        "inconsistency_reasons": "; ".join(str(x) for x in _as_list(cross.get("inconsistency_reasons"))),
    }


def write_cross_engine_context_consistency_report(
    contexts: list[dict[str, Any]],
    csv_path: str | Path = "reports/cross_engine_context_consistency.csv",
) -> Path:
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CROSS_ENGINE_CONTEXT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for context in contexts:
            writer.writerow(_report_row(context))
    return path


def build_cross_engine_context_consistency_summary(
    csv_path: str | Path = "reports/cross_engine_context_consistency.csv",
    out_path: str | Path = "reports/cross_engine_context_consistency_summary.md",
) -> str:
    csv_path = Path(csv_path)
    out_path = Path(out_path)
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8"))) if csv_path.exists() else []
    total = len(rows)
    failures = [row for row in rows if str(row.get("consistency_pass") or "").lower() not in {"true", "1", "yes"}]
    reasons = Counter()
    for row in failures:
        for reason in str(row.get("inconsistency_reasons") or "").split(";"):
            reason = reason.strip()
            if reason:
                reasons[reason] += 1
    lines = [
        "# Cross-engine Context Consistency Summary",
        "",
        f"- Total cards checked: **{total}**",
        f"- Consistency failures: **{len(failures)}**",
        f"- Native score changed by cross-engine context: **{sum(str(r.get('native_score_changed_by_cross_engine')).lower() == 'true' for r in rows)}**",
        f"- Native actionability changed by cross-engine context: **{sum(str(r.get('native_actionable_changed_by_cross_engine')).lower() == 'true' for r in rows)}**",
        f"- Cards hidden by cross-engine context: **{sum(str(r.get('card_hidden_by_cross_engine')).lower() == 'true' for r in rows)}**",
        f"- Levels removed by cross-engine context: **{sum(str(r.get('levels_removed_by_cross_engine')).lower() == 'true' for r in rows)}**",
        "",
        "## Top inconsistency reasons",
        "",
    ]
    if reasons:
        lines.extend(f"- {reason}: {count}" for reason, count in reasons.most_common(20))
    else:
        lines.append("- (none recorded)")
    text = "\n".join(lines) + "\n"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return text
