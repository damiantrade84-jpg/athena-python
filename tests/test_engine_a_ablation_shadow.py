"""Plumbing regression tests for the research-only Engine A ablation shadow
scorer (2026-06-19). Proves the wiring the production scorer is missing, and the
HYBRID denominator policy's anti-inflation guarantee.

These test the AGGREGATION CONTRACT directly with synthetic components so the
math is deterministic (no candle fixtures needed). The price core is proven
identical to production by import identity.
"""

from __future__ import annotations

import engine_a_v3.quant_scorer as qs
from engine_a_v3.quant_scorer import Component

from athena_research.engine_a_ablation import context_builder as cb
from athena_research.engine_a_ablation.shadow_scorer import (
    ALL_FACTORS,
    PRICE_FACTORS,
    ST_AVAILABLE,
    ST_NA,
    ST_NEUTRAL,
    ST_UNAVAILABLE,
    BarComponents,
    aggregate,
)

_FOREX_W = {"trend": .24, "momentum": .16, "location": .12, "volume": .05,
            "intermarket": 0.0, "carry": .14, "sentiment": .08, "macro": 0.0,
            "microstructure": 0.0}


class _Prof:
    trade_threshold = 2.0
    direction_deadband = 0.05


def _mk(family="forex", price=None, subs=None, vol_mult=1.0):
    """price: {name:(signal,quality)} -> available; subs: {name:(signal,quality,state)}."""
    price = price or {}
    subs = subs or {}
    comps, states = {}, {}
    for n in PRICE_FACTORS:
        sig, q = price.get(n, (0.0, 0.0))
        comps[n] = Component(sig, q, available=(n in price))
        states[n] = ST_AVAILABLE if n in price else ST_UNAVAILABLE
    for n in ("intermarket", "carry", "sentiment", "macro", "microstructure"):
        if n in subs:
            sig, q, st = subs[n]
            comps[n] = Component(sig, q, available=st in (ST_AVAILABLE, ST_NEUTRAL))
            states[n] = st
        else:
            comps[n] = Component(0.0, 0.0, available=False)
            states[n] = ST_UNAVAILABLE
    return BarComponents("forex" if family == "forex" else family, comps, states,
                         "trend", vol_mult, 0.05, _Prof())


# ── 1 & 2: every configured subsystem factor CAN affect direction ────────────
def test_carry_can_flip_direction():
    flat_price = {n: (0.0, 0.5) for n in PRICE_FACTORS}  # available, zero directional
    long = aggregate(_mk(price=flat_price, subs={"carry": (0.9, 0.9, ST_AVAILABLE)}),
                     weights=_FOREX_W)
    short = aggregate(_mk(price=flat_price, subs={"carry": (-0.9, 0.9, ST_AVAILABLE)}),
                      weights=_FOREX_W)
    assert long.direction == "LONG"
    assert short.direction == "SHORT"


def test_sentiment_can_flip_direction():
    flat_price = {n: (0.0, 0.5) for n in PRICE_FACTORS}
    long = aggregate(_mk(price=flat_price, subs={"sentiment": (0.9, 0.9, ST_AVAILABLE)}),
                     weights=_FOREX_W)
    short = aggregate(_mk(price=flat_price, subs={"sentiment": (-0.9, 0.9, ST_AVAILABLE)}),
                      weights=_FOREX_W)
    assert long.direction == "LONG"
    assert short.direction == "SHORT"


# ── 3: a configured subsystem factor CAN affect confluence ───────────────────
def test_carry_affects_confluence():
    price = {"trend": (1.0, 0.8)}
    with_carry = aggregate(_mk(price=price, subs={"carry": (1.0, 0.9, ST_AVAILABLE)}),
                           weights=_FOREX_W)
    without = aggregate(_mk(price=price, subs={"carry": (0.0, 0.0, ST_UNAVAILABLE)}),
                        weights=_FOREX_W)
    assert with_carry.direction == "LONG"
    assert with_carry.confluence_score > without.confluence_score


# ── 4: missing subsystem cannot inflate the technicals (the core fix) ─────────
def test_missing_subsystem_does_not_inflate_technicals():
    w = {"trend": .24, "carry": .14, "momentum": 0.0, "location": 0.0, "volume": 0.0,
         "intermarket": 0.0, "sentiment": 0.0, "macro": 0.0, "microstructure": 0.0}
    price = {"trend": (1.0, 0.8)}
    # carry applicable but UNAVAILABLE -> stays in denominator (reduces confluence)
    unavail = aggregate(_mk(price=price, subs={"carry": (0.0, 0.0, ST_UNAVAILABLE)}), weights=w)
    # carry structurally removed (weight 0 -> na) -> trend renormalizes to full weight
    w_na = dict(w, carry=0.0)
    na = aggregate(_mk(price=price), weights=w_na)
    te_unavail = unavail.factor_table["trend"]["effective_weight"]
    te_na = na.factor_table["trend"]["effective_weight"]
    assert te_na == 1.0                       # only trend in denominator
    assert te_unavail < te_na                 # carry's denom weight suppresses trend
    assert unavail.confluence_score < na.confluence_score
    assert unavail.factor_table["carry"]["state"] == ST_UNAVAILABLE
    assert unavail.factor_table["carry"]["in_denominator"] is True


