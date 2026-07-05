"""Metric parsing and scoring for AutoResearch candidates."""

from __future__ import annotations

import json
import re
from typing import Any


_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "expR": ("expR", "exp_r", "expectancy", "expectancy_r"),
    "profit_factor": ("profit_factor", "profitFactor", "PF", "pf"),
    "sqn": ("sqn", "SQN"),
    "max_drawdown_R": ("max_drawdown_R", "max_dd_r", "max_drawdown", "max_dd", "max DD"),
    "trade_count": ("trade_count", "trades", "n", "num_trades"),
    "oos_score": ("oos_score", "out_of_sample", "OOS", "oos"),
    "stability_score": ("stability_score", "train_test_stability", "stability"),
    "symbol_concentration": ("symbol_concentration", "max_symbol_share", "concentration"),
    "cost_sensitivity_penalty": ("cost_sensitivity_penalty", "cost_penalty"),
}


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace("%", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _pick(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def normalize_metrics(raw: dict[str, Any]) -> dict[str, float]:
    """Flatten heterogeneous metric dicts into canonical keys."""
    out: dict[str, float] = {}
    for canonical, aliases in _METRIC_ALIASES.items():
        val = _pick(raw, aliases)
        if val is None:
            continue
        coerced = _coerce_float(val)
        if coerced is not None:
            out[canonical] = coerced
    return out


def _extract_from_ablation_json(data: dict[str, Any]) -> dict[str, float]:
    """Map engine_a_ablation JSON report to canonical metrics."""
    variants = data.get("variants") or {}
    primary = variants.get("shadow_full") or variants.get("current") or {}
    overall = primary.get("overall") or {}
    if not overall:
        return {}

    metrics = normalize_metrics(overall)
    conc = data.get("concentration") or {}
    primary_conc = conc.get("shadow_full") or conc.get("current") or {}
    if isinstance(primary_conc, dict):
        share = primary_conc.get("max_share") or primary_conc.get("max_symbol_share")
        if share is not None:
            metrics["symbol_concentration"] = _coerce_float(share) or 0.0

    cost = data.get("cost_sensitivity") or {}
    primary_cost = cost.get("shadow_full") or cost.get("current") or {}
    if isinstance(primary_cost, dict) and len(primary_cost) >= 2:
        vals = [_coerce_float(v) for v in primary_cost.values()]
        vals = [v for v in vals if v is not None]
        if vals:
            spread = max(vals) - min(vals)
            metrics["cost_sensitivity_penalty"] = max(0.0, spread * 5.0)

    oos = data.get("oos") or data.get("out_of_sample") or {}
    if isinstance(oos, dict):
        oos_val = oos.get("score") or oos.get("expectancy")
        if oos_val is not None:
            metrics["oos_score"] = _coerce_float(oos_val) or 0.0

    return metrics


def parse_metrics_json(text: str) -> dict[str, float]:
    """Parse JSON metrics from stdout or a JSON file body."""
    text = text.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}

    if isinstance(data, dict):
        if "metrics" in data and isinstance(data["metrics"], dict):
            return normalize_metrics(data["metrics"])
        if "variants" in data:
            extracted = _extract_from_ablation_json(data)
            if extracted:
                return extracted
        return normalize_metrics(data)
    return {}


_FALLBACK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("expR", re.compile(r"(?:expR|expectancy)\s*[=:]\s*([+-]?\d+\.?\d*)", re.I)),
    ("profit_factor", re.compile(r"(?:profit\s*factor|PF)\s*[=:]\s*(\d+\.?\d*)", re.I)),
    ("sqn", re.compile(r"SQN\s*[=:]\s*([+-]?\d+\.?\d*)", re.I)),
    ("max_drawdown_R", re.compile(r"(?:max[_\s]?dd(?:_r)?|max[_\s]?drawdown)\s*[=:]\s*(\d+\.?\d*)", re.I)),
    ("trade_count", re.compile(r"(?:trade[_\s]?count|trades)\s*[=:]\s*(\d+)", re.I)),
    ("oos_score", re.compile(r"(?:OOS|out[_\s]?of[_\s]?sample)\s*[=:]\s*([+-]?\d+\.?\d*)", re.I)),
]


