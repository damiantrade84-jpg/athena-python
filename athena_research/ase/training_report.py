"""Markdown report for deterministic ASE family-horizon training."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _format_float(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "n/a"


def write_training_report(payload: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# ASE Training Report",
        "",
        f"- artifact version: `{payload.get('version', 'unknown')}`",
        f"- trained family-horizons: {len(payload.get('trained') or [])}",
        f"- failed family-horizons: {len(payload.get('failed') or [])}",
        "",
        "## Trained",
        "",
        "| family | horizon | eval trades | expectancy R | win rate | Brier | threshold | fallback | enriched |",
        "|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for result in payload.get("trained") or []:
        metrics = result.get("metrics") or {}
        enriched = metrics.get("enriched") or {}
        lines.append(
            "| {family} | {horizon} | {trades} | {expectancy} | {win_rate} | "
            "{brier} | {threshold} | {fallback} | {enriched} |".format(
                family=result.get("family", ""),
                horizon=result.get("horizon", ""),
                trades=metrics.get("eval_trades", 0),
                expectancy=_format_float(metrics.get("eval_expectancy")),
                win_rate=_format_float(metrics.get("eval_win_rate")),
                brier=_format_float(metrics.get("eval_brier")),
                threshold=_format_float(metrics.get("thr_family")),
                fallback="yes" if metrics.get("threshold_fallback") else "no",
                enriched="yes" if enriched.get("usable") else enriched.get("reason", "no"),
            )
        )

    lines.extend(["", "## Failed / FLAT-only", ""])
    failures = payload.get("failed") or []
    if not failures:
        lines.append("None.")
    else:
        for failure in failures:
            lines.append(
                f"- `{failure.get('family')}/{failure.get('horizon')}`: "
                f"{failure.get('reason', 'unknown training failure')}"
            )

    lines.extend(
        [
            "",
            "Negative evaluation expectancy is reported without alteration. "
            "Only family-horizons that failed training are listed as FLAT-only.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
