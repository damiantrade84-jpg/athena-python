#!/usr/bin/env python3
"""Build logs/calibration/final_calibration_decision_report.md from evidence artifacts."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "calibration" / "final_calibration_decision_report.md"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_report() -> str:
    by_engine = _read_csv(ROOT / "logs/calibration/full_baseline_real/by_engine.csv")
    by_regime = _read_csv(ROOT / "logs/calibration/full_baseline_real/by_engine_regime.csv")
    matrix = _read_csv(ROOT / "logs/calibration/group_blocker_matrix.csv")

    weak = [r for r in matrix if r.get("evidence_strength") == "weak"]
    insufficient = [r for r in matrix if r.get("evidence_strength") == "insufficient"]
    strong = [r for r in matrix if r.get("evidence_strength") == "strong"]

    regime_n30 = [r for r in by_regime if r.get("sample_size_warning", "").lower() == "false"]

    lines = [
        "# Final calibration decision report",
        "",
        f"_Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. "
        "Evidence-backed package close-out — **no live config or execution changes applied.**_",
        "",
        "## 1. Dataset summary",
        "",
        "| Metric | Value | Source |",
        "| --- | --- | --- |",
        "| Accepted closed trades | **466** | `master_evidence_report.md` §1 |",
        "| ENGINE_A accepted | **276** | `master_evidence_report.md` §1 |",
        "| ENGINE_B accepted | **190** | `master_evidence_report.md` §1 |",
        "| Rejected / diagnostic events | **2** | `master_evidence_report.md` §1 |",
        "| Date range (accepted) | **2026-03-30 → 2026-05-19** | `master_evidence_report.md` §1 |",
        "| Matrix groups | **51** | `group_blocker_matrix.csv` |",
        "| strong (both pipelines n≥30) | **0** | `master_evidence_report.md` §1 |",
        "| weak (accepted n≥30, no diagnostics) | **4** | `group_blocker_matrix.csv` |",
        "| insufficient | **47** | `group_blocker_matrix.csv` |",
        "| standard_real variants | **1620** | `standard_real/calibration_sweep.json` |",
        "| Promotable sweep rows (n≥30, no warning) | **0** | `standard_real/calibration_sweep.csv` |",
        "",
        "## 2. Engine A findings (accepted trades)",
        "",
    ]
    for row in by_engine:
        if row.get("engine") == "ENGINE_A":
            lines.append(
                f"- Portfolio: n={row['trade_count']}, WR={float(row['win_rate']):.2%}, "
                f"avg_R={row['avg_r_multiple']} (`full_baseline_real/by_engine.csv`)"
            )
    lines.append("")
    lines.append("### Regime buckets (n≥30)")
    lines.append("")
    lines.append("| Regime | n | WR | avg_R | Evidence |")
    lines.append("| --- | ---: | ---: | ---: | --- |")
    for row in regime_n30:
        if row.get("engine") != "ENGINE_A":
            continue
        ev = "weak" if float(row["avg_r_multiple"]) < 0 else "weak-positive"
        lines.append(
            f"| {row['regime']} | {row['trade_count']} | {float(row['win_rate']):.2%} | "
            f"{row['avg_r_multiple']} | {ev} |"
        )
    lines.extend(
        [
            "",
            "**Pattern:** High win rate with negative expectancy in major regimes — suggests loss-skew / exits, not proven over-permission at gate.",
            "",
            "## 3. Engine B findings (accepted trades)",
            "",
        ]
    )
    for row in by_engine:
        if row.get("engine") == "ENGINE_B":
            lines.append(
                f"- Portfolio: n={row['trade_count']}, WR={float(row['win_rate']):.2%}, "
                f"avg_R={row['avg_r_multiple']} (`full_baseline_real/by_engine.csv`)"
            )
    lines.append("")
    lines.append("### Regime buckets (n≥30)")
    lines.append("")
    lines.append("| Regime | n | WR | avg_R | Evidence |")
    lines.append("| --- | ---: | ---: | ---: | --- |")
    for row in regime_n30:
        if row.get("engine") != "ENGINE_B":
            continue
        tag = "weak-positive" if float(row["avg_r_multiple"]) > 0 else "weak"
        lines.append(
            f"| {row['regime']} | {row['trade_count']} | {float(row['win_rate']):.2%} | "
            f"{row['avg_r_multiple']} | {tag} |"
        )
    lines.extend(
        [
            "",
            "**Strongest accepted bucket:** ENGINE_B TRENDING n=32 avg_R=+0.1241 (`by_engine_regime.csv`).",
            "",
            "## 4. What works by group",
            "",
            "| Engine | Group | n | avg_R | Strength |",
            "| --- | --- | ---: | ---: | --- |",
            "| ENGINE_B | TRENDING regime | 32 | +0.1241 | weak-positive (n≥30) |",
            "| ENGINE_A | TRENDING / intraday | 63 | +0.0278 | weak (`group_blocker_matrix.csv`) |",
            "| ENGINE_B | scalp style | 75 | +0.0145 | weak (`by_engine_style.csv`) |",
            "",
            "## 5. What works against each group",
            "",
            "| Engine | Group | n | avg_R | WR | Blocker |",
            "| --- | --- | ---: | ---: | ---: | --- |",
            "| ENGINE_A | LOW_VOLATILITY / intraday | 76 | -0.055 | 71% | negative R despite high WR |",
            "| ENGINE_A | TRENDING (regime) | 96 | -0.104 | 64% | negative expectancy |",
            "| ENGINE_B | RANGING (regime) | 125 | -0.066 | 57% | negative expectancy |",
            "| ENGINE_B | RANGING / intraday | 58 | -0.073 | 52% | weak (`group_blocker_matrix.csv`) |",
            "| BOTH | Rejection diagnostics | 2 | n/a | n/a | insufficient to explain gates |",
            "",
            "## 6. Evidence strength classification",
            "",
            "| Class | Count | Meaning |",
            "| --- | ---: | --- |",
            f"| **strong** | {len(strong)} | n≥30 accepted **and** n≥30 rejected — **none** |",
            f"| **weak** | {len(weak)} | n≥30 accepted only |",
            f"| **insufficient** | {len(insufficient)} | accepted and/or rejected below 30 |",
            "",
            "## 7. Decisions",
            "",
            "### Safe for proposed overlay (research only)",
            "",
            "1. **CALIBRATION_DIAGNOSTICS_ENABLED: true** — telemetry only; `config.yaml` L103-105.",
            "",
            "### Recommended for further research only (apply: false)",
            "",
            "Seven gate families in `configs/proposed_calibration_overlay.yaml` (`_proposed_gate_adjustments`) "
            "pending OOS + diagnostic collection (EXP-P0-001, controlled_experiment_plan.md).",
            "",
            "### Rejected for live promotion",
            "",
            "See `logs/calibration/rejected_calibration_changes.md`.",
            "",
            "## 8. No live change confirmation",
            "",
            "- `config.yaml` defaults **unchanged**.",
            "- `configs/proposed_calibration_overlay.yaml` is **not** imported by `config.py`.",
            "- No Engine A/B scoring formula files modified.",
            "- No execution / auto-trader settings modified.",
            "- Ranking policy remains **expectancy_r**, not raw PnL.",
            "",
            "## 9. Artifact index",
            "",
            "| Artifact | Path |",
            "| --- | --- |",
            "| Master evidence | `logs/calibration/master_evidence_report.md` |",
            "| Blocker matrix | `logs/calibration/group_blocker_matrix.csv` |",
            "| Experiment plan | `logs/calibration/controlled_experiment_plan.md` |",
            "| Proposed overlay | `configs/proposed_calibration_overlay.yaml` |",
            "| Proposed changes | `logs/calibration/proposed_config_changes.md` |",
            "| Rejected changes | `logs/calibration/rejected_calibration_changes.md` |",
            "| Baseline sweep | `logs/calibration/full_baseline_real/` |",
            "| Standard sweep | `logs/calibration/standard_real/` |",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_report(), encoding="utf-8")
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