# ── 5: effective weights of in-denominator factors sum to 1.0 ────────────────
def test_effective_weights_sum_to_one():
    price = {n: (0.5, 0.6) for n in PRICE_FACTORS}
    res = aggregate(_mk(price=price, subs={"carry": (0.4, 0.6, ST_AVAILABLE),
                                           "sentiment": (0.3, 0.5, ST_AVAILABLE)}),
                    weights=_FOREX_W)
    eff = sum(row["effective_weight"] for row in res.factor_table.values()
              if row["in_denominator"])
    assert abs(eff - 1.0) < 1e-6


# ── 6: no factor active-in-diagnostics-yet-contributing-nothing ──────────────
def test_no_active_factor_contributes_nothing():
    price = {"trend": (1.0, 0.7), "momentum": (1.0, 0.6)}
    res = aggregate(_mk(price=price, subs={"carry": (1.0, 0.8, ST_AVAILABLE)}),
                    weights=_FOREX_W)
    for name, row in res.factor_table.items():
        if row["aligned"] and row["quality"] > 0:
            assert row["contribution"] > 0, f"{name} aligned but zero contribution"
        if row["contribution"] > 0:
            assert row["in_denominator"] is True


# ── 7: structurally-not-applicable factors carry no weight / no penalty ───────
def test_structural_na_no_penalty():
    res = aggregate(_mk(price={n: (0.5, 0.6) for n in PRICE_FACTORS}), weights=_FOREX_W)
    for name in ("intermarket", "macro", "microstructure"):
        assert res.factor_table[name]["state"] == ST_NA
        assert res.factor_table[name]["effective_weight"] == 0.0
        assert res.factor_table[name]["in_denominator"] is False


# ── 8: price-core parity with production (import identity) ────────────────────
def test_price_core_is_production_verbatim():
    import athena_research.engine_a_ablation.shadow_scorer as ss
    assert ss._trend_component is qs._trend_component
    assert ss._momentum_component is qs._momentum_component
    assert ss._location_component is qs._location_component
    assert ss._volume_component is qs._volume_component
    assert ss._volatility_mult is qs._volatility_mult


# ── 9: forex orientation is base-minus-quote at the feed source ──────────────
def test_carry_formula_orientation():
    from carry_feed import _PAIR_CARRY_FORMULA
    assert _PAIR_CARRY_FORMULA["EUR/USD"] == [(1.0, "EUR"), (-1.0, "USD")]
    assert _PAIR_CARRY_FORMULA["USD/JPY"] == [(1.0, "USD"), (-1.0, "JPY")]
    # cross combines both legs
    assert _PAIR_CARRY_FORMULA["EUR/GBP"] == [(1.0, "EUR"), (-1.0, "GBP")]


# ── 10: z->entry units bounded; neutral & unavailable distinguished ──────────
def test_z_to_entry_units_and_states():
    big = cb._z_to_entry(10.0)
    assert -1.0 <= big["signal"] <= 1.0 and 0.0 <= big["quality"] <= 1.0
    assert big["state"] == cb.ST_AVAILABLE
    neutral = cb._z_to_entry(0.0)
    assert neutral["state"] == cb.ST_NEUTRAL and neutral["signal"] == 0.0
    assert cb._z_to_entry(None)["state"] == cb.ST_UNAVAILABLE
    # sign preserved (orientation): +z -> long-side signal, -z -> short-side
    assert cb._z_to_entry(1.0)["signal"] > 0
    assert cb._z_to_entry(-1.0)["signal"] < 0


# ── 11: stale/unavailable subsystem excluded from numerator but kept in denom ─
def test_unavailable_subsystem_zero_contribution_but_in_denominator():
    price = {"trend": (1.0, 0.8)}
    res = aggregate(_mk(price=price, subs={"carry": (1.0, 0.9, ST_UNAVAILABLE)}),
                    weights=_FOREX_W)
    assert res.factor_table["carry"]["contribution"] == 0.0
    assert res.factor_table["carry"]["aligned"] is False
    assert res.factor_table["carry"]["in_denominator"] is True


# ── 12: ablation mask (only_<factor>) restricts the active numerator ─────────
def test_only_factor_weights_isolate_one_factor():
    price = {"trend": (1.0, 0.8), "momentum": (1.0, 0.8)}
    only_carry_w = {k: (_FOREX_W[k] if k == "carry" else 0.0) for k in ALL_FACTORS}
    res = aggregate(_mk(price=price, subs={"carry": (1.0, 0.9, ST_AVAILABLE)}),
                    weights=only_carry_w)
    # direction comes from carry alone; trend/momentum have weight 0 -> na
    assert res.direction == "LONG"
    assert res.factor_table["trend"]["state"] == ST_NA
    assert res.factor_table["carry"]["in_denominator"] is True
