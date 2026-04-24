"""Build a markdown threshold audit report from signal_funnel.jsonl."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from threshold_audit import count_reasons, distribution

DEFAULT_OUTPUT = Path("docs") / "diagnostics" / "threshold_audit_report.md"


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _pct(n: int, d: int) -> str:
    if d <= 0:
        return "0.0%"
    return f"{(n / d) * 100:.1f}%"


def _within_below(rows: list[dict[str, Any]], score_key: str, threshold_key: str, pct: float) -> str:
    total = 0
    hits = 0
    for row in rows:
        th = ((row.get("thresholds") or {}).get(threshold_key))
        score = row.get(score_key)
        if th is None or score is None:
            continue
        try:
            th_f = float(th)
            score_f = float(score)
        except (TypeError, ValueError):
            continue
        if th_f <= 0:
            continue
        total += 1
        if th_f * (1.0 - pct) <= score_f < th_f:
            hits += 1
    return _pct(hits, total)


def _top_table(counter: Counter, limit: int = 10) -> str:
    if not counter:
        return "| reason | count |\n|---|---:|\n| none | 0 |\n"
    lines = ["| reason | count |", "|---|---:|"]
    for reason, count in counter.most_common(limit):
        lines.append(f"| {reason} | {count} |")
    return "\n".join(lines) + "\n"


def _near_misses(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        thresholds = row.get("thresholds") or {}
        for engine, score_key, th_key, fail_key in (
            ("Engine A", "engine_a_raw_score", "engine_a", "engine_a_fail_reasons"),
            ("Engine B", "engine_b_raw_score", "engine_b", "engine_b_fail_reasons"),
        ):
            try:
                score = float(row.get(score_key) or 0.0)
                threshold = float(thresholds.get(th_key) or 0.0)
            except (TypeError, ValueError):
                continue
            if threshold > 0 and threshold * 0.85 <= score < threshold:
                out.append({
                    "symbol": row.get("symbol"),
                    "asset_type": row.get("asset_type"),
                    "engine": engine,
                    "score": score,
                    "threshold": threshold,
                    "fail_reason": ", ".join((row.get(fail_key) or [])[:3]),
                })
    return out


def _shadow_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, Counter]]:
    out: dict[str, dict[str, Counter]] = {
        "ENGINE_A": defaultdict(Counter),
        "ENGINE_B": defaultdict(Counter),
        "ENGINE_C": defaultdict(Counter),
    }
    for row in rows:
        asset = row.get("asset_type") or "unknown"
        shadows = row.get("shadow_thresholds") or {}
        a_score = float(row.get("engine_a_raw_score") or 0.0)
        b_score = float(row.get("engine_b_raw_score") or 0.0)
        c_score = float(row.get("engine_c_final_conviction") or 0.0)
        for label, th in (shadows.get("ENGINE_A") or {}).items():
            if a_score >= float(th or 0.0):
                out["ENGINE_A"][label][asset] += 1
        for label, th in (shadows.get("ENGINE_B") or {}).items():
            if b_score >= float(th or 0.0):
                out["ENGINE_B"][label][asset] += 1
        for label, th in (shadows.get("ENGINE_C") or {}).items():
            if c_score >= float(th or 0.0):
                out["ENGINE_C"][label][asset] += 1
    return out


def _recommendation(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "do not change yet - no audit rows available"
    a_near = sum(1 for r in rows if r.get("final_scan_result") == "A_NEAR_MISS")
    b_near = sum(1 for r in rows if r.get("final_scan_result") == "B_NEAR_MISS")
    a_fails = count_reasons(rows, "engine_a_fail_reasons")
    b_fails = count_reasons(rows, "engine_b_fail_reasons")
    if b_fails.get("engine_b_confidence_passed_false", 0) >= max(3, b_near):
        return "adjust specific gate - Engine B confidence/checklist is the leading bottleneck"
    if b_fails.get("engine_b_rr_ok_false", 0) >= max(3, b_near):
        return "adjust specific gate - Engine B RR/room evidence needs review"
    if a_fails.get("below_engine_a_threshold", 0) and a_near > b_near:
        return "lower Engine A only - only after paper/backtest validation"
    if b_near > a_near:
        return "lower Engine B only - only after paper/backtest validation"
    return "do not change yet"


def build_report(rows: list[dict[str, Any]]) -> str:
    total = len(rows)
    signals = sum(1 for r in rows if r.get("final_scan_result") in {"A_ONLY", "B_ONLY", "ALIGNED"})
    by_asset = Counter(r.get("asset_type") or "unknown" for r in rows)
    signals_by_asset = Counter(
        r.get("asset_type") or "unknown"
        for r in rows
        if r.get("final_scan_result") in {"A_ONLY", "B_ONLY", "ALIGNED"}
    )
    c_dist = Counter(r.get("engine_c_consensus_type") or "UNKNOWN" for r in rows)
    final_dist = Counter(r.get("final_scan_result") or "UNKNOWN" for r in rows)
    risk_reasons = Counter()
    for row in rows:
        for reason in row.get("risk_check_fail_reasons") or []:
            risk_reasons[str(reason)] += 1
        freshness = row.get("data_freshness_status") or {}
        if isinstance(freshness, dict) and freshness.get("allowed") is False:
            risk_reasons[str(freshness.get("reason") or "freshness_block")] += 1

    a_dist = distribution([r.get("engine_a_raw_score") for r in rows])
    b_dist = distribution([r.get("engine_b_raw_score") for r in rows])
    c_score_dist = distribution([r.get("engine_c_final_conviction") for r in rows])
    current_a = next(((r.get("thresholds") or {}).get("engine_a") for r in rows if (r.get("thresholds") or {}).get("engine_a") is not None), None)
    current_b = next(((r.get("thresholds") or {}).get("engine_b") for r in rows if (r.get("thresholds") or {}).get("engine_b") is not None), None)
    near = _near_misses(rows)
    shadow = _shadow_counts(rows)

    lines = [
        "# Threshold Audit Report",
        "",
        f"Total scanned symbols: {total}",
        f"Total signals produced: {signals}",
        "",
        "## Signal Rate By Asset Type",
        "| asset type | scanned | signals | signal rate |",
        "|---|---:|---:|---:|",
    ]
    for asset in sorted(by_asset):
        lines.append(f"| {asset} | {by_asset[asset]} | {signals_by_asset[asset]} | {_pct(signals_by_asset[asset], by_asset[asset])} |")

    lines.extend([
        "",
        "## Engine A Distribution",
        f"`{a_dist}`",
        f"Current threshold: {current_a}",
        f"Within 5% below threshold: {_within_below(rows, 'engine_a_raw_score', 'engine_a', 0.05)}",
        f"Within 10% below threshold: {_within_below(rows, 'engine_a_raw_score', 'engine_a', 0.10)}",
        f"Within 15% below threshold: {_within_below(rows, 'engine_a_raw_score', 'engine_a', 0.15)}",
        "",
        "## Engine B Distribution",
        f"`{b_dist}`",
        f"Current threshold: {current_b}",
        f"Within 5% below threshold: {_within_below(rows, 'engine_b_raw_score', 'engine_b', 0.05)}",
        f"Within 10% below threshold: {_within_below(rows, 'engine_b_raw_score', 'engine_b', 0.10)}",
        f"Within 15% below threshold: {_within_below(rows, 'engine_b_raw_score', 'engine_b', 0.15)}",
        "",
        "## Engine C Distribution",
        f"A_ONLY count: {c_dist.get('A_ONLY', 0)}",
        f"B_ONLY count: {c_dist.get('B_ONLY', 0)}",
        f"ALIGNED count: {c_dist.get('ALIGNED', 0)}",
        f"CONFLICT count: {c_dist.get('CONFLICT', 0)}",
        f"WATCHLIST count: {sum(1 for r in rows if r.get('engine_c_decision_state') == 'watchlist')}",
        f"BLOCKED count: {sum(1 for r in rows if r.get('engine_c_decision_state') == 'blocked')}",
        f"Score distribution: `{c_score_dist}`",
        "",
        "## Top Fail Reasons",
        "### Engine A",
        _top_table(count_reasons(rows, "engine_a_fail_reasons")),
        "### Engine B",
        _top_table(count_reasons(rows, "engine_b_fail_reasons")),
        "### Engine C",
        _top_table(Counter(r.get("engine_c_block_watchlist_reason") or "none" for r in rows)),
        "### Risk/Freshness",
        _top_table(risk_reasons),
        "## Near Misses",
        "| symbol | asset type | engine | score | threshold | fail reason |",
        "|---|---|---|---:|---:|---|",
    ])
    if near:
        for item in near[:50]:
            lines.append(
                f"| {item['symbol']} | {item['asset_type']} | {item['engine']} | "
                f"{item['score']:.4f} | {item['threshold']:.4f} | {item['fail_reason']} |"
            )
    else:
        lines.append("| none | n/a | n/a | 0 | 0 | none |")

    lines.extend(["", "## Shadow Threshold Simulation"])
    for engine, labels in shadow.items():
        lines.append(f"### {engine}")
        lines.append("| setting | asset type | would pass |")
        lines.append("|---|---|---:|")
        for label, asset_counts in labels.items():
            if not asset_counts:
                lines.append(f"| {label} | all | 0 |")
            for asset, count in sorted(asset_counts.items()):
                lines.append(f"| {label} | {asset} | {count} |")

    lines.extend([
        "",
        "## Final Scan Result Distribution",
        _top_table(final_dist),
        "## Recommendation",
        _recommendation(rows),
        "",
        "Threshold audit mode is report-only. It does not change live, paper, freshness, or risk gates.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to signal_funnel.jsonl")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Markdown output path")
    args = parser.parse_args()
    rows = _load_rows(Path(args.input))
    report = build_report(rows)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"Wrote {out} from {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
