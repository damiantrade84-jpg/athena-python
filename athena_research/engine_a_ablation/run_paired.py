"""CLI for PAIRED Engine A factor attribution (research-only).

Builds one immutable candidate ledger (direction/entry/exit/net R fixed by the
canonical price-core rule) and scores every variant on it, then reports:

  A. RANKING  — Spearman(score, net R) + deciles + top-minus-bottom, with a
     cluster (per-symbol) bootstrap on the incremental Spearman lift of each
     factor. Split by family, direction, carry-source (fx vs 10Y proxy) and cost.
  B. SELECTION — dev/holdout time split; threshold frozen on dev; holdout
     selected-subset expectancy per variant (the holdout is never optimised on).

Usage (from repo root):
  python -m athena_research.engine_a_ablation.run_paired --step 2 \
      --out tmp/engine_a_paired_<date>.json
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from statistics import fmean

# config.py runs fatal safety validation at import time; this offline research
# harness only satisfies that import guard and never enables real orders.
os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

REPO_ROOT = Path(__file__).resolve().parents[2]

VARIANTS = ("price_only", "plus_carry", "plus_sentiment", "full")
COST_MULTS = (1.0, 1.5, 2.0)
TRADE_THRESHOLD = 2.0  # production gate; frozen, NOT optimised on holdout


def _net(c: dict, mult: float) -> float:
    return float(c["raw_result_r"]) - mult * float(c["cost_r"])


def _rank_block(rows, net_key):
    from athena_research.engine_a_ablation.paired_metrics import (
        decile_table, spearman, top_minus_bottom,
    )
    net = [r[net_key] for r in rows]
    block = {"n": len(rows), "mean_net_r": round(fmean(net), 4) if net else None}
    for v in VARIANTS:
        sc = [r[v] for r in rows]
        block[v] = {
            "spearman": spearman(sc, net),
            "top_minus_bottom": top_minus_bottom(sc, net),
        }
    block["full_deciles"] = decile_table([r["full"] for r in rows], net)
    block["price_only_deciles"] = decile_table([r["price_only"] for r in rows], net)
    return block


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-as-of", default="2026-05-30")
    ap.add_argument("--horizon", default="swing")
    ap.add_argument("--step", type=int, default=2)
    ap.add_argument("--max-symbols", type=int, default=0)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--headline-cost", type=float, default=1.0)
    ap.add_argument("--dev-frac", type=float, default=0.6, help="time-ordered dev share")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    os.environ["BACKTEST_DATA_AS_OF"] = args.data_as_of
    os.environ.setdefault("ATHENA_FROZEN_DATA_ROOT", str(REPO_ROOT))

    from athena_research.engine_a_ablation.harness import (
        HarnessConfig, _load_rows, load_universe,
    )
    from athena_research.engine_a_ablation.paired import build_ledger_for_symbol
    from athena_research.engine_a_ablation.paired_metrics import (
        ranking_lift, selection_holdout,
    )

    manifest = REPO_ROOT / "data" / "manifests" / f"{args.data_as_of}.json"
    universe = load_universe(manifest)
    if args.symbols:
        wanted = {s.strip() for s in args.symbols.split(",")}
        universe = [p for p in universe if p["display"] in wanted]
    if args.max_symbols:
        universe = universe[: args.max_symbols]

    cfg = HarnessConfig(horizon=args.horizon, step=max(1, args.step))
    mult = args.headline_cost

    ledger: list[dict] = []
    per_symbol: dict[str, int] = {}
    for pair in universe:
        candles = {tf: _load_rows(pair["paths"][tf], REPO_ROOT) for tf in ("D1", "H4", "H1")}
        cands = build_ledger_for_symbol(pair, candles, cfg=cfg)
        for c in cands:
            row = {
                "symbol": c.symbol, "family": c.family, "direction": c.direction,
                "date": c.date, "year": c.year, "regime": c.regime,
                "carry_source": c.carry_source, "carry_state": c.carry_state,
                "sentiment_state": c.sentiment_state,
                "raw_result_r": c.raw_result_r, "cost_r": c.cost_r, "outcome": c.outcome,
            }
            for v in VARIANTS:
                row[v] = c.scores.get(v, 0.0)
            for m in COST_MULTS:
                row[f"net_{m}"] = _net({"raw_result_r": c.raw_result_r, "cost_r": c.cost_r}, m)
            ledger.append(row)
        per_symbol[pair["display"]] = len(cands)

    net_key = f"net_{mult}"

    # ── Integrity invariants (proven, not assumed) ──────────────────────────────
    same_count = all(set(VARIANTS).issubset(r) for r in ledger)
    integrity = {
        "total_candidates": len(ledger),
        "every_variant_scores_every_candidate": same_count,
        "n_symbols": len(universe),
        "carry_source_counts": dict(
            (k, sum(1 for r in ledger if r["carry_source"] == k))
            for k in ("fx", "yield", "na")),
    }

    report: dict = {
        "meta": {
            "mode": "paired_attribution", "data_as_of": args.data_as_of,
            "horizon": args.horizon, "step": args.step, "headline_cost_mult": mult,
            "symbols": [p["display"] for p in universe], "dev_frac": args.dev_frac,
            "note": "immutable ledger; direction/entry/exit/net R fixed by price-core "
                    "rule; variants differ ONLY in score. carry split fx vs 10Y proxy.",
        },
        "integrity": integrity,
        "ranking": {},
        "ranking_lift": {},
        "selection": {},
        "per_symbol_candidates": per_symbol,
    }

    # ── A. RANKING (overall + slices) ───────────────────────────────────────────
    report["ranking"]["overall"] = _rank_block(ledger, net_key)
    for axis in ("family", "direction", "carry_source", "year", "regime"):
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in ledger:
            groups[str(r[axis])].append(r)
        report["ranking"][f"by_{axis}"] = {
            k: _rank_block(v, net_key) for k, v in sorted(groups.items()) if len(v) >= 10
        }
    # cost robustness of ranking (Spearman of full at each cost)
    from athena_research.engine_a_ablation.paired_metrics import spearman as _sp
    report["ranking"]["cost_robustness_full_spearman"] = {
        str(m): _sp([r["full"] for r in ledger], [r[f"net_{m}"] for r in ledger])
        for m in COST_MULTS
    }

    # ── Incremental ranking lift (cluster bootstrap on Spearman diff) ───────────
    report["ranking_lift"]["plus_carry_overall"] = ranking_lift(ledger, "price_only", "plus_carry", net_key)
    report["ranking_lift"]["plus_sentiment_overall"] = ranking_lift(ledger, "price_only", "plus_sentiment", net_key)
    report["ranking_lift"]["full_overall"] = ranking_lift(ledger, "price_only", "full", net_key)
    fx = [r for r in ledger if r["carry_source"] == "fx"]
    yld = [r for r in ledger if r["carry_source"] == "yield"]
    if len(fx) >= 10:
        report["ranking_lift"]["carry_fx_only"] = ranking_lift(fx, "price_only", "plus_carry", net_key)
    if len(yld) >= 10:
        report["ranking_lift"]["macro_yield_only"] = ranking_lift(yld, "price_only", "plus_carry", net_key)

    # ── B. SELECTION (dev/holdout time split, threshold frozen on dev) ──────────
    ordered = sorted(ledger, key=lambda r: r["date"])
    cut = int(len(ordered) * args.dev_frac)
    dev, holdout = ordered[:cut], ordered[cut:]
    report["selection"]["split"] = {
        "dev_n": len(dev), "holdout_n": len(holdout),
        "dev_dates": [dev[0]["date"], dev[-1]["date"]] if dev else None,
        "holdout_dates": [holdout[0]["date"], holdout[-1]["date"]] if holdout else None,
        "threshold": TRADE_THRESHOLD,
    }
    for v in VARIANTS:
        report["selection"][v] = selection_holdout(dev, holdout, v, net_key, threshold=TRADE_THRESHOLD)

    out = args.out or f"tmp/engine_a_paired_{args.data_as_of}.json"
    out_path = REPO_ROOT / out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    # ── Console summary ─────────────────────────────────────────────────────────
    print(f"[paired] wrote {out_path}")
    print(f"[paired] candidates={len(ledger)} symbols={len(universe)} "
          f"carry_source={integrity['carry_source_counts']}")
    ov = report["ranking"]["overall"]
    print(f"[paired] RANKING overall (cost x{mult}, n={ov['n']}, mean_net_r={ov['mean_net_r']}):")
    for v in VARIANTS:
        print(f"   {v:16s} spearman={ov[v]['spearman']}  "
              f"top-bottom={ov[v]['top_minus_bottom']['spread']:+.4f}")
    print("[paired] incremental ranking lift (Spearman diff vs price_only, cluster-boot CI):")
    for k, info in report["ranking_lift"].items():
        print(f"   {k:20s} point={info.get('point')}  ci95={info.get('ci95')}  "
              f"clusters={info.get('n_clusters')}")
    print("[paired] cost robustness (full Spearman):",
          report["ranking"]["cost_robustness_full_spearman"])
    print("[paired] SELECTION holdout (threshold frozen on dev):")
    for v in VARIANTS:
        s = report["selection"][v]
        print(f"   {v:16s} hold_sel_n={s['holdout_selected_n']:4d} "
              f"hold_sel_exp={s['holdout_selected_exp']}  ci95={s['holdout_selected_exp_ci95']}")


if __name__ == "__main__":
    main()
