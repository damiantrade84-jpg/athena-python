"""Per-style Engine B min_rr calibration from replayed demo/paper outcomes.

Research tool only — it never changes thresholds. It replays the Engine B
backtest in two modes:

  Mode A (legacy): synthetic fallback-RR single TP, scale-out runner disabled.
  Mode B (scale-out): dual-TP scale-out runner enabled.

Results are split by style (intraday/swing) and asset class, reporting WR,
PF, expectancy, and max drawdown per cell, plus a min_rr floor sweep. The
recommended floor per style is the smallest candidate where Mode B expectancy
exceeds Mode A by at least ``margin`` with enough samples in both cells.

The markdown report surfaces sample size per cell; thin cells are flagged and
never produce a recommendation. New floors go live only after explicit manual
approval and a separate wiring change (see ENGINE_B_MIN_RR_*_FLOOR keys in
config.py).

Usage:
    python -m athena_research.calibrate_engine_b_min_rr \
        --pairs BTC/USDT ETH/USDT --styles intraday swing \
        --margin 0.05 --out reports/engine_b_min_rr_calibration.md
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from backtest_runner import backtest_pair_naked
from config import CONFIG

log = logging.getLogger("sentinel")

# Candidate min_rr floors swept per style (trade-level filter on rr_target).
DEFAULT_FLOORS = (1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.8, 2.0, 2.2, 2.5)
# Minimum closed trades per cell before a recommendation is allowed.
MIN_SAMPLES_PER_CELL = 30

_MODE_A_OVERRIDES = {
    "ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP": True,
    "ENGINE_B_BT_RUNNER_ENABLED": False,
}
_MODE_B_OVERRIDES = {
    "ENGINE_B_ALLOW_SYNTHETIC_FALLBACK_RR_TP": True,
    "ENGINE_B_BT_RUNNER_ENABLED": True,
}


def _with_config_overrides(overrides: dict, fn):
    """Run ``fn()`` with CONFIG keys temporarily overridden, then restore."""
    _missing = object()
    saved = {k: CONFIG.get(k, _missing) for k in overrides}
    CONFIG.update(overrides)
    try:
        return fn()
    finally:
        for k, v in saved.items():
            if v is _missing:
                CONFIG.pop(k, None)
            else:
                CONFIG[k] = v


def _trade_r(trade: dict) -> float | None:
    try:
        return float(trade.get("resultR"))
    except (TypeError, ValueError):
        return None


def compute_cell_metrics(trades: list[dict]) -> dict:
    """WR / PF / expectancy / max-DD (in R) for a list of closed trades."""
    rs = [r for r in (_trade_r(t) for t in trades) if r is not None]
    n = len(rs)
    if n == 0:
        return {
            "trades": 0,
            "win_rate": None,
            "profit_factor": None,
            "expectancy_r": None,
            "max_drawdown_r": None,
        }
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else None
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rs:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "trades": n,
        "win_rate": round(len(wins) / n, 4),
        "profit_factor": round(pf, 4) if pf is not None else None,
        "expectancy_r": round(sum(rs) / n, 4),
        "max_drawdown_r": round(max_dd, 4),
    }


def _trade_rr_target(trade: dict) -> float | None:
    try:
        return float(trade.get("rr_target"))
    except (TypeError, ValueError):
        return None


def sweep_floor_metrics(trades: list[dict], floors=DEFAULT_FLOORS) -> dict:
    """Metrics per candidate floor, keeping only trades with rr_target >= floor."""
    out = {}
    for floor in floors:
        subset = [
            t
            for t in trades
            if (_trade_rr_target(t) is not None and _trade_rr_target(t) >= floor)
        ]
        out[floor] = compute_cell_metrics(subset)
    return out


def recommend_floor(
    trades_a: list[dict],
    trades_b: list[dict],
    floors=DEFAULT_FLOORS,
    margin: float = 0.05,
    min_samples: int = MIN_SAMPLES_PER_CELL,
) -> float | None:
    """Smallest floor where Mode B expectancy beats Mode A by >= margin.

    Both cells must clear ``min_samples`` closed trades; otherwise the floor
    is skipped. Returns None when no candidate qualifies (thin data or no
    edge) — no recommendation is ever invented.
    """
    sweep_a = sweep_floor_metrics(trades_a, floors)
    sweep_b = sweep_floor_metrics(trades_b, floors)
    for floor in floors:
        a = sweep_a[floor]
        b = sweep_b[floor]
        if a["trades"] < min_samples or b["trades"] < min_samples:
            continue
        if a["expectancy_r"] is None or b["expectancy_r"] is None:
            continue
        if b["expectancy_r"] - a["expectancy_r"] >= margin:
            return floor
    return None


def _run_mode(pairs: list[dict], style: str, overrides: dict) -> list[dict]:
    trades: list[dict] = []
    for pair in pairs:
        def _one(pair=pair):
            return backtest_pair_naked(pair, style=style)

        try:
            result = _with_config_overrides(overrides, _one)
        except Exception as exc:
            log.warning(
                "[CALIBRATE-B-RR] %s %s failed: %s",
                pair.get("display"), style, exc,
            )
            continue
        for t in (result or {}).get("trades") or []:
            t = dict(t)
            t["_asset_class"] = str(pair.get("type") or "unknown").lower()
            trades.append(t)
    return trades


def run_calibration(
    pairs: list[dict],
    styles=("intraday", "swing"),
    floors=DEFAULT_FLOORS,
    margin: float = 0.05,
    min_samples: int = MIN_SAMPLES_PER_CELL,
) -> dict:
    """Replay Mode A vs Mode B per style and build the calibration summary."""
    results: dict = {"styles": {}, "margin": margin, "min_samples": min_samples}
    for style in styles:
        trades_a = _run_mode(pairs, style, _MODE_A_OVERRIDES)
        trades_b = _run_mode(pairs, style, _MODE_B_OVERRIDES)
        asset_classes = sorted(
            {t["_asset_class"] for t in trades_a + trades_b} or {"unknown"}
        )
        by_asset = {}
        for ac in asset_classes:
            a_ac = [t for t in trades_a if t["_asset_class"] == ac]
            b_ac = [t for t in trades_b if t["_asset_class"] == ac]
            by_asset[ac] = {
                "mode_a": compute_cell_metrics(a_ac),
                "mode_b": compute_cell_metrics(b_ac),
                "recommended_floor": recommend_floor(
                    a_ac, b_ac, floors, margin, min_samples
                ),
            }
        results["styles"][style] = {
            "mode_a": compute_cell_metrics(trades_a),
            "mode_b": compute_cell_metrics(trades_b),
            "floor_sweep_a": sweep_floor_metrics(trades_a, floors),
            "floor_sweep_b": sweep_floor_metrics(trades_b, floors),
            "recommended_floor": recommend_floor(
                trades_a, trades_b, floors, margin, min_samples
            ),
            "by_asset_class": by_asset,
        }
    return results


def _fmt(v) -> str:
    if v is None:
        return "n/a"
    return f"{v:.4f}" if isinstance(v, float) else str(v)


def build_report(results: dict) -> str:
    """Markdown calibration report. Surfaces sample size per cell."""
    lines = [
        "# Engine B min_rr calibration report",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Margin: {results.get('margin')} R expectancy | "
        f"min samples per cell: {results.get('min_samples')}",
        "",
        "Mode A = legacy single fallback-RR TP (runner disabled). "
        "Mode B = dual-TP scale-out runner.",
        "",
        "> Research output only. No thresholds change until values below are",
        "> explicitly approved and wired into resolve_engine_b_execution_levels.",
        "",
    ]
    for style, data in (results.get("styles") or {}).items():
        lines.append(f"## Style: {style}")
        lines.append("")
        lines.append("| cell | trades | WR | PF | expectancy (R) | max DD (R) |")
        lines.append("|---|---|---|---|---|---|")
        for label, m in (("Mode A", data["mode_a"]), ("Mode B", data["mode_b"])):
            lines.append(
                f"| {label} | {m['trades']} | {_fmt(m['win_rate'])} | "
                f"{_fmt(m['profit_factor'])} | {_fmt(m['expectancy_r'])} | "
                f"{_fmt(m['max_drawdown_r'])} |"
            )
        rec = data.get("recommended_floor")
        if rec is not None:
            lines.append("")
            lines.append(f"Recommended min_rr floor ({style}): **{rec}**")
        else:
            lines.append("")
            lines.append(
                f"No recommendation for {style}: insufficient samples or "
                "Mode B never beat Mode A by the margin."
            )
        lines.append("")
        lines.append("### Floor sweep")
        lines.append("")
        lines.append(
            "| floor | A trades | A expectancy | B trades | B expectancy |"
        )
        lines.append("|---|---|---|---|---|")
        sweep_a = data.get("floor_sweep_a") or {}
        sweep_b = data.get("floor_sweep_b") or {}
        for floor in sorted(sweep_a):
            a = sweep_a[floor]
            b = sweep_b.get(floor) or {}
            lines.append(
                f"| {floor} | {a['trades']} | {_fmt(a['expectancy_r'])} | "
                f"{b.get('trades', 0)} | {_fmt(b.get('expectancy_r'))} |"
            )
        lines.append("")
        lines.append("### By asset class")
        lines.append("")
        lines.append(
            "| asset class | A trades | A expectancy | B trades | "
            "B expectancy | recommended floor |"
        )
        lines.append("|---|---|---|---|---|---|")
        for ac, cell in (data.get("by_asset_class") or {}).items():
            a = cell["mode_a"]
            b = cell["mode_b"]
            lines.append(
                f"| {ac} | {a['trades']} | {_fmt(a['expectancy_r'])} | "
                f"{b['trades']} | {_fmt(b['expectancy_r'])} | "
                f"{_fmt(cell.get('recommended_floor'))} |"
            )
        lines.append("")
    lines.append(
        "Fidelity note: runner-trail simulation is an approximation of the "
        "live chandelier (trail_not_simulated)."
    )
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", nargs="+", required=True, help="Pair display names")
    parser.add_argument("--type", default="crypto", help="Asset type for all pairs")
    parser.add_argument("--styles", nargs="+", default=["intraday", "swing"])
    parser.add_argument("--margin", type=float, default=0.05)
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES_PER_CELL)
    parser.add_argument(
        "--out", default="reports/engine_b_min_rr_calibration.md"
    )
    args = parser.parse_args(argv)

    pairs = [
        {"display": p, "symbol": p.replace("/", ""), "type": args.type, "source": "bybit"}
        for p in args.pairs
    ]
    results = run_calibration(
        pairs,
        styles=tuple(args.styles),
        margin=args.margin,
        min_samples=args.min_samples,
    )
    report = build_report(results)
    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(report)
    print(f"Report written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
