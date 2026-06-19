"""Engine A recovery-lead validation: H1_FOREX_INVERSE + H2_RANGE_BREAKOUT.

PRE-REGISTERED, FROZEN before viewing results (do not change after running):

H1_FOREX_INVERSE — on the IDENTICAL canonical price-core candidate ledger (forex
only), compare directions: original / inverse / long_only / short_only / random
(seed 42) / drift(+1 always). Risk unit = ATR(14) on H4 at the decision bar.
Entry = next-bar open. Primary exit = time-exit at max_hold (exit-agnostic for a
direction test). Net = gross - mult*cost_r using the production _cost_r with
|entry-sl|=ATR. Path stats (MFE/MAE/time-to/first-passage) reported as
distributions for both original and inverse direction.

H2_RANGE_BREAKOUT — the EXISTING range-breakout setup (direction_audit setup_id
== "range_breakout"), production structural entry/exit unchanged, reported
SEPARATELY for equity_etf and commodity with net cluster CIs.

Research-only. No live state / Engine B / Engine D / executor writes.

Usage:  python -m athena_research.engine_a_ablation.run_recovery_leads --out tmp/engine_a_recovery_<date>.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
from collections import defaultdict
from statistics import fmean
from pathlib import Path

os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

REPO_ROOT = Path(__file__).resolve().parents[2]
COST_MULTS = (1.0, 1.5, 2.0)
FOREX = {"EUR/USD", "GBP/USD", "USD/JPY", "GBP/JPY"}
VARIANTS = ("original", "inverse", "long_only", "short_only", "random", "drift")


def _atr14(prefix: list[dict], period: int = 14) -> float | None:
    if len(prefix) < period + 1:
        return None
    trs = []
    for i in range(len(prefix) - period, len(prefix)):
        h = float(prefix[i]["high"]); l = float(prefix[i]["low"])
        pc = float(prefix[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs) / period
    return atr if atr > 0 else None


def _path_stats(window: list[dict], dsign: float, entry: float, risk: float) -> dict:
    mfe = mae = 0.0
    t_mfe = t_mae = 0
    t_p1 = t_m1 = t_p05 = t_m05 = None
    for t, bar in enumerate(window):
        hi = float(bar["high"]); lo = float(bar["low"])
        if dsign > 0:
            fav = (hi - entry) / risk; adv = (lo - entry) / risk
        else:
            fav = (entry - lo) / risk; adv = (entry - hi) / risk
        if fav > mfe:
            mfe = fav; t_mfe = t
        if adv < mae:
            mae = adv; t_mae = t
        if t_p1 is None and fav >= 1.0:
            t_p1 = t
        if t_m1 is None and adv <= -1.0:
            t_m1 = t
        if t_p05 is None and fav >= 0.5:
            t_p05 = t
        if t_m05 is None and adv <= -0.5:
            t_m05 = t

    def _first(a, b):
        if a is None and b is None:
            return None
        if b is None:
            return 1
        if a is None:
            return 0
        return 1 if a <= b else 0

    return {
        "mfe": round(mfe, 4), "mae": round(mae, 4), "t_mfe": t_mfe, "t_mae": t_mae,
        "p1_first": _first(t_p1, t_m1), "p05_first": _first(t_p05, t_m05),
        "mfe_before_mae": (1 if t_mfe < t_mae else 0 if t_mfe > t_mae else None),
    }


def _quantiles(xs, qs=(0.1, 0.25, 0.5, 0.75, 0.9)):
    s = sorted(v for v in xs if v is not None)
    if not s:
        return {}
    return {f"p{int(q*100)}": round(s[min(len(s) - 1, int(q * len(s)))], 4) for q in qs}


def _prob(xs):
    v = [x for x in xs if x is not None]
    return round(sum(v) / len(v), 3) if v else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-as-of", default="2026-05-30")
    ap.add_argument("--step", type=int, default=2)
    ap.add_argument("--dev-frac", type=float, default=0.6)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    os.environ["BACKTEST_DATA_AS_OF"] = args.data_as_of
    os.environ.setdefault("ATHENA_FROZEN_DATA_ROOT", str(REPO_ROOT))

    from bisect import bisect_right
    from engine_a_v3.backtest import _cost_r
    from engine_a_v3.evaluator import _parse_time
    from engine_a_v3.profile import baseline_profile
    from engine_a_v3.routing import route_specialist
    from athena_research.engine_a_ablation.harness import (
        HarnessConfig, _COSTS, _load_rows, _regime, load_universe,
    )
    from athena_research.engine_a_ablation.context_builder import build_pit_context
    from athena_research.engine_a_ablation.shadow_scorer import (
        PRICE_FACTORS, aggregate, compute_components,
    )
    from athena_research.engine_a_ablation.direction_audit import (
        _price_only_weights, build_event_ledger_for_symbol,
    )
    from athena_research.engine_a_ablation.paired_metrics import cluster_bootstrap

    manifest = REPO_ROOT / "data" / "manifests" / f"{args.data_as_of}.json"
    universe = load_universe(manifest)
    cfg = HarnessConfig(step=max(1, args.step))
    rng = random.Random(42)

    # ── H1: forex inversion ledger (ATR-normalised, time-exit) ──────────────
    recs: list[dict] = []
    orient_samples = []
    for pair in (p for p in universe if p["display"] in FOREX):
        candles = {tf: _load_rows(pair["paths"][tf], REPO_ROOT) for tf in ("D1", "H4", "H1")}
        route = route_specialist(pair)
        profile = baseline_profile(route.score_group, cfg.horizon)
        costs = _COSTS.get(route.family, _COSTS["unknown"])
        pw = _price_only_weights(route.family)
        primary = list(candles["H4"])
        n = len(primary)
        tf_rows = {tf: list(r or []) for tf, r in candles.items()}
        tf_times = {tf: [str(c.get("time") or c.get("datetime")) for c in r] for tf, r in tf_rows.items()}
        cap = cfg.prefix_cap
        cooldown = cfg.warmup
        for index in range(cfg.warmup, n - 1):
            if index < cooldown:
                continue
            if cfg.step > 1 and (index - cfg.warmup) % cfg.step != 0:
                continue
            cutoff = str(primary[index].get("time") or primary[index].get("datetime"))
            as_of = cutoff[:10]
            if bisect_right(tf_times.get("D1", []), cutoff) < cfg.min_d1:
                continue
            prefix = {tf: r[max(0, bisect_right(tf_times[tf], cutoff) - cap): bisect_right(tf_times[tf], cutoff)]
                      for tf, r in tf_rows.items()}
            h4_prefix = prefix["H4"]
            atr = _atr14(h4_prefix)
            if atr is None:
                continue
            ctx = build_pit_context(pair["display"], as_of)
            bar = compute_components(route, cfg.horizon, prefix, context=ctx, profile=profile)
            canon = aggregate(bar, include=set(PRICE_FACTORS), weights=pw, threshold=profile.trade_threshold)
            if canon.direction not in ("LONG", "SHORT"):
                continue
            dsign = 1.0 if canon.direction == "LONG" else -1.0
            entry = float(primary[index + 1]["open"])
            jx = min(n - 1, index + cfg.max_hold_bars)
            close_exit = float(primary[jx]["close"])
            window = primary[index + 1: jx + 1]
            et = _parse_time(primary[index + 1].get("time") or primary[index + 1].get("datetime"))
            xt = _parse_time(primary[jx].get("time") or primary[jx].get("datetime"))
            if et is None or xt is None:
                continue
            cost_r = _cost_r(entry, entry - atr, spread_bps=costs["spread"], commission_bps=costs["commission"],
                             slippage_bps=costs["slippage"], swap_bps_per_day=0.0,
                             entry_time=et, exit_time=xt)
            base_gross = (close_exit - entry) / atr           # long-equivalent drift
            rand_dir = 1.0 if rng.random() < 0.5 else -1.0
            rec = {
                "symbol": pair["display"], "year": as_of[:4], "regime": _regime(h4_prefix),
                "canon_dir": canon.direction, "cost_r": round(cost_r, 6),
                "gross": {
                    "original": round(dsign * base_gross, 6),
                    "inverse": round(-dsign * base_gross, 6),
                    "long_only": round(base_gross, 6) if canon.direction == "LONG" else None,
                    "short_only": round(-base_gross, 6) if canon.direction == "SHORT" else None,
                    "random": round(rand_dir * base_gross, 6),
                    "drift": round(base_gross, 6),
                },
                "path_orig": _path_stats(window, dsign, entry, atr),
                "path_inv": _path_stats(window, -dsign, entry, atr),
                "date": cutoff,
            }
            recs.append(rec)
            cooldown = index + cfg.max_hold_bars + 1
            if len(orient_samples) < 8 and rng.random() < 0.02:
                orient_samples.append({"pair": pair["display"], "dir": canon.direction,
                                       "entry": round(entry, 5), "close_exit": round(close_exit, 5),
                                       "gross_original": rec["gross"]["original"]})

    def _net(rec, variant, mult):
        g = rec["gross"][variant]
        return None if g is None else g - mult * rec["cost_r"]

    def _agg(rows, variant, mult):
        vals = [v for v in (_net(r, variant, mult) for r in rows) if v is not None]
        return round(fmean(vals), 4) if vals else None

    def _ci(rows, variant, mult):
        data = [{"symbol": r["symbol"], "v": _net(r, variant, mult)} for r in rows
                if _net(r, variant, mult) is not None]
        cb = cluster_bootstrap(data, lambda rr: fmean(x["v"] for x in rr) if rr else None)
        return cb["ci95"], cb["n_clusters"]

    def _hit(rows, variant):
        vals = [r["gross"][variant] for r in rows if r["gross"][variant] is not None]
        return round(sum(1 for v in vals if v > 0) / len(vals), 3) if vals else None

    def _variant_block(rows):
        out = {"n": len(rows)}
        for v in VARIANTS:
            ci15, ncl = _ci(rows, v, 1.5)
            out[v] = {
                "gross": _agg_gross(rows, v), "net_x1": _agg(rows, v, 1.0),
                "net_x1.5": _agg(rows, v, 1.5), "net_x2": _agg(rows, v, 2.0),
                "hit": _hit(rows, v), "net_x1.5_ci95": ci15, "n_clusters": ncl,
                "n_dir": sum(1 for r in rows if r["gross"][v] is not None),
            }
        return out

    def _agg_gross(rows, variant):
        vals = [r["gross"][variant] for r in rows if r["gross"][variant] is not None]
        return round(fmean(vals), 4) if vals else None

    report = {
        "meta": {
            "experiment": "recovery_leads", "data_as_of": args.data_as_of, "step": args.step,
            "forex_pairs": sorted(FOREX), "risk_unit": "ATR14_H4", "exit": "time_exit_maxhold",
            "n_forex_candidates": len(recs),
            "history": "2023-07 .. 2026-05 (~2.8y, single rate cycle)",
            "data_limits": "4 forex pairs (no expansion), 4 tech equities, 3 commodities; "
                           "no unseen-symbol holdout possible; ~2.8y history only.",
        },
        "phase0_orientation_samples": orient_samples,
        "h1_forex_inverse": {}, "h2_range_breakout": {}, "phase5_path": {},
    }

    # H1 overall + slices
    report["h1_forex_inverse"]["overall"] = _variant_block(recs)
    for axis in ("year", "regime", "symbol"):
        groups = defaultdict(list)
        for r in recs:
            groups[r[axis]].append(r)
        report["h1_forex_inverse"][f"by_{axis}"] = {
            k: _variant_block(v) for k, v in sorted(groups.items()) if len(v) >= 10
        }
    # chronological holdout
    ordered = sorted(recs, key=lambda r: r["date"])
    cut = int(len(ordered) * args.dev_frac)
    dev, hold = ordered[:cut], ordered[cut:]
    report["h1_forex_inverse"]["holdout"] = {
        "dev_dates": [dev[0]["date"][:10], dev[-1]["date"][:10]] if dev else None,
        "holdout_dates": [hold[0]["date"][:10], hold[-1]["date"][:10]] if hold else None,
        "dev_original_net_x1.5": _agg(dev, "original", 1.5),
        "dev_inverse_net_x1.5": _agg(dev, "inverse", 1.5),
        "holdout_original_net_x1.5": _agg(hold, "original", 1.5),
        "holdout_inverse_net_x1.5": _agg(hold, "inverse", 1.5),
        "holdout_inverse_ci95": _ci(hold, "inverse", 1.5)[0],
    }

    # ── Phase 5: path distributions (original vs inverse) ───────────────────
    def _path_report(key):
        return {
            "mfe_quantiles": _quantiles([r[key]["mfe"] for r in recs]),
            "mae_quantiles": _quantiles([r[key]["mae"] for r in recs]),
            "t_mfe_quantiles": _quantiles([r[key]["t_mfe"] for r in recs]),
            "t_mae_quantiles": _quantiles([r[key]["t_mae"] for r in recs]),
            "p_mfe_before_mae": _prob([r[key]["mfe_before_mae"] for r in recs]),
            "p_plus05_before_minus05": _prob([r[key]["p05_first"] for r in recs]),
            "p_plus1_before_minus1": _prob([r[key]["p1_first"] for r in recs]),
        }
    report["phase5_path"] = {"forex_original": _path_report("path_orig"),
                             "forex_inverse": _path_report("path_inv")}

    # ── H2: range-breakout (production structural), equity_etf & commodity ──
    for fam, names in (("equity_etf", {"AAPL", "MSFT", "NVDA", "TSLA"}),
                       ("commodity", {"WTI Oil", "XAG/USD", "XAU/USD"})):
        cands = []
        for pair in (p for p in universe if p["display"] in names):
            candles = {tf: _load_rows(pair["paths"][tf], REPO_ROOT) for tf in ("D1", "H4", "H1")}
            for c in build_event_ledger_for_symbol(pair, candles, cfg=cfg):
                if c.setup_id == "range_breakout":
                    cands.append(c)
        if not cands:
            report["h2_range_breakout"][fam] = {"n": 0}
            continue

        def _netc(c, m):
            return c.raw_result_r - m * c.cost_r
        data15 = [{"symbol": c.symbol, "v": _netc(c, 1.5)} for c in cands]
        cb = cluster_bootstrap(data15, lambda rr: fmean(x["v"] for x in rr) if rr else None)
        by_sym = defaultdict(int)
        for c in cands:
            by_sym[c.symbol] += 1
        top = max(by_sym.values()) / len(cands)
        report["h2_range_breakout"][fam] = {
            "n": len(cands), "symbols": dict(by_sym), "n_clusters": cb["n_clusters"],
            "long_n": sum(1 for c in cands if c.direction == "LONG"),
            "short_n": sum(1 for c in cands if c.direction == "SHORT"),
            "gross_raw_to_maxhold": round(fmean(c.fwd_r["maxhold"] for c in cands), 4),
            "net_x1": round(fmean(_netc(c, 1.0) for c in cands), 4),
            "net_x1.5": round(fmean(_netc(c, 1.5) for c in cands), 4),
            "net_x2": round(fmean(_netc(c, 2.0) for c in cands), 4),
            "net_x1.5_ci95": cb["ci95"], "top_symbol_concentration": round(top, 3),
            "win_rate": round(sum(1 for c in cands if c.raw_result_r > 0) / len(cands), 3),
        }

    out = args.out or f"tmp/engine_a_recovery_{args.data_as_of}.json"
    out_path = REPO_ROOT / out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    # ── Console ─────────────────────────────────────────────────────────────
    ov = report["h1_forex_inverse"]["overall"]
    print(f"[rec] wrote {out_path}")
    print(f"[H1] forex candidates={len(recs)} clusters={ov['original']['n_clusters']}")
    for v in VARIANTS:
        b = ov[v]
        print(f"   {v:11s} gross={b['gross']} net_x1={b['net_x1']} net_x1.5={b['net_x1.5']} "
              f"net_x2={b['net_x2']} hit={b['hit']} ci95(x1.5)={b['net_x1.5_ci95']}")
    h = report["h1_forex_inverse"]["holdout"]
    print(f"[H1] holdout inverse net_x1.5={h['holdout_inverse_net_x1.5']} "
          f"ci95={h['holdout_inverse_ci95']} (dev inverse={h['dev_inverse_net_x1.5']})")
    print("[H1] inverse by pair net_x1.5:",
          {k: v["inverse"]["net_x1.5"] for k, v in report["h1_forex_inverse"]["by_symbol"].items()})
    po, pi = report["phase5_path"]["forex_original"], report["phase5_path"]["forex_inverse"]
    print(f"[P5] orig P(+1<-1)={po['p_plus1_before_minus1']} P(+.5<-.5)={po['p_plus05_before_minus05']} "
          f"| inv P(+1<-1)={pi['p_plus1_before_minus1']} P(+.5<-.5)={pi['p_plus05_before_minus05']}")
    print("[H2] range_breakout:")
    for fam, b in report["h2_range_breakout"].items():
        if b.get("n"):
            print(f"   {fam:11s} n={b['n']} clusters={b['n_clusters']} L/S={b['long_n']}/{b['short_n']} "
                  f"net_x1={b['net_x1']} net_x1.5={b['net_x1.5']} ci95={b['net_x1.5_ci95']} "
                  f"conc={b['top_symbol_concentration']}")


if __name__ == "__main__":
    main()