def parse_metrics_fallback(*texts: str) -> dict[str, float]:
    combined = "\n".join(t for t in texts if t)
    found: dict[str, float] = {}
    for key, pattern in _FALLBACK_PATTERNS:
        match = pattern.search(combined)
        if match:
            val = _coerce_float(match.group(1))
            if val is not None:
                found[key] = val
    return found


def parse_command_output(
    stdout: str,
    stderr: str,
    *,
    metrics_file: str | None = None,
) -> dict[str, float]:
    """Parse metrics from JSON file, stdout JSON, or regex fallback."""
    if metrics_file:
        try:
            from pathlib import Path

            body = Path(metrics_file).read_text(encoding="utf-8")
            parsed = parse_metrics_json(body)
            if parsed:
                return parsed
        except OSError:
            pass

    for chunk in (stdout, stderr):
        parsed = parse_metrics_json(chunk)
        if parsed:
            return parsed

    fallback = parse_metrics_fallback(stdout, stderr)
    return fallback


def compute_score(metrics: dict[str, float], config: dict[str, Any]) -> float:
    exp_r = metrics.get("expR", 0.0)
    pf = metrics.get("profit_factor", 0.0)
    sqn = metrics.get("sqn", 0.0)
    max_dd = metrics.get("max_drawdown_R", 0.0)
    trade_count = int(metrics.get("trade_count", 0))

    trade_count_score = min(trade_count / 100.0, 1.0) * 5.0
    oos_score = metrics.get("oos_score", 0.0) * 10.0
    stability_score = metrics.get("stability_score", 0.0) * 5.0

    max_conc = float(config.get("max_symbol_concentration", 0.60))
    concentration = metrics.get("symbol_concentration", 0.0)
    concentration_penalty = max(0.0, concentration - max_conc) * 20.0

    cost_penalty = metrics.get("cost_sensitivity_penalty", 0.0)

    score = (
        exp_r * 40.0
        + min(pf, 3.0) * 15.0
        + min(sqn, 4.0) * 10.0
        - max_dd * 8.0
        + trade_count_score
        + oos_score
        + stability_score
        - concentration_penalty
        - cost_penalty
    )
    return round(score, 4)


def evaluate_acceptance(
    *,
    baseline_metrics: dict[str, float],
    candidate_metrics: dict[str, float],
    baseline_score: float,
    candidate_score: float,
    config: dict[str, Any],
    safety_ok: bool,
    safety_reasons: list[str],
    tests_passed: bool,
    evaluation_ok: bool,
    metrics_found: bool,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []

    if not metrics_found:
        reasons.append("no_metrics_found")
    if not safety_ok:
        reasons.extend(safety_reasons)
    if not tests_passed:
        reasons.append("tests_failed")
    if not evaluation_ok:
        reasons.append("evaluation_command_failed")

    min_delta = float(config.get("min_score_delta", 0.05))
    if candidate_score <= baseline_score + min_delta:
        reasons.append("score_delta_insufficient")

    min_trades = int(config.get("min_trade_count", 30))
    if candidate_metrics.get("trade_count", 0) < min_trades:
        reasons.append("low_trade_count")

    max_dd = float(config.get("max_drawdown_R", 3.0))
    if candidate_metrics.get("max_drawdown_R", 0.0) > max_dd:
        reasons.append("excessive_drawdown")

    if config.get("require_oos_not_worse", True):
        base_oos = baseline_metrics.get("oos_score", baseline_score)
        cand_oos = candidate_metrics.get("oos_score", candidate_score)
        if cand_oos < base_oos:
            reasons.append("oos_worse_than_baseline")

    max_conc = float(config.get("max_symbol_concentration", 0.60))
    if candidate_metrics.get("symbol_concentration", 0.0) > max_conc:
        reasons.append("symbol_concentration_too_high")

    return len(reasons) == 0, reasons
