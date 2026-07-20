#!/usr/bin/env python3
"""Controlled H4 entry-TF indicator tuning experiment (research-only overlay)."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ATHENA_DIAGNOSTIC_MODE", "1")
os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

from scoring import get_pair_score_group
from tools._athena_pair_loader import find_pair_by_display, representative_audit_pairs
from tools.h4_overlay_runtime import (
    OVERLAY_ENV,
    apply_h4_indicator_overlay,
    load_h4_overlay,
    research_backtest_patches,
    summarize_backtest,
)

EXPERIMENT_PAIRS = [
    "EUR/USD",
    "EUR/GBP",
    "USD/ZAR",
    "BTC/USDT",
    "SOL/USDT",
    "XAU/USD",
    "WTI Oil",
    "AAPL",
    "SPY",
]

RESULTS_PATH = ROOT / "tools" / "_h4_tuning_experiment_results.csv"
VERDICT_PATH = ROOT / "tools" / "_h4_tuning_verdict.md"


def _run_variant(pair: dict, variant: str, entry_tf: str = "H4") -> dict:
    import backtest_runner
    from tools.h4_overlay_runtime import bootstrap_backtest_runtime, research_backtest_patches

    bootstrap_backtest_runtime()
    score_group = get_pair_score_group(pair)
    overlay_ctx = apply_h4_indicator_overlay() if variant == "h4_tuned" else _nullcontext()

    with research_backtest_patches(entry_tf=entry_tf, score_group_aware=True) as calc_wrapper, overlay_ctx:
        calc_wrapper._active_score_group = score_group
        result = backtest_runner.backtest_pair_naked(pair, style="intraday")
    trades = result.get("trades") or []
    summary = summarize_backtest(result, trades)
    summary.update(
        {
            "pair": pair["display"],
            "symbol": pair.get("symbol"),
            "score_group": score_group,
            "variant": variant,
            "entry_tf": entry_tf,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    return summary


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, *args):
        return False


def run_experiment(pairs: list[dict], output: Path = RESULTS_PATH) -> list[dict]:
    rows: list[dict] = []
    for pair in pairs:
        for variant in ("h1_on_h4", "h4_tuned"):
            print(f"Running {pair['display']} variant={variant} ...", flush=True)
            rows.append(_run_variant(pair, variant))
    fieldnames = [
        "timestamp_utc",
        "pair",
        "symbol",
        "score_group",
        "variant",
        "entry_tf",
        "trades",
        "win_rate",
        "avg_r",
        "sqn",
        "sl_count",
        "sl_pct",
        "tp_count",
        "timeout_count",
        "be_count",
        "error",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def write_verdict(rows: list[dict], path: Path = VERDICT_PATH) -> None:
    by_pair: dict[str, dict[str, dict]] = {}
    for row in rows:
        by_pair.setdefault(row["pair"], {})[row["variant"]] = row

    lines = [
        "# H4 Indicator Tuning Experiment Verdict",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Single-variable test: H4 entry TF held constant; only indicator periods differ between variants.",
        "",
        "| Pair | Score group | H1-on-H4 SL% | H4-tuned SL% | SL delta | H1-on-H4 avg R | H4-tuned avg R | R delta | Trades (base/tuned) | Verdict |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]

    group_notes: dict[str, list[str]] = {}
    for pair, variants in sorted(by_pair.items()):
        base = variants.get("h1_on_h4", {})
        tuned = variants.get("h4_tuned", {})
        sl_delta = float(tuned.get("sl_pct", 0) or 0) - float(base.get("sl_pct", 0) or 0)
        r_delta = float(tuned.get("avg_r", 0) or 0) - float(base.get("avg_r", 0) or 0)
        if base.get("error") or tuned.get("error"):
            verdict = "inconclusive (data error)"
        elif not base.get("trades") and not tuned.get("trades"):
            verdict = "inconclusive (zero trades)"
        elif sl_delta <= -3.0 and r_delta >= 0:
            verdict = "H4 tuning improved SL rate without hurting R"
        elif abs(sl_delta) < 2.0 and abs(r_delta) < 0.02:
            verdict = "no clear effect — TF change dominates"
        elif sl_delta > 3.0:
            verdict = "H4 tuning worsened SL rate"
        else:
            verdict = "mixed / inconclusive on this pair sample"
        group = str(base.get("score_group") or tuned.get("score_group") or "unknown")
        group_notes.setdefault(group, []).append(
            f"{pair}: {verdict} (SL delta {sl_delta:+.1f}pp, R delta {r_delta:+.3f})"
        )
        lines.append(
            f"| {pair} | {base.get('score_group','')} | {base.get('sl_pct','')} | {tuned.get('sl_pct','')} | "
            f"{sl_delta:+.1f} | {base.get('avg_r','')} | {tuned.get('avg_r','')} | {r_delta:+.3f} | "
            f"{base.get('trades','')}/{tuned.get('trades','')} | {verdict} |"
        )

    lines.extend(["", "## Per-group inference (one pair per group — indicative only)", ""])
    for group, notes in sorted(group_notes.items()):
        improved = sum(1 for n in notes if "improved" in n)
        worsened = sum(1 for n in notes if "worsened" in n)
        if improved and not worsened:
            group_call = "sufficient signal to justify a full-group backtest"
        elif worsened and not improved:
            group_call = "no clear TF-period effect — look elsewhere first"
        else:
            group_call = "mixed on this single-pair sample — not conclusive for the group"
        lines.append(f"### {group}")
        lines.append(group_call)
        for note in notes:
            lines.append(f"- {note}")
        lines.append("")

    lines.extend(
        [
            "## Notes",
            "",
            "- Overlay file: `configs/proposed_h4_indicator_overlay.yaml` (NOT imported by live config).",
            "- Activate overlay only via `ATHENA_BT_H4_OVERLAY=proposed_h4_indicator_overlay` inside the experiment runner.",
            "- Do NOT promote overlay values to `config.yaml` from this single-pair-per-group run.",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="H4 indicator tuning experiment")
    parser.add_argument(
        "--pairs",
        default=",".join(EXPERIMENT_PAIRS),
        help="Comma-separated pair display names",
    )
    parser.add_argument("--output", default=str(RESULTS_PATH))
    parser.add_argument("--verdict", default=str(VERDICT_PATH))
    parser.add_argument(
        "--require-overlay-env",
        action="store_true",
        help="Fail if ATHENA_BT_H4_OVERLAY is unset (h4_tuned variant still uses direct overlay load)",
    )
    args = parser.parse_args()

    if args.require_overlay_env and not os.environ.get(OVERLAY_ENV):
        print(f"{OVERLAY_ENV} must be set to proposed_h4_indicator_overlay")
        return 1

    # Ensure overlay file exists even when env var unset (runner loads file directly for h4_tuned).
    load_h4_overlay()

    names = [p.strip() for p in args.pairs.split(",") if p.strip()]
    pairs: list[dict] = []
    for name in names:
        pair = find_pair_by_display(name)
        if pair is None:
            print(f"Unknown pair: {name}")
            return 1
        pairs.append(pair)

    rows = run_experiment(pairs, Path(args.output))
    write_verdict(rows, Path(args.verdict))
    print(f"Wrote {args.output}")
    print(f"Wrote {args.verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
