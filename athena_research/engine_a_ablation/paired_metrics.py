"""Ranking + selection metrics for the PAIRED factor ledger (research-only).

All functions take the immutable candidate ledger (a list of dicts with a fixed
`net_r` plus one score per variant) so every variant is measured on the IDENTICAL
candidate set. CIs use a CLUSTER bootstrap that resamples whole symbols, which
respects both serial dependence within a symbol and cross-sectional clustering
across symbols on overlapping dates (the per-trade IID bootstrap in metrics.py
understates uncertainty for exactly this reason).
"""

from __future__ import annotations

import random
from statistics import fmean
from typing import Sequence


def _rank(xs: Sequence[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    rx, ry = _rank(xs), _rank(ys)
    mx, my = fmean(rx), fmean(ry)
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((rx[i] - mx) ** 2 for i in range(n))
    dy = sum((ry[i] - my) ** 2 for i in range(n))
    den = (dx * dy) ** 0.5
    return round(num / den, 4) if den > 0 else None


def decile_table(scores: Sequence[float], net: Sequence[float], *, q: int = 10) -> list[dict]:
    """Mean net R + count per score quantile (low score -> high score)."""
    pairs = sorted(zip(scores, net), key=lambda p: p[0])
    n = len(pairs)
    if n < q:
        q = max(2, n)
    out = []
    for b in range(q):
        lo = b * n // q
        hi = (b + 1) * n // q
        chunk = pairs[lo:hi]
        if not chunk:
            continue
        out.append({
            "bucket": b,
            "n": len(chunk),
            "score_lo": round(chunk[0][0], 4),
            "score_hi": round(chunk[-1][0], 4),
            "mean_net_r": round(fmean([c[1] for c in chunk]), 4),
        })
    return out


def top_minus_bottom(scores: Sequence[float], net: Sequence[float], *, frac: float = 0.2) -> dict:
    pairs = sorted(zip(scores, net), key=lambda p: p[0])
    n = len(pairs)
    k = max(1, int(n * frac))
    bot = [p[1] for p in pairs[:k]]
    top = [p[1] for p in pairs[-k:]]
    return {"k": k, "top_exp": round(fmean(top), 4), "bottom_exp": round(fmean(bot), 4),
            "spread": round(fmean(top) - fmean(bot), 4)}


def cluster_bootstrap(
    ledger: Sequence[dict], stat, *, n_boot: int = 1000, seed: int = 13, alpha: float = 0.05,
) -> dict:
    """Bootstrap a statistic by resampling whole symbols (clusters) with
    replacement. `stat(rows) -> float | None`. Returns point + percentile CI."""
    by_sym: dict[str, list[dict]] = {}
    for r in ledger:
        by_sym.setdefault(r["symbol"], []).append(r)
    syms = list(by_sym)
    point = stat(list(ledger))
    if point is None or len(syms) < 2:
        return {"point": point, "ci95": None, "n_clusters": len(syms)}
    rng = random.Random(seed)
    vals = []
    for _ in range(n_boot):
        pick = [by_sym[rng.choice(syms)] for _ in syms]
        rows = [r for grp in pick for r in grp]
        v = stat(rows)
        if v is not None:
            vals.append(v)
    if not vals:
        return {"point": round(point, 4), "ci95": None, "n_clusters": len(syms)}
    vals.sort()
    lo = vals[int(alpha / 2 * len(vals))]
    hi = vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]
    return {"point": round(point, 4), "ci95": (round(lo, 4), round(hi, 4)),
            "n_clusters": len(syms)}


def ranking_lift(ledger: Sequence[dict], base_col: str, test_col: str, net_key: str) -> dict:
    """Cluster-bootstrap CI on Spearman(test) - Spearman(base) over the SAME rows.
    Positive & CI excluding 0 => the factor genuinely improves ranking."""
    def _diff(rows):
        if len(rows) < 5:
            return None
        net = [r[net_key] for r in rows]
        sb = spearman([r[base_col] for r in rows], net)
        st = spearman([r[test_col] for r in rows], net)
        if sb is None or st is None:
            return None
        return st - sb
    return cluster_bootstrap(ledger, _diff)


def selection_holdout(
    dev: Sequence[dict], holdout: Sequence[dict], col: str, net_key: str, *, threshold: float,
) -> dict:
    """Question B. Threshold is frozen on dev (passed in); we only REPORT the
    holdout subset the variant selects. No optimisation touches the holdout."""
    def _sel_exp(rows):
        sel = [r[net_key] for r in rows if r[col] >= threshold]
        return fmean(sel) if sel else None
    dev_sel = [r for r in dev if r[col] >= threshold]
    hold_rows = list(holdout)
    hold_sel = [r for r in hold_rows if r[col] >= threshold]
    boot = cluster_bootstrap(hold_rows, lambda rows: _sel_exp(rows))
    return {
        "threshold": threshold,
        "dev_selected_n": len(dev_sel),
        "holdout_selected_n": len(hold_sel),
        "holdout_selected_exp": round(fmean([r[net_key] for r in hold_sel]), 4) if hold_sel else None,
        "holdout_all_n": len(hold_rows),
        "holdout_all_exp": round(fmean([r[net_key] for r in hold_rows]), 4) if hold_rows else None,
        "holdout_selected_exp_ci95": boot.get("ci95"),
    }
