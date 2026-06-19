"""CLI for the Engine A ENTRY/DIRECTION edge forensic audit (research-only).

Separates DIRECTION edge, ENTRY-timing and EXIT/label destruction on ONE
immutable canonical ledger, then reports per family / regime / year / setup with
symbol-cluster-aware CIs and 1/1.5/2x cost stress. Writes JSON + prints summary.

Usage (repo root):
  python -m athena_research.engine_a_ablation.run_direction_audit --step 2 \
      --out tmp/engine_a_direction_<date>.json
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from statistics import fmean
from pathlib import Path

os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

REPO_ROOT = Path(__file__).resolve().parents[2]
COST_MULTS = (1.0, 1.5, 2.0)
VOTE_KEYS = ("trend", "rsi", "macd", "di", "adx", "location", "volume")


def _pearson(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    mx = fmean(p[0] for p in pairs)
    my = fmean(p[1] for p in pairs)
    num = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    dx = sum((p[0] - mx) ** 2 for p in pairs)
    dy = sum((p[1] - my) ** 2 for p in pairs)
    den = (dx * dy) ** 0.5
    return round(num / den, 3) if den > 0 else None


def _net(c, mult):
    return c.raw_result_r - mult * c.cost_r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-as-of", default="2026-05-30")
    ap.add_argument("--horizon", default="swing")
    ap.add_argument("--step", type=int, default=2)
    ap.add_argument("--max-symbols", type=int, default=0)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--dev-frac", type=float, default=0.6)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    os.environ["BACKTEST_DATA_AS_OF"] = args.data_as_of
    os.environ.setdefault("ATHENA_FROZEN_DATA_ROOT", str(REPO_ROOT))

    from athena_research.engine_a_ablation.harness import (
        HarnessConfig, _load_rows, load_universe,
    )
    from athena_research.engine_a_ablation.direction_audit import (
        FWD_HORIZONS, build_event_ledger_for_symbol,
    )
    from athena_research.engine_a_ablation.paired_metrics import cluster_bootstrap

    manifest = REPO_ROOT / "data" / "manifests" / f"{args.data_as_of}.json"
    universe = load_universe(manifest)
    if args.symbols:
        wanted = {s.strip() for s in args.symbols.split(",")}
        universe = [p for p in universe if p["display"] in wanted]
    if args.max_symbols:
        universe = universe[: args.max_symbols]

    cfg = HarnessConfig(horizon=args.horizon, step=max(1, args.step))

    ledger = []
    per_symbol = {}
    for pair in universe:
        candles = {tf: _load_rows(pair["paths"][tf], REPO_ROOT) for tf in ("D1", "H4", "H1")}
        cands = build_event_ledger_for_symbol(pair, candles, cfg=cfg)
        ledger.extend(cands)
        per_symbol[pair["display"]] = len(cands)

    hkeys = [str(h) for h in FWD_HORIZONS] + ["maxhold"]

    def _rows_to_dicts(rows):
        return [{"symbol": c.symbol, "v": c.fwd_r["maxhold"]} for c in rows]

    def _dir_block(rows):
        if not rows:
            return {"n": 0}
        blk = {"n": len(rows)}
        for hk in hkeys:
            vals = [c.fwd_r[hk] for c in rows]
            blk[f"fwd_r_{hk}_mean"] = round(fmean(vals), 4)
            blk[f"hit_{hk}"] = round(sum(1 for v in vals if v > 0) / len(vals), 3)
        blk["beta_maxhold_mean"] = round(fmean(c.fwd_beta["maxhold"] for c in rows), 4)
        # cluster CI on the direction edge (raw fwd_r to maxhold)
        cb = cluster_bootstrap(_rows_to_dicts(rows),
                               lambda rr: fmean(r["v"] for r in rr) if rr else None)
        blk["fwd_r_maxhold_ci95"] = cb["ci95"]
        blk["n_clusters"] = cb["n_clusters"]
        # long/short split of the direction edge
        lo = [c.fwd_r["maxhold"] for c in rows if c.direction == "LONG"]
        sh = [c.fwd_r["maxhold"] for c in rows if c.direction == "SHORT"]
        blk["long_n"] = len(lo); blk["short_n"] = len(sh)
        blk["long_fwd_r_maxhold"] = round(fmean(lo), 4) if lo else None
        blk["short_fwd_r_maxhold"] = round(fmean(sh), 4) if sh else None
        return blk

    def _exit_block(rows):
        if not rows:
            return {"n": 0}
        return {
            "n": len(rows),
            "raw_to_maxhold_mean": round(fmean(c.fwd_r["maxhold"] for c in rows), 4),
            "structural_net_x1_mean": round(fmean(_net(c, 1.0) for c in rows), 4),
            "structural_net_x1.5_mean": round(fmean(_net(c, 1.5) for c in rows), 4),
            "structural_net_x2_mean": round(fmean(_net(c, 2.0) for c in rows), 4),
            "mfe_r_mean": round(fmean(c.mfe_r for c in rows), 4),
            "mae_r_mean": round(fmean(c.mae_r for c in rows), 4),
            "reached_1r_pct": round(sum(1 for c in rows if c.reached_1r) / len(rows), 3),
            "win_rate_structural": round(
                sum(1 for c in rows if c.raw_result_r > 0) / len(rows), 3),
        }

    report = {
        "meta": {
            "mode": "direction_entry_exit_forensic", "data_as_of": args.data_as_of,
            "horizon": args.horizon, "step": args.step, "dev_frac": args.dev_frac,
            "n_symbols": len(universe), "total_candidates": len(ledger),
            "symbols": [p["display"] for p in universe],
            "note": "canonical price-core direction; raw fwd_r = entry-anchored, "
                    "NO SL/TP/cost, normalised by |entry-sl|; beta = long-equivalent.",
        },
        "per_symbol_candidates": per_symbol,
        "direction": {}, "exit_separation": {}, "entry_timing": {},
        "collinearity": {}, "setup_baselines": {}, "validation": {},
    }

    # ── Part 2: DIRECTION audit ─────────────────────────────────────────────
    report["direction"]["overall"] = _dir_block(ledger)
    for axis in ("family", "regime", "year", "direction", "setup_id"):
        groups = defaultdict(list)
        for c in ledger:
            groups[str(getattr(c, axis))].append(c)
        report["direction"][f"by_{axis}"] = {
            k: _dir_block(v) for k, v in sorted(groups.items()) if len(v) >= 10
        }

    # ── Part 4: EXIT/label separation ───────────────────────────────────────
    report["exit_separation"]["overall"] = _exit_block(ledger)
    fam_groups = defaultdict(list)
    for c in ledger:
        fam_groups[c.family].append(c)
    report["exit_separation"]["by_family"] = {
        k: _exit_block(v) for k, v in sorted(fam_groups.items()) if len(v) >= 10
    }

    # ── Part 3: ENTRY-timing variants (direction fixed) ─────────────────────
    def _entry_block(rows):
        if not rows:
            return {"n": 0}
        keys = ("current", "next_open", "delay1", "delay2")
        out = {"n": len(rows)}
        for k in keys:
            vals = [c.entry_variants[k] for c in rows if k in c.entry_variants]
            out[k] = round(fmean(vals), 4) if vals else None
        return out
    report["entry_timing"]["overall"] = _entry_block(ledger)
    report["entry_timing"]["by_family"] = {
        k: _entry_block(v) for k, v in sorted(fam_groups.items()) if len(v) >= 10
    }

    # ── Part 5: COLLINEARITY ────────────────────────────────────────────────
    corr = {}
    for i, a in enumerate(VOTE_KEYS):
        for b in VOTE_KEYS[i + 1:]:
            corr[f"{a}~{b}"] = _pearson([c.votes.get(a) for c in ledger],
                                        [c.votes.get(b) for c in ledger])
    fwd6 = [c.fwd_r["6"] for c in ledger]
    fwd_info = {k: _pearson([c.votes.get(k) for c in ledger], fwd6) for k in VOTE_KEYS}
    report["collinearity"] = {
        "pairwise_vote_pearson": corr,
        "vote_vs_fwd_r_6_pearson": fwd_info,
        "note": "duplicates: |r|>0.7; forward info: corr(vote, raw fwd_r at 6 bars).",
    }

    # ── Part 6: SETUP baselines (family x setup) ────────────────────────────
    setup_groups = defaultdict(list)
    for c in ledger:
        setup_groups[(c.family, c.setup_id)].append(c)
    report["setup_baselines"] = {
        f"{fam}|{sid}": {
            "n": len(v),
            "raw_to_maxhold_mean": round(fmean(x.fwd_r["maxhold"] for x in v), 4),
            "structural_net_x1": round(fmean(_net(x, 1.0) for x in v), 4),
            "structural_net_x1.5": round(fmean(_net(x, 1.5) for x in v), 4),
            "long_n": sum(1 for x in v if x.direction == "LONG"),
            "short_n": sum(1 for x in v if x.direction == "SHORT"),
            "fwd_r_maxhold_ci95": cluster_bootstrap(
                [{"symbol": x.symbol, "v": x.fwd_r["maxhold"]} for x in v],
                lambda rr: fmean(r["v"] for r in rr) if rr else None)["ci95"],
        }
        for (fam, sid), v in sorted(setup_groups.items(), key=lambda kv: str(kv[0]))
        if len(v) >= 15
    }

    # ── Part 8: VALIDATION — dev/holdout on direction edge + cost stress ────
    ordered = sorted(ledger, key=lambda c: c.date)
    cut = int(len(ordered) * args.dev_frac)
    dev, hold = ordered[:cut], ordered[cut:]
    report["validation"] = {
        "split": {
            "dev_n": len(dev), "holdout_n": len(hold),
            "dev_dates": [dev[0].date, dev[-1].date] if dev else None,
            "holdout_dates": [hold[0].date, hold[-1].date] if hold else None,
        },
        "dev_direction": _dir_block(dev),
        "holdout_direction": _dir_block(hold),
        "cost_stress_structural_net": {
            str(m): round(fmean(_net(c, m) for c in ledger), 4) for m in COST_MULTS
        },
    }

    out = args.out or f"tmp/engine_a_direction_{args.data_as_of}.json"
    out_path = REPO_ROOT / out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    # ── Console summary ─────────────────────────────────────────────────────
    ov = report["direction"]["overall"]
    print(f"[dir] wrote {out_path}")
    print(f"[dir] candidates={len(ledger)} symbols={len(universe)} clusters={ov['n_clusters']}")
    print(f"[dir] DIRECTION raw fwd_r means: " +
          " ".join(f"{hk}={ov[f'fwd_r_{hk}_mean']:+.3f}" for hk in hkeys))
    print(f"[dir]            hit rates:       " +
          " ".join(f"{hk}={ov[f'hit_{hk}']:.2f}" for hk in hkeys))
    print(f"[dir] maxhold CI95={ov['fwd_r_maxhold_ci95']} beta={ov['beta_maxhold_mean']:+.3f} "
          f"long={ov['long_fwd_r_maxhold']} short={ov['short_fwd_r_maxhold']}")
    ex = report["exit_separation"]["overall"]
    print(f"[exit] raw_to_maxhold={ex['raw_to_maxhold_mean']:+.3f} "
          f"struct_net_x1={ex['structural_net_x1_mean']:+.3f} "
          f"mfe={ex['mfe_r_mean']:+.3f} mae={ex['mae_r_mean']:+.3f} "
          f"reached_1r={ex['reached_1r_pct']:.2f} win={ex['win_rate_structural']:.2f}")
    print("[dir] by_family raw fwd_r maxhold:")
    for k, b in report["direction"]["by_family"].items():
        print(f"   {k:12s} n={b['n']:4d} fwd_r_maxhold={b['fwd_r_maxhold_mean']:+.3f} "
              f"CI={b['fwd_r_maxhold_ci95']} L={b['long_fwd_r_maxhold']} S={b['short_fwd_r_maxhold']}")
    et = report["entry_timing"]["overall"]
    print(f"[entry] current={et['current']:+.3f} next_open={et['next_open']:+.3f} "
          f"delay1={et['delay1']:+.3f} delay2={et['delay2']:+.3f}")
    print(f"[valid] holdout dir fwd_r_maxhold="
          f"{report['validation']['holdout_direction'].get('fwd_r_maxhold_mean')} "
          f"cost_stress={report['validation']['cost_stress_structural_net']}")


if __name__ == "__main__":
    main()
