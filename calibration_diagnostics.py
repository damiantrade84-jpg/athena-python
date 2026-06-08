"""Diagnostic-only Engine A/B calibration telemetry.

This module is report-only. It must not change scan, backtest, paper, live,
risk, or execution decisions.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import CONFIG

DEFAULT_DIAGNOSTICS_PATH = Path("logs") / "calibration_diagnostics" / "calibration_events.jsonl"
_WRITE_LOCK = threading.Lock()


def calibration_diagnostics_enabled() -> bool:
    return bool(CONFIG.get("CALIBRATION_DIAGNOSTICS_ENABLED", False))


def _diagnostics_path(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path)
    configured = CONFIG.get("CALIBRATION_DIAGNOSTICS_PATH")
    if configured:
        return Path(str(configured))
    return DEFAULT_DIAGNOSTICS_PATH


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        out = float(value)
        if out != out:
            return default
        return out
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return bool(value)


def _first_failure_reason(reasons: Any) -> str | None:
    if isinstance(reasons, (list, tuple)):
        for item in reasons:
            if item:
                return str(item)
        return None
    if reasons:
        return str(reasons)
    return None


def _engine_b_regime_multiplier(regime_label: str | None, asset_type: str = "") -> float:
    """Delegate to market_structure so calibration rows match live gating."""
    from market_structure import _engine_b_regime_gate

    return _engine_b_regime_gate(regime_label, asset_type)


def _engine_b_level_cohort(level_mode: str | None) -> str:
    """Delegate to market_structure so cohort names stay in one place."""
    from market_structure import engine_b_level_cohort

    return engine_b_level_cohort(level_mode)


def _engine_b_cohort_performance(rows: list[dict[str, Any]]) -> dict[str, dict]:
    """Aggregate Engine B calibration rows by execution-level cohort."""
    from market_structure import aggregate_engine_b_level_cohorts

    engine_b_rows = [
        row for row in rows if isinstance(row, dict) and row.get("engine") == "engine_b"
    ]
    return aggregate_engine_b_level_cohorts(engine_b_rows)


def record_engine_a_analyze_skip(
    pair: dict[str, Any],
    *,
    abort_reason: str,
    detail: str | None = None,
    score_group: str | None = None,
    path: Path | str | None = None,
) -> bool:
    """Record Engine A skip when analyze_pair aborts before calc_confluence."""
    if not calibration_diagnostics_enabled():
        return False
    try:
        from scoring import get_score_threshold

        threshold = get_score_threshold(pair)
    except Exception:
        threshold = None
    row = {
        "engine": "engine_a",
        "pair": pair.get("display") or pair.get("symbol"),
        "symbol": pair.get("symbol"),
        "asset_class": pair.get("type"),
        "score_group": score_group or pair.get("score_group"),
        "passed": False,
        "failure_reason": abort_reason,
        "abort_reason": abort_reason,
        "threshold": threshold,
        "final_score": 0.0,
        "raw_score": 0.0,
        "trend_score": 0.0,
        "skip_detail": detail,
    }
    return record_calibration_diagnostic(row, path=path)


def record_calibration_diagnostic(
    row: dict[str, Any],
    path: Path | str | None = None,
) -> bool:
    """Append a calibration diagnostic row when the config flag is enabled."""
    if not calibration_diagnostics_enabled():
        return False
    out = _diagnostics_path(path)
    payload = dict(row)
    payload.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    with _WRITE_LOCK:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
    return True


def _resolve_score_group(pair: dict[str, Any]) -> str | None:
    try:
        from scoring import get_pair_score_group

        return get_pair_score_group(pair)
    except Exception:
        return None


def build_engine_a_calibration_row(
    *,
    pair: dict[str, Any],
    result: dict[str, Any],
    factor_result: dict[str, Any] | None = None,
    threshold: float | None,
    passed: bool,
    failure_reason: str | None = None,
    timeframe: str | None = None,
) -> dict[str, Any]:
    factor_result = factor_result if isinstance(factor_result, dict) else {}
    factor_diag = result.get("factorDiagnostics") or {}
    feed_status = factor_diag.get("feedStatus") or {}
    if not feed_status:
        feed_status = factor_result.get("feed_status") or {}
    asset_diag = factor_diag.get("engineAAssetDiagnostics") or {}
    if not asset_diag:
        asset_diag = result.get("engine_a_asset_diagnostics") or factor_result.get("engine_a_asset_diagnostics") or {}
    volatility = asset_diag.get("volatility") or {}
    raw_score = _safe_float(result.get("confluenceScore"), _safe_float(result.get("score"), 0.0))
    final_score = _safe_float(result.get("score"), raw_score)
    return {
        "engine": "engine_a",
        "pair": pair.get("display") or result.get("pair") or pair.get("symbol"),
        "symbol": pair.get("symbol") or result.get("symbol"),
        "asset_class": pair.get("type") or result.get("type"),
        "score_group": (
            result.get("scoreGroup")
            or result.get("score_group")
            or pair.get("score_group")
            or _resolve_score_group(pair)
        ),
        "regime": result.get("regimeName") or ((result.get("regime") or {}).get("label") if isinstance(result.get("regime"), dict) else result.get("regime")),
        "timeframe": timeframe or result.get("timeframe") or "D1/H4/H1",
        "raw_score": raw_score,
        "threshold": _safe_float(threshold),
        "passed": bool(passed),
        "failure_reason": failure_reason,
        "trend_score": _safe_float(factor_diag.get("directionalScore"), _safe_float(factor_result.get("directional_score"))),
        "adx_mult": _safe_float((result.get("factorScores") or {}).get("adx_multiplier"), _safe_float(factor_result.get("adx_multiplier"))),
        "di_align_mult": _safe_float(feed_status.get("di_align")),
        "dir_ramp_mult": _safe_float(
            factor_diag.get("directionalRampMult"),
            _safe_float(
                factor_diag.get("directionalRampMultiplier"),
                _safe_float(factor_result.get("directional_ramp_multiplier")),
            ),
        ),
        "volatility_scaler": _safe_float(volatility.get("atr_pct_scaler")),
        "final_score": _safe_float(factor_result.get("final_score"), final_score),
    }


def build_engine_b_calibration_row(
    *,
    conf: dict[str, Any] | None,
    style_profile: dict[str, Any] | None,
    regime_label: str | None,
    asset_type: str = "",
    scaled_min_score: float | None = None,
    passed: bool | None = None,
    diagnostics_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    conf = conf if isinstance(conf, dict) else {}
    profile = style_profile if isinstance(style_profile, dict) else {}
    ctx = diagnostics_context if isinstance(diagnostics_context, dict) else {}
    failed = list(conf.get("failed_gate_names") or ctx.get("failed_gate_names") or [])
    base_min_score = _safe_float(profile.get("min_score"), 0.0) or 0.0
    regime_multiplier = _engine_b_regime_multiplier(regime_label, asset_type)
    if passed is False and not failed:
        try:
            score = float(conf.get("score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        if scaled_min_score is not None and score < float(scaled_min_score):
            failed.append("min_score")
        elif conf.get("passed") is False:
            failed.append("checklist")
    return {
        "engine": "engine_b",
        "pair": ctx.get("pair") or ctx.get("display") or conf.get("pair"),
        "symbol": ctx.get("symbol") or conf.get("symbol"),
        "asset_class": ctx.get("asset_class") or ctx.get("asset_type") or asset_type,
        "score_group": ctx.get("score_group") or profile.get("score_group"),
        "regime": regime_label,
        "style": ctx.get("style") or profile.get("style"),
        "raw_score": _safe_float(conf.get("score"), 0.0),
        "threshold": _safe_float(scaled_min_score),
        "passed": bool(passed),
        "failure_reason": _first_failure_reason(failed),
        "base_min_score": base_min_score,
        "regime_multiplier": regime_multiplier,
        "multipliers_enabled": bool(CONFIG.get("ENGINE_B_REGIME_MULTIPLIERS_ENABLED", True)),
        "min_score": _safe_float(profile.get("min_score")),
        "scaled_min_score": _safe_float(scaled_min_score),
        "min_rr": _safe_float(profile.get("min_rr")),
        "rr_actual": _safe_float(conf.get("rr_used_for_gate"), _safe_float(conf.get("rr"))),
        "level_mode": conf.get("level_mode"),
        "level_cohort": _engine_b_level_cohort(conf.get("level_cohort") or conf.get("level_mode")),
        "sl_source": conf.get("sl_source"),
        "tp_source": conf.get("tp_source"),
        "fallback_tp_applied": _safe_bool(conf.get("fallback_tp_applied")),
        "structural_tp_available": _safe_bool(conf.get("structural_tp_available")),
        "synthetic_rr_tp_used": _safe_bool(conf.get("synthetic_rr_tp_used")),
        "rr_required": _safe_float(conf.get("rr_required"), _safe_float(profile.get("min_rr"))),
        "rr_passed": _safe_bool(conf.get("rr_passed", conf.get("rr_ok"))),
        "structural_target_distance_atr": _safe_float(conf.get("structural_target_distance_atr"), None),
        "stop_distance_atr": _safe_float(conf.get("stop_distance_atr"), None),
        "structure_ok": _safe_bool(conf.get("structure_ok")),
        "location_ok": _safe_bool(conf.get("location_ok")),
        "entry_ok": _safe_bool(conf.get("entry_ok")),
        "space_gate_ok": _safe_bool(conf.get("space_gate_ok", conf.get("room_ok"))),
        "rr_ok": _safe_bool(conf.get("rr_ok")),
        "macro_ok": _safe_bool(conf.get("macro_ok")),
        "checklist_score": _safe_float(conf.get("gate_score")),
        "bonus_points": _safe_float(conf.get("bonus_points")),
    }


def load_calibration_rows(path: Path | str | None = None) -> list[dict[str, Any]]:
    source = _diagnostics_path(path)
    if not source.exists():
        return []
    rows: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def build_calibration_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dims = {
        "by_engine": "engine",
        "by_asset_class": "asset_class",
        "by_score_group": "score_group",
        "by_regime": "regime",
        "by_timeframe_style": None,
        "by_pass_fail_reason": None,
        "by_engine_b_level_mode": "level_mode",
        "by_engine_b_level_cohort": "level_cohort",
        "by_engine_b_fallback_tp_applied": "fallback_tp_applied",
    }
    summary: dict[str, Any] = {"total_rows": len(rows)}
    for name, key in dims.items():
        counter: Counter[str] = Counter()
        for row in rows:
            if name == "by_timeframe_style":
                value = row.get("timeframe") or row.get("style") or "unknown"
            elif name == "by_pass_fail_reason":
                value = "passed" if row.get("passed") is True else str(row.get("failure_reason") or "failed_unknown")
            elif name.startswith("by_engine_b") and row.get("engine") != "engine_b":
                continue
            else:
                value = row.get(key) if key else None
            counter[str(value if value not in (None, "") else "unknown")] += 1
        summary[name] = dict(counter)
    summary["engine_b_level_cohort_performance"] = _engine_b_cohort_performance(rows)
    return summary


def build_calibration_report(rows: list[dict[str, Any]]) -> str:
    summary = build_calibration_summary(rows)
    lines = ["# Calibration Diagnostics Report", ""]
    lines.append(f"Total rows: {summary['total_rows']}")
    for section in (
        "by_engine",
        "by_asset_class",
        "by_score_group",
        "by_regime",
        "by_timeframe_style",
        "by_pass_fail_reason",
        "by_engine_b_level_mode",
        "by_engine_b_level_cohort",
        "by_engine_b_fallback_tp_applied",
    ):
        lines.extend(["", f"## {section}", "", "| value | count |", "|---|---:|"])
        values = summary.get(section) or {}
        if not values:
            lines.append("| none | 0 |")
        else:
            for value, count in sorted(values.items()):
                lines.append(f"| {value} | {count} |")
    cohort_perf = summary.get("engine_b_level_cohort_performance") or {}
    lines.extend(
        [
            "",
            "## engine_b_level_cohort_performance",
            "",
            "| cohort | count | passed | failed | avg_rr_actual |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    if not cohort_perf:
        lines.append("| none | 0 | 0 | 0 | — |")
    else:
        for cohort, bucket in sorted(cohort_perf.items()):
            avg_rr = bucket.get("avg_rr_actual")
            avg_rr_display = "—" if avg_rr is None else str(avg_rr)
            lines.append(
                f"| {cohort} | {bucket.get('count', 0)} | {bucket.get('passed', 0)} "
                f"| {bucket.get('failed', 0)} | {avg_rr_display} |"
            )
    lines.append("")
    return "\n".join(lines)


def write_calibration_report(
    input_path: Path | str | None = None,
    output_path: Path | str = Path("docs") / "diagnostics" / "calibration_diagnostics_report.md",
) -> Path:
    rows = load_calibration_rows(input_path)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_calibration_report(rows), encoding="utf-8")
    return out
