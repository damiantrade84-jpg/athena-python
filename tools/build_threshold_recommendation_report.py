"""Build threshold recommendation artifacts from real calibration evidence.

Research-only. Reads closed-trade sweep outputs and normalized diagnostic
events. Does not mutate config, scoring, thresholds, risk, or execution.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SWEEP_ROOT = ROOT / "logs" / "calibration" / "sweeps"
DIAG_JSON = ROOT / "logs" / "calibration" / "diagnostics_normalized" / "diagnostic_events_normalized.json"
ACCEPTED_JSON = ROOT / "logs" / "calibration" / "sweep_input_from_audit.json"
OUT_MD = ROOT / "logs" / "calibration" / "threshold_recommendation_report.md"
OUT_CSV = ROOT / "logs" / "calibration" / "threshold_recommendation_matrix.csv"

MATRIX_COLUMNS = [
    "engine",
    "threshold_family",
    "asset_class",
    "score_group",
    "regime",
    "style",
    "config_key",
    "current_value",
    "tested_values",
    "recommended_value",
    "accepted_sample_size",
    "diagnostic_sample_size",
    "baseline_expectancy",
    "recommended_expectancy",
    "baseline_win_rate",
    "recommended_win_rate",
    "trade_count_change",
    "near_threshold_rejected_count",
    "common_failure_reason",
    "evidence_strength",
    "overfit_risk",
    "rollback_rule",
    "live_change_allowed",
    "evidence_file",
    "decision",
    "reason",
]

FAMILIES = {
    "engine_a_thresholds": {
        "engine": "ENGINE_A",
        "parameter": "score_group_threshold_default",
        "config_key": "ENGINE_A_SCORE_GROUP_THRESHOLDS.default",
    },
    "engine_a_adx": {
        "engine": "ENGINE_A",
        "parameter": "adx_trend_min",
        "config_key": "ADX_TREND_MIN_CLASS.default / FACTOR_ADX_HARD_FAIL_CLASS.default",
    },
    "engine_a_volatility": {
        "engine": "ENGINE_A",
        "parameter": "volatility_scaler_low",
        "config_key": "VOLATILITY_SCALER_BANDS",
    },
    "engine_b_min_score": {
        "engine": "ENGINE_B",
        "parameter": "style_min_score",
        "config_key": "NAKED_ENGINE.style_profiles.intraday.min_score",
    },
    "engine_b_rr": {
        "engine": "ENGINE_B",
        "parameter": "style_min_rr",
        "config_key": "NAKED_ENGINE.style_profiles.intraday.min_rr",
    },
    "engine_b_room": {
        "engine": "ENGINE_B",
        "parameter": "min_room_atr",
        "config_key": "NAKED_ENGINE.style_profiles.intraday.min_room_atr",
    },
    "engine_b_regime_multiplier": {
        "engine": "ENGINE_B",
        "parameter": "regime_multiplier",
        "config_key": "ENGINE_B_REGIME_MULTIPLIERS",
    },
    "engine_b_fallback_tp": {
        "engine": "ENGINE_B",
        "parameter": "fallback_tp_enabled",
        "config_key": "ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP",
    },
}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        out = float(value)
        if out != out:
            return None
        return out
    except (TypeError, ValueError):
        return None


def _current_value(cfg: dict[str, Any], key: str) -> Any:
    if key == "ENGINE_A_SCORE_GROUP_THRESHOLDS.default":
        return (cfg.get("ENGINE_A_SCORE_GROUP_THRESHOLDS") or {}).get("default")
    if key.startswith("ADX_TREND_MIN_CLASS"):
        return {
            "ADX_TREND_MIN_CLASS": cfg.get("ADX_TREND_MIN_CLASS"),
            "FACTOR_ADX_HARD_FAIL_CLASS": cfg.get("FACTOR_ADX_HARD_FAIL_CLASS"),
        }
    if key == "VOLATILITY_SCALER_BANDS":
        return cfg.get("VOLATILITY_SCALER_BANDS")
    if key == "NAKED_ENGINE.style_profiles.intraday.min_score":
        return (((cfg.get("NAKED_ENGINE") or {}).get("style_profiles") or {}).get("intraday") or {}).get("min_score")
    if key == "NAKED_ENGINE.style_profiles.intraday.min_rr":
        return (((cfg.get("NAKED_ENGINE") or {}).get("style_profiles") or {}).get("intraday") or {}).get("min_rr")
    if key == "NAKED_ENGINE.style_profiles.intraday.min_room_atr":
        return (((cfg.get("NAKED_ENGINE") or {}).get("style_profiles") or {}).get("intraday") or {}).get("min_room_atr")
    if key == "ENGINE_B_REGIME_MULTIPLIERS":
        return cfg.get("ENGINE_B_REGIME_MULTIPLIERS")
    if key == "ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP":
        return cfg.get("ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP")
    return None


def _accepted_baseline(rows: list[dict[str, Any]], engine: str) -> dict[str, Any]:
    subset = [row for row in rows if row.get("engine") == engine]
    vals = [_safe_float(row.get("r_multiple")) for row in subset]
    vals = [v for v in vals if v is not None]
    wins = [row for row in subset if row.get("win") is not None]
    return {
        "n": len(subset),
        "expectancy": round(sum(vals) / len(vals), 6) if vals else None,
        "win_rate": round(sum(1 for row in wins if row.get("win") is True) / len(wins), 6) if wins else None,
    }


def _diag_summary(rows: list[dict[str, Any]], engine: str) -> dict[str, Any]:
    subset = [row for row in rows if row.get("engine") == engine]
    failed = [row for row in subset if row.get("passed") is False]
    near = [
        row for row in failed
        if (dist := _safe_float(row.get("distance_to_threshold"))) is not None and dist >= -0.25
    ]
    reasons = Counter(row.get("failure_reason") or "passed" for row in failed)
    return {
        "n": len(subset),
        "near": len(near),
        "common_failure": reasons.most_common(1)[0][0] if reasons else "none",
    }


def _variant_groups(rows: list[dict[str, str]], parameter: str) -> dict[str, list[dict[str, str]]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        try:
            params = json.loads(row.get("parameters") or "{}")
        except json.JSONDecodeError:
            params = {}
        value = params.get(parameter)
        if parameter == "volatility_scaler_low":
            value = {
                "low": params.get("volatility_scaler_low"),
                "high": params.get("volatility_scaler_high"),
            }
        elif parameter == "adx_trend_min":
            value = {
                "trend_min": params.get("adx_trend_min"),
                "hard_fail": params.get("adx_hard_fail"),
            }
        groups.setdefault(json.dumps(value, sort_keys=True), []).append(row)
    return groups


def _aggregate_variant(items: list[dict[str, str]]) -> dict[str, Any]:
    raw = max([int(float(row.get("raw_source_count") or 0)) for row in items] or [0])
    admitted = max([int(float(row.get("admitted_count") or 0)) for row in items] or [0])
    excluded = max([int(float(row.get("excluded_by_threshold_count") or 0)) for row in items] or [0])
    wins = 0
    win_total = 0
    weighted_r = 0.0
    r_n = 0
    for row in items:
        n = int(float(row.get("trade_count") or 0))
        wr = _safe_float(row.get("win_rate"))
        exp = _safe_float(row.get("expectancy"))
        if wr is not None:
            wins += int(round(wr * n))
            win_total += n
        if exp is not None:
            weighted_r += exp * n
            r_n += n
    return {
        "raw": raw,
        "admitted": admitted,
        "excluded": excluded,
        "expectancy": round(weighted_r / r_n, 6) if r_n else None,
        "win_rate": round(wins / win_total, 6) if win_total else None,
        "warning_rows": sum(1 for row in items if str(row.get("sample_size_warning")).lower() == "true"),
        "row_count": len(items),
    }


def build_matrix() -> list[dict[str, Any]]:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    accepted_rows = (_load_json(ACCEPTED_JSON).get("rows") or [])
    diag_rows = (_load_json(DIAG_JSON).get("rows") or [])
    matrix: list[dict[str, Any]] = []
    baselines = {engine: _accepted_baseline(accepted_rows, engine) for engine in ("ENGINE_A", "ENGINE_B")}
    diagnostics = {engine: _diag_summary(diag_rows, engine) for engine in ("ENGINE_A", "ENGINE_B")}

    for family, meta in FAMILIES.items():
        sweep_csv = SWEEP_ROOT / family / "calibration_sweep.csv"
        sweep_json = SWEEP_ROOT / family / "calibration_sweep.json"
        rows = _load_csv(sweep_csv)
        engine = meta["engine"]
        groups = _variant_groups(rows, meta["parameter"])
        aggregates = {value: _aggregate_variant(items) for value, items in groups.items()}
        tested_values = list(aggregates)
        best_value = None
        best = None
        if aggregates:
            best_value, best = max(
                aggregates.items(),
                key=lambda item: (
                    item[1]["expectancy"] if item[1]["expectancy"] is not None else -999.0,
                    item[1]["admitted"],
                ),
            )
        baseline = baselines[engine]
        diag = diagnostics[engine]
        best_expectancy = best.get("expectancy") if best else None
        best_win_rate = best.get("win_rate") if best else None
        admitted = int(best.get("admitted") or 0) if best else 0
        trade_count_change = admitted - int(baseline["n"] or 0)
        sufficient_diag = diag["n"] >= 30
        sufficient_accept = int(baseline["n"] or 0) >= 30
        improves = (
            best_expectancy is not None
            and baseline["expectancy"] is not None
            and best_expectancy > baseline["expectancy"]
            and admitted >= max(30, int((baseline["n"] or 0) * 0.5))
        )
        direct_effect = family in {"engine_a_thresholds", "engine_b_min_score"}
        if not rows:
            decision = "insufficient"
            reason = "missing sweep output"
            recommendation = ""
        elif not direct_effect:
            decision = "no_change"
            reason = "input-json sweep cannot prove this family because required per-trade gate fields are absent or not re-simulated"
            recommendation = ""
        elif not sufficient_accept or not sufficient_diag:
            decision = "insufficient"
            reason = "sample size below threshold for accepted or diagnostic evidence"
            recommendation = ""
        elif improves and diag["near"] == 0:
            decision = "exploratory_only"
            reason = "expectancy improves but remains research-only pending OOS confirmation"
            recommendation = best_value
        else:
            decision = "no_change"
            reason = "best tested value does not clear expectancy/trade-count/diagnostic gates"
            recommendation = ""
        strength = "weak" if sufficient_accept and sufficient_diag else "insufficient"
        if decision == "exploratory_only":
            strength = "exploratory"
        matrix.append(
            {
                "engine": engine,
                "threshold_family": family,
                "asset_class": "all",
                "score_group": "all" if engine == "ENGINE_A" else "",
                "regime": "all",
                "style": "intraday" if engine == "ENGINE_B" else "",
                "config_key": meta["config_key"],
                "current_value": json.dumps(_current_value(cfg, meta["config_key"]), sort_keys=True),
                "tested_values": "; ".join(tested_values),
                "recommended_value": recommendation,
                "accepted_sample_size": baseline["n"],
                "diagnostic_sample_size": diag["n"],
                "baseline_expectancy": baseline["expectancy"],
                "recommended_expectancy": best_expectancy,
                "baseline_win_rate": baseline["win_rate"],
                "recommended_win_rate": best_win_rate,
                "trade_count_change": trade_count_change,
                "near_threshold_rejected_count": diag["near"],
                "common_failure_reason": diag["common_failure"],
                "evidence_strength": strength,
                "overfit_risk": "high" if len(tested_values) > 3 or strength != "weak" else "medium",
                "rollback_rule": "No live change proposed; keep current config. If later applied, revert this config key to current_value.",
                "live_change_allowed": False,
                "evidence_file": str(sweep_csv.relative_to(ROOT)) if sweep_csv.exists() else str(sweep_csv),
                "decision": decision,
                "reason": reason,
            }
        )
    return matrix


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATRIX_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(out)


def _write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    accepted_rows = (_load_json(ACCEPTED_JSON).get("rows") or [])
    diag_rows = (_load_json(DIAG_JSON).get("rows") or [])
    ea = sum(1 for row in accepted_rows if row.get("engine") == "ENGINE_A")
    eb = sum(1 for row in accepted_rows if row.get("engine") == "ENGINE_B")
    dea = sum(1 for row in diag_rows if row.get("engine") == "ENGINE_A")
    deb = sum(1 for row in diag_rows if row.get("engine") == "ENGINE_B")
    no_change = [row for row in rows if row["decision"] == "no_change"]
    insufficient = [row for row in rows if row["decision"] == "insufficient"]
    exploratory = [row for row in rows if row["decision"] == "exploratory_only"]
    lines = [
        "# Threshold recommendation report",
        "",
        f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. Research-only; no live config or execution changes._",
        "",
        "## Dataset",
        "",
        _md_table(
            ["Metric", "Value"],
            [
                ["Accepted closed trades", len(accepted_rows)],
                ["ENGINE_A accepted", ea],
                ["ENGINE_B accepted", eb],
                ["Diagnostic events", len(diag_rows)],
                ["ENGINE_A diagnostics", dea],
                ["ENGINE_B diagnostics", deb],
                ["Recommendation rows", len(rows)],
                ["Exploratory recommendations", len(exploratory)],
                ["No-change rows", len(no_change)],
                ["Insufficient rows", len(insufficient)],
            ],
        ),
        "",
        "## Recommendations",
        "",
        "No threshold family is approved for live promotion. Rows marked `exploratory_only`, if any, remain `live_change_allowed=false`.",
        "",
        _md_table(
            [
                "Family", "Engine", "Current", "Recommended", "Accepted n", "Diagnostic n",
                "Baseline R", "Best R", "Evidence", "Decision", "Reason",
            ],
            [
                [
                    row["threshold_family"],
                    row["engine"],
                    row["current_value"],
                    row["recommended_value"] or "none",
                    row["accepted_sample_size"],
                    row["diagnostic_sample_size"],
                    row["baseline_expectancy"],
                    row["recommended_expectancy"],
                    row["evidence_strength"],
                    row["decision"],
                    row["reason"],
                ]
                for row in rows
            ],
        ),
        "",
        "## Insufficient Evidence Families",
        "",
    ]
    for row in rows:
        if row["decision"] != "exploratory_only":
            lines.append(
                f"- `{row['threshold_family']}`: {row['reason']} "
                f"(accepted_n={row['accepted_sample_size']}, diagnostic_n={row['diagnostic_sample_size']}, "
                f"evidence={row['evidence_strength']})."
            )
    lines.extend(
        [
            "",
            "## Artifact Inputs",
            "",
            "- `logs/calibration/sweep_input_from_audit.json`",
            "- `logs/calibration/diagnostics_normalized/diagnostic_events_normalized.json`",
            "- `logs/calibration/master_evidence_report.md`",
            "- `logs/calibration/group_blocker_matrix.csv`",
            "- `logs/calibration/controlled_experiment_plan.md`",
            "- `logs/calibration/sweeps/*/calibration_sweep.csv`",
            "",
            "## Policy",
            "",
            "- `live_change_allowed=false` for every row.",
            "- Rankings use expectancy / robust metadata, not raw PnL alone.",
            "- Tests are safety evidence only; threshold decisions use real input-json sweeps and diagnostics.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    matrix = build_matrix()
    _write_csv(OUT_CSV, matrix)
    _write_markdown(OUT_MD, matrix)
    print(json.dumps({"rows": len(matrix), "csv": str(OUT_CSV), "markdown": str(OUT_MD)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
