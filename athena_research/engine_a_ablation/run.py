"""CLI for the Engine A factor-ablation harness (research-only).

Usage (from repo root):
  python -m athena_research.engine_a_ablation.run \
      --data-as-of 2026-05-30 --step 2 --out tmp/engine_a_ablation_<date>.json
  python -m athena_research.engine_a_ablation.run --max-symbols 4 --step 4   # quick smoke

Sets BACKTEST_DATA_AS_OF so carry/COT resolve point-in-time from the frozen
factor store. Writes a sliced JSON report. No execution side effects.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

# config.py runs fatal safety validation at import time (same guard the pytest
# conftest satisfies). This research harness is offline/no-execution; this token
# only satisfies the import guard and never enables real orders.
os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

REPO_ROOT = Path(__file__).resolve().parents[2]


def _net(trade: dict, mult: float) -> float:
    return float(trade["raw_result_r"]) - mult * float(trade["cost_r"])


def _slice(trades: list[dict], key: str, mult: float):
    from athena_research.engine_a_ablation.metrics import summarize
    groups: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        groups[str(t[key])].append(_net(t, mult))
    return {k: summarize(v) for k, v in sorted(groups.items())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-as-of", default="2026-05-30")
    ap.add_argument("--horizon", default="swing")
    ap.add_argument("--step", type=int, default=1, help="evaluate every Nth H4 bar")
    ap.add_argument("--max-symbols", type=int, default=0, help="0 = all")
    ap.add_argument("--symbols", default="", help="comma-separated display filter")
    ap.add_argument("--headline-cost", type=float, default=1.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    os.environ["BACKTEST_DATA_AS_OF"] = args.data_as_of
    os.environ.setdefault("ATHENA_FROZEN_DATA_ROOT", str(REPO_ROOT))

    from athena_research.engine_a_ablation.harness import (
        HarnessConfig, _load_rows, build_variants, load_universe, run_symbol,
    )
    from athena_research.engine_a_ablation.metrics import (
        bootstrap_ci, concentration, deflated_sharpe, pbo, score_return_monotonicity,
        summarize,
    )

    manifest = REPO_ROOT / "data" / "manifests" / f"{args.data_as_of}.json"
    universe = load_universe(manifest)
    if args.symbols:
        wanted = {s.strip() for s in args.symbols.split(",")}
        universe = [p for p in universe if p["display"] in wanted]
    if args.max_symbols:
        universe = universe[: args.max_symbols]

    cfg = HarnessConfig(horizon=args.horizon, step=max(1, args.step))
    all_trades: list[dict] = []
    per_symbol_counts: dict[str, int] = {}
    for pair in universe:
        candles = {tf: _load_rows(pair["paths"][tf], REPO_ROOT) for tf in ("D1", "H4", "H1")}
        variants = build_variants(_family(pair))
        trades = run_symbol(pair, candles, cfg=cfg, variants=variants)
        all_trades.extend(trades)
        per_symbol_counts[pair["display"]] = sum(1 for t in trades if t["variant"] == "shadow_full")

    by_variant: dict[str, list[dict]] = defaultdict(list)
    for t in all_trades:
        by_variant[t["variant"]].append(t)

    mult = args.headline_cost
    n_variants = len(by_variant)
    report: dict = {
        "meta": {
            "data_as_of": args.data_as_of, "horizon": args.horizon, "step": args.step,
            "symbols": [p["display"] for p in universe], "n_symbols": len(universe),
            "headline_cost_mult": mult, "total_trade_rows": len(all_trades),
            "note": "research-only ablation; carry+sentiment sourced PIT; "
                    "intermarket/macro/microstructure = insufficient coverage this pass",
        },
        "variants": {},
        "cost_sensitivity": {},
        "factor_lift_net": {},
        "monotonicity": {},
        "concentration": {},
    }

    for name, trades in sorted(by_variant.items()):
        net = [_net(t, mult) for t in trades]
        overall = summarize(net)
        overall["ci95"] = bootstrap_ci(net)
        overall["dsr"] = deflated_sharpe(net, n_trials=max(1, n_variants))
        report["variants"][name] = {
            "overall": overall,
            "by_family": _slice(trades, "family", mult),
            "by_direction": _slice(trades, "direction", mult),
            "by_year": _slice(trades, "year", mult),
            "by_regime": _slice(trades, "regime", mult),
        }
        report["cost_sensitivity"][name] = {
            str(m): summarize([_net(t, m) for t in trades])["expectancy"]
            for m in cfg.cost_multiples
        }
        buckets: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            buckets[t["score_bucket"]].append(_net(t, mult))
        report["monotonicity"][name] = score_return_monotonicity(buckets)
        sym_r: dict[str, float] = defaultdict(float)
        for t in trades:
            sym_r[t["symbol"]] += _net(t, mult)
        report["concentration"][name] = concentration(sym_r)

    # Factor incremental lift = full expectancy - leave-one-out expectancy.
    full_exp = report["variants"].get("shadow_full", {}).get("overall", {}).get("expectancy")
    if full_exp is not None:
        for name in list(report["variants"]):
            if name.startswith("loo_"):
                f = name[4:]
                loo_exp = report["variants"][name]["overall"]["expectancy"]
                report["factor_lift_net"][f] = round(full_exp - loo_exp, 4)

    main_variants = {k: [_net(t, mult) for t in v]
                     for k, v in by_variant.items()
                     if k in ("current", "shadow_full", "shadow_price_only")}
    report["pbo"] = pbo(main_variants)
    report["per_symbol_shadow_full_trades"] = per_symbol_counts

    out = args.out or f"tmp/engine_a_ablation_{args.data_as_of}.json"
    out_path = REPO_ROOT / out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print(f"[ablation] wrote {out_path}")
    print(f"[ablation] symbols={len(universe)} trade_rows={len(all_trades)} variants={n_variants}")
    print(f"[ablation] headline net expectancy (cost x{mult}):")
    for name in sorted(report["variants"]):
        ov = report["variants"][name]["overall"]
        print(f"   {name:22s} n={ov['n']:4d}  exp={ov['expectancy']:+.4f}R  "
              f"sqn={ov['sqn']:+.2f}  win%={ov['win_rate']:.1f}  ci95={ov.get('ci95')}")
    if report["factor_lift_net"]:
        print("[ablation] factor incremental lift (full - LOO):")
        for f, lift in sorted(report["factor_lift_net"].items(), key=lambda kv: -kv[1]):
            print(f"   {f:14s} {lift:+.4f}R")
    print(f"[ablation] PBO={report['pbo']}")


def _family(pair: dict) -> str:
    from engine_a_v3.routing import route_specialist
    return route_specialist(pair).family


if __name__ == "__main__":
    main()
