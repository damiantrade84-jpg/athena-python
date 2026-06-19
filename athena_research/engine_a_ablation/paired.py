"""Paired Engine A factor attribution (research-only, demo/no-execution).

Fixes the central integrity gap in the unpaired ablation harness: there, every
variant ran its OWN cooldown and SELECTED its own trades, so a "factor lift"
mixed *ranking* value with *selection* value and compared non-comparable trade
sets (current n=741 vs shadow_full n=293 on the same data).

Here we build ONE immutable master candidate ledger. A candidate's direction,
entry, exit and realised net R are fixed by a single scorer-independent rule and
NEVER change between variants. Each variant only assigns a *score* to the same
fixed candidate. That cleanly separates the two questions the audit demands:

  A. RANKING value  — does a factor make the score correlate better with the
     fixed realised net R (Spearman / deciles / top-minus-bottom)?
  B. SELECTION value — with a threshold frozen on development data only, does the
     corrected model pick a better *untouched holdout* subset?

Canonical candidate rule (variant-independent):
  * direction = sign of the PRICE-CORE-only weighted dir_sum (the subsystems we
    are testing play NO part in defining the candidate). FLAT -> no candidate.
  * entry/exit/cost = production V3 machinery (_build_levels / _simulate_exit /
    _cost_r), identical to the live backtest.
  * a single cooldown on the canonical schedule keeps candidates non-overlapping
    per symbol (so within-symbol trades are serially independent); the SAME
    ledger is then scored by every variant.

Carry is split per the audit: carry_feed returns genuine FX policy-rate carry for
forex pairs but an INVERTED 10Y-yield macro proxy for index/commodity/stock/ETF.
We tag each candidate's carry source ("fx" vs "yield") so attribution never
labels a 10-year-yield proxy as carry.

Nothing here writes to live state, Engine B, Engine D, or any executor.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from engine_a_v3.backtest import _cost_r, _simulate_exit
from engine_a_v3.evaluator import _build_levels, _parse_time
from engine_a_v3.profile import baseline_profile
from engine_a_v3.routing import route_specialist

from athena_research.engine_a_ablation.context_builder import build_pit_context
from athena_research.engine_a_ablation.harness import (
    HarnessConfig,
    _COSTS,
    _regime,
)
from athena_research.engine_a_ablation.shadow_scorer import (
    PRICE_FACTORS,
    _RESEARCH_WEIGHTS,
    aggregate,
    compute_components,
)

# Subsystems sourced with leak-free point-in-time data this pass.
SOURCED_SUBSYSTEMS = ("carry", "sentiment")

# Forex families get genuine FX carry; everything else gets the inverted-10Y
# macro proxy. Same context slot ("carry"), different economic meaning -> tagged.
_FX_FAMILIES = {"forex"}


def _variant_weights(base: dict) -> dict[str, dict[str, float]]:
    """Score-only variants over the FIXED ledger. price_only is the canonical
    scorer; the rest add one or both subsystems. (With two sourced subsystems,
    leave-one-out equals the single-add variants, so we keep the minimal set.)"""
    price_only = {k: (base.get(k, 0.0) if k in PRICE_FACTORS else 0.0) for k in base}
    plus_carry = dict(price_only); plus_carry["carry"] = base.get("carry", 0.0)
    plus_sent = dict(price_only); plus_sent["sentiment"] = base.get("sentiment", 0.0)
    full = dict(base)
    return {
        "price_only": price_only,
        "plus_carry": plus_carry,
        "plus_sentiment": plus_sent,
        "full": full,
    }


@dataclass
class Candidate:
    symbol: str
    family: str
    score_group: str
    direction: str
    date: str
    year: str
    regime: str
    raw_result_r: float
    cost_r: float
    outcome: str
    carry_source: str            # "fx" | "yield" | "na"
    carry_state: str
    sentiment_state: str
    scores: dict[str, float] = field(default_factory=dict)   # variant -> confluence
    eff_weights: dict[str, float] = field(default_factory=dict)


def build_ledger_for_symbol(
    pair: dict,
    candles: dict[str, list[dict]],
    *,
    cfg: HarnessConfig,
) -> list[Candidate]:
    """Walk confirmed H4 prefixes once; emit immutable candidates scored by every
    variant. Direction/entry/exit/R are fixed by the canonical price-core rule."""
    route = route_specialist(pair)
    family = route.family
    profile = baseline_profile(route.score_group, cfg.horizon)
    costs = _COSTS.get(family, _COSTS["unknown"])
    base = dict(_RESEARCH_WEIGHTS.get(family, _RESEARCH_WEIGHTS["unknown"]))
    variants = _variant_weights(base)
    price_only_w = variants["price_only"]

    primary = list(candles.get("H4") or [])
    n = len(primary)
    if n < cfg.warmup + 5:
        return []

    tf_rows = {tf: list(rows or []) for tf, rows in candles.items()}
    tf_times = {tf: [str(c.get("time") or c.get("datetime")) for c in rows]
                for tf, rows in tf_rows.items()}
    cap = cfg.prefix_cap
    out: list[Candidate] = []
    cooldown_until = cfg.warmup     # ONE cooldown for the whole ledger

    for index in range(cfg.warmup, n - 1):
        if index < cooldown_until:
            continue
        if cfg.step > 1 and (index - cfg.warmup) % cfg.step != 0:
            continue
        cutoff = str(primary[index].get("time") or primary[index].get("datetime"))
        as_of_date = cutoff[:10]
        d1_n = bisect_right(tf_times.get("D1", []), cutoff)
        h4_n = bisect_right(tf_times.get("H4", []), cutoff)
        if d1_n < cfg.min_d1 or h4_n < 60:
            continue
        prefix = {}
        for tf, rows in tf_rows.items():
            idx = bisect_right(tf_times[tf], cutoff)
            prefix[tf] = rows[max(0, idx - cap): idx]
        h4_prefix = prefix["H4"]

        context = build_pit_context(pair["display"], as_of_date)
        bar = compute_components(route, cfg.horizon, prefix, context=context, profile=profile)

        # Canonical direction from price-core only (subsystems excluded by mask).
        canon = aggregate(bar, include=set(PRICE_FACTORS), weights=price_only_w,
                          threshold=profile.trade_threshold)
        direction = canon.direction
        if direction not in ("LONG", "SHORT"):
            continue

        levels = _build_levels(h4_prefix, direction=direction, level_style=canon.level_style)
        if levels is None or levels.entry_zone is None:
            continue
        entry_bar = primary[index + 1]
        zlo, zhi = float(levels.entry_zone.low), float(levels.entry_zone.high)
        blo, bhi = float(entry_bar["low"]), float(entry_bar["high"])
        if bhi < zlo or blo > zhi:
            continue
        bopen = float(entry_bar["open"])
        entry = (min(max(bopen, zlo), zhi) if direction == "LONG"
                 else max(min(bopen, zhi), zlo))
        sl = float(levels.invalidation)
        tgts = levels.targets
        if not tgts:
            continue
        tp1 = float(tgts[0].price)
        tp2 = float(tgts[1].price) if len(tgts) > 1 else tp1
        if direction == "LONG" and not (sl < entry < tp2):
            continue
        if direction == "SHORT" and not (sl > entry > tp2):
            continue
        if abs(entry - sl) <= 0:
            continue

        max_exit = min(n - 1, index + cfg.max_hold_bars)
        ex = _simulate_exit(primary[index + 1: max_exit + 1], direction=direction,
                            entry=entry, sl=sl, tp1=tp1, tp2=tp2,
                            exit_policy=profile.exit_policy)
        exit_index = index + 1 + ex.exit_offset
        et = _parse_time(entry_bar.get("time") or entry_bar.get("datetime"))
        xt = _parse_time(primary[exit_index].get("time") or primary[exit_index].get("datetime"))
        if et is None or xt is None:
            continue
        cost_r = _cost_r(entry, sl, spread_bps=costs["spread"], commission_bps=costs["commission"],
                         slippage_bps=costs["slippage"], swap_bps_per_day=costs["swap"],
                         entry_time=et, exit_time=xt)

        # Score the SAME fixed candidate with every variant (no re-selection).
        scores = {name: round(aggregate(bar, weights=w,
                                         threshold=profile.trade_threshold).confluence_score, 4)
                  for name, w in variants.items()}

        carry_state = str((context.get("carry") or {}).get("state", "na"))
        sent_state = str((context.get("sentiment") or {}).get("state", "na"))
        carry_source = ("na" if carry_state == "na"
                        else ("fx" if family in _FX_FAMILIES else "yield"))
        ft = canon.factor_table
        eff = {f: ft.get(f, {}).get("effective_weight", 0.0) for f in base}

        out.append(Candidate(
            symbol=pair["display"], family=family, score_group=route.score_group,
            direction=direction, date=cutoff, year=as_of_date[:4], regime=_regime(h4_prefix),
            raw_result_r=round(ex.result_r, 6), cost_r=round(cost_r, 6), outcome=ex.outcome,
            carry_source=carry_source, carry_state=carry_state, sentiment_state=sent_state,
            scores=scores, eff_weights=eff,
        ))
        cooldown_until = exit_index + 1

    return out
