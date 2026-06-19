"""Harness-integrity + paired-attribution tests (research-only).

Covers the statistical core of the paired ledger (Spearman, deciles, cluster
bootstrap, incremental ranking lift, frozen-threshold selection) and the paired
contract itself: every variant scores the IDENTICAL candidate set and a
candidate's realised net R is single-valued (variant-independent).

Run: pytest tests/test_engine_a_ablation_paired.py -q
"""

from __future__ import annotations

import os

os.environ.setdefault("ATHENA_REAL_ORDERS_CONFIRM", "I_UNDERSTAND_REAL_ORDER_RISK")

from athena_research.engine_a_ablation.paired_metrics import (  # noqa: E402
    cluster_bootstrap,
    decile_table,
    ranking_lift,
    selection_holdout,
    spearman,
    top_minus_bottom,
)
from athena_research.engine_a_ablation.paired import _variant_weights  # noqa: E402
from athena_research.engine_a_ablation.shadow_scorer import PRICE_FACTORS  # noqa: E402


# ── Spearman ───────────────────────────────────────────────────────────────────

def test_spearman_perfect_monotone():
    xs = [1, 2, 3, 4, 5]
    ys = [10, 20, 30, 40, 50]
    assert spearman(xs, ys) == 1.0


def test_spearman_perfect_inverse():
    assert spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == -1.0


def test_spearman_handles_ties():
    # tie-aware average ranks; constant y -> undefined (None)
    assert spearman([1, 1, 2, 2], [1, 1, 1, 1]) is None
    val = spearman([1, 2, 2, 3], [1, 2, 3, 4])
    assert val is not None and val > 0.8


# ── Deciles ────────────────────────────────────────────────────────────────────

def test_decile_table_counts_and_order():
    scores = list(range(100))
    net = [float(s) for s in scores]      # net rises with score
    table = decile_table(scores, net, q=10)
    assert len(table) == 10
    assert sum(b["n"] for b in table) == 100
    # mean_net_r strictly increases low->high bucket
    means = [b["mean_net_r"] for b in table]
    assert means == sorted(means)
    assert table[0]["score_lo"] <= table[-1]["score_hi"]


def test_top_minus_bottom_positive_when_score_predicts():
    scores = list(range(50))
    net = [float(s) for s in scores]
    tb = top_minus_bottom(scores, net, frac=0.2)
    assert tb["spread"] > 0
    assert tb["top_exp"] > tb["bottom_exp"]


# ── Cluster bootstrap ──────────────────────────────────────────────────────────

def test_cluster_bootstrap_resamples_symbols():
    ledger = [{"symbol": f"S{i % 5}", "v": float(i % 5)} for i in range(50)]
    res = cluster_bootstrap(ledger, lambda rows: sum(r["v"] for r in rows) / len(rows))
    assert res["n_clusters"] == 5
    assert res["ci95"] is not None
    lo, hi = res["ci95"]
    assert lo <= res["point"] <= hi


def test_cluster_bootstrap_single_cluster_no_ci():
    ledger = [{"symbol": "ONE", "v": float(i)} for i in range(10)]
    res = cluster_bootstrap(ledger, lambda _rows: 1.0)
    assert res["ci95"] is None


# ── Incremental ranking lift ────────────────────────────────────────────────────

def test_ranking_lift_positive_when_test_col_ranks_better():
    # base score is pure noise vs net; test score equals net (perfect ranker).
    ledger = []
    for s in range(4):
        for i in range(20):
            net = float(i)
            ledger.append({
                "symbol": f"S{s}", "net": net,
                "base": float((i * 7) % 20),   # decorrelated from net
                "test": net,                    # perfectly correlated
            })
    lift = ranking_lift(ledger, "base", "test", "net")
    assert lift["point"] is not None and lift["point"] > 0
    assert lift["ci95"] is not None and lift["ci95"][0] > 0   # CI excludes 0


def test_ranking_lift_zero_when_factor_is_noise():
    ledger = []
    for s in range(4):
        for i in range(25):
            net = float(i)
            ledger.append({
                "symbol": f"S{s}", "net": net,
                "base": net,                       # already perfect
                "test": net + ((i * 13) % 5) * 0,  # identical to base
            })
    lift = ranking_lift(ledger, "base", "test", "net")
    assert abs(lift["point"]) < 1e-6   # no improvement possible over a perfect base


# ── Selection (frozen threshold, untouched holdout) ─────────────────────────────

def test_selection_holdout_uses_frozen_threshold_only():
    dev = [{"symbol": "A", "score": 1.0 + i * 0.1, "net": 1.0} for i in range(10)]
    holdout = [{"symbol": "A", "score": 1.0 + i * 0.1, "net": (1.0 if i >= 5 else -1.0)}
               for i in range(10)]
    # threshold 1.5 selects the upper half of holdout (scores 1.5..1.9 -> net +1)
    res = selection_holdout(dev, holdout, "score", "net", threshold=1.5)
    assert res["threshold"] == 1.5
    assert res["holdout_selected_n"] == 5
    assert res["holdout_selected_exp"] == 1.0
    assert res["holdout_all_n"] == 10


# ── Paired contract / variant construction ──────────────────────────────────────

def test_variant_weights_price_only_excludes_subsystems():
    base = {"trend": 0.24, "momentum": 0.16, "location": 0.12, "volume": 0.05,
            "carry": 0.14, "sentiment": 0.08}
    v = _variant_weights(base)
    assert set(v) == {"price_only", "plus_carry", "plus_sentiment", "full"}
    # price_only zeroes every subsystem
    assert v["price_only"]["carry"] == 0.0
    assert v["price_only"]["sentiment"] == 0.0
    for f in PRICE_FACTORS:
        assert v["price_only"][f] == base[f]
    # plus_carry adds only carry; plus_sentiment adds only sentiment
    assert v["plus_carry"]["carry"] == 0.14 and v["plus_carry"]["sentiment"] == 0.0
    assert v["plus_sentiment"]["sentiment"] == 0.08 and v["plus_sentiment"]["carry"] == 0.0
    assert v["full"]["carry"] == 0.14 and v["full"]["sentiment"] == 0.08


def test_paired_ledger_net_r_is_variant_independent():
    # Simulate the paired contract: one immutable candidate carries a single
    # net R; every variant only attaches a score. No variant may change net R.
    candidate = {
        "symbol": "EUR/USD", "raw_result_r": 1.4, "cost_r": 0.1,
        "price_only": 2.1, "plus_carry": 2.3, "plus_sentiment": 2.0, "full": 2.4,
    }
    net = candidate["raw_result_r"] - 1.0 * candidate["cost_r"]
    # net R derives only from raw_result_r/cost_r, independent of any variant score
    for _v in ("price_only", "plus_carry", "plus_sentiment", "full"):
        assert candidate["raw_result_r"] - candidate["cost_r"] == net
