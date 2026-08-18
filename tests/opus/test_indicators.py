"""Behavioural tests for the OPUS indicator set.

These assert that each indicator DETECTS the structure it claims to detect and
with the correct sign, not merely that it executes.
"""

from __future__ import annotations

import numpy as np
import pytest

from opus.core.stats import (
    fractal_efficiency, jump_ratio, robust_z, squash, swing_pivots,
    weighted_dispersion, yang_zhang_sigma,
)
from opus.indicators import (
    absorption, flow, kinetics, liquidity, regime, temporal, value, voids,
)
from opus.indicators.base import MarketContext
from opus.types import Candles, Regime
from tests.opus import synth


def build_ctx(ctxt, struct, trig, asset_class="forex") -> MarketContext:
    return MarketContext.build(
        symbol="TEST", display="TEST", asset_class=asset_class,
        context=ctxt, structure=struct, trigger=trig,
        quote=synth.quote_for(trig), now=float(trig.ts[-1]) + 300.0,
    )


def noise_ctx(seed: int = 11, drift: float = 0.0) -> MarketContext:
    return build_ctx(*synth.coherent_ladder(seed=seed, drift=drift))


def mirror(c: Candles) -> Candles:
    """Reflect price geometry about a constant: highs become lows."""
    k = 2.0 * float(np.mean(c.close))
    return Candles(
        symbol=c.symbol, timeframe=c.timeframe, ts=c.ts,
        open=k - c.open, high=k - c.low, low=k - c.high, close=k - c.close,
        volume=c.volume, source=c.source,
        volume_is_tick_count=c.volume_is_tick_count,
    )


# --------------------------------------------------------------------------
# core statistics
# --------------------------------------------------------------------------

def test_fractal_efficiency_bounds():
    straight = np.linspace(0.0, 10.0, 60)
    assert fractal_efficiency(straight, 50) == pytest.approx(1.0)
    zigzag = np.array([0.0, 1.0] * 40)
    assert fractal_efficiency(zigzag, 50) < 0.05


def test_jump_ratio_detects_discontinuity():
    rng = np.random.default_rng(3)
    smooth = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, 300)))
    jumpy = smooth.copy()
    jumpy[150:] *= 1.05          # one discrete repricing
    assert jump_ratio(jumpy, 120) > jump_ratio(smooth, 120)


def test_yang_zhang_scales_with_volatility():
    quiet = synth.random_walk(n=200, vol=0.0005, seed=2)
    loud = synth.random_walk(n=200, vol=0.0040, seed=2)
    s_q = yang_zhang_sigma(quiet.open, quiet.high, quiet.low, quiet.close, 60)
    s_l = yang_zhang_sigma(loud.open, loud.high, loud.low, loud.close, 60)
    assert s_l > s_q * 3.0


def test_swing_pivots_keep_equal_highs():
    """Equal highs are the richest liquidity structure, not a degenerate case."""
    h = np.array([1.0, 2.0, 5.0, 2.0, 1.0, 2.0, 5.0, 2.0, 1.0])
    lo = np.array([1.0, 0.0, 3.0, 0.0, -1.0, 0.0, 3.0, 0.0, 1.0])
    highs, lows = swing_pivots(h, lo, 2)
    assert highs.tolist() == [2, 6]
    assert lows.tolist() == [4]


def test_swing_pivots_reject_monotone_ramp():
    ramp = np.arange(1.0, 20.0)
    highs, lows = swing_pivots(ramp, ramp, 2)
    assert highs.size == 0 and lows.size == 0


def test_robust_z_resists_outliers_better_than_mean_std():
    """MAD scaling must degrade less than mean/std when the baseline is dirty.

    Volume and range are heavy-tailed, so the baseline routinely contains the
    same spikes the z-score is meant to detect.
    """
    def mean_std_z(values):
        base = values[:-1]
        sd = float(np.std(base, ddof=1))
        return (values[-1] - float(np.mean(base))) / sd if sd > 0 else 0.0

    clean = np.concatenate([np.random.default_rng(1).normal(10, 1, 60), [20.0]])
    dirty = clean.copy()
    dirty[5], dirty[17], dirty[33] = 90.0, 85.0, 95.0   # three contaminants

    robust_drop = abs(robust_z(clean, 60) - robust_z(dirty, 60))
    naive_drop = abs(mean_std_z(clean) - mean_std_z(dirty))
    assert robust_drop < naive_drop, "MAD scaling should be the more stable one"
    assert robust_z(dirty, 60) > 3.0, "signal must survive a contaminated baseline"


def test_squash_is_bounded_and_odd():
    assert -1.0 < squash(1e6, 1.0) <= 1.0
    assert squash(2.0, 1.0) == pytest.approx(-squash(-2.0, 1.0))
    assert squash(float("nan"), 1.0) == 0.0


def test_weighted_dispersion_zero_on_agreement():
    v = np.array([0.5, 0.5, 0.5])
    w = np.array([1.0, 2.0, 3.0])
    assert weighted_dispersion(v, w) == pytest.approx(0.0)
    assert weighted_dispersion(np.array([1.0, -1.0]), np.array([1.0, 1.0])) > 0.9


# --------------------------------------------------------------------------
# LDI - the sweep / hold distinction is the engine's core claim
# --------------------------------------------------------------------------

def _clean_ctx(side: str, reclaim: bool) -> MarketContext:
    struct = synth.clean_pool_series(side=side, reclaim=reclaim)
    trig = synth.clean_pool_series(side=side, reclaim=reclaim, timeframe="M5", step=300)
    return build_ctx(synth.resample(struct, 4, "H1"), struct, trig)


@pytest.mark.parametrize(
    "side,reclaim,expect_kind,expect_dir,label",
    [
        ("low", True, "sweep", +1, "low swept and reclaimed -> bullish"),
        ("low", False, "hold", -1, "low broken and held -> bearish"),
        ("high", True, "sweep", -1, "high swept and rejected -> bearish"),
        ("high", False, "hold", +1, "high broken and held -> bullish"),
    ],
)
def test_ldi_classifies_pool_interactions(side, reclaim, expect_kind, expect_dir, label):
    """The engine's core claim: a rejected poke and a genuine break are
    opposite trades, and retention is what separates them."""
    ctx = _clean_ctx(side, reclaim)
    pools = liquidity.detect_pools(ctx)
    assert pools, f"{label}: no pool detected in a deterministic fixture"

    events = liquidity.detect_events(ctx, pools)
    final = [e for e in events if e.index == len(ctx.structure) - 1]
    assert final, f"{label}: final-bar interaction produced no event"

    ev = final[0]
    assert ev.kind == expect_kind, f"{label}: classified {ev.kind}"
    assert ev.direction == expect_dir, f"{label}: direction {ev.direction}"


@pytest.mark.parametrize(
    "side,reclaim,expect_sign,label",
    [
        ("low", True, +1, "low swept and reclaimed -> bullish"),
        ("low", False, -1, "low broken and held -> bearish"),
        ("high", True, -1, "high swept and rejected -> bearish"),
        ("high", False, +1, "high broken and held -> bullish"),
    ],
)
def test_ldi_aggregate_sign_on_clean_series(side, reclaim, expect_sign, label):
    ctx = _clean_ctx(side, reclaim)
    reading = liquidity.compute(ctx)
    assert np.sign(reading.value) == expect_sign, (
        f"{label}: got {reading.value:+.4f} detail={reading.detail}"
    )
    assert abs(reading.value) > 0.15, f"{label}: reading too weak to act on"


def test_ldi_event_strength_is_bounded():
    """A one-tick poke closing far away must not dominate the indicator.

    Retention is unbounded below; without a clamp a single wick contributes
    many times a normal event's strength and saturates the reading.
    """
    ctx = noise_ctx(seed=4)
    pools = liquidity.detect_pools(ctx)
    events = liquidity.detect_events(ctx, pools)
    if events:
        masses = [p.mass for p in pools]
        assert max(e.strength for e in events) <= max(masses) * 1.01


def test_pools_include_equal_high_clusters():
    ctx = noise_ctx(seed=7)
    pools = liquidity.detect_pools(ctx)
    assert pools, "no liquidity pools detected on a normal series"
    assert any(p.origin.startswith("session_") for p in pools)


# --------------------------------------------------------------------------
# structural symmetry: no long/short bias anywhere
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "module,name,tol",
    [
        (liquidity, "LDI", 0.05),
        (absorption, "ARC", 0.05),
        (voids, "VFI", 0.15),   # discrete detector: threshold ties under reflection
        (kinetics, "KDI", 0.05),
        (flow, "OFP", 0.05),
    ],
)
def test_indicators_are_antisymmetric_under_price_reflection(module, name, tol):
    """Reflecting the chart must negate the reading.

    This is the definitive test for directional bias, and it is independent of
    sample size in a way that "mean over N random seeds" is not.
    """
    residuals = []
    for seed in range(1, 9):
        ctxt, struct, trig = synth.coherent_ladder(seed=seed)
        up = build_ctx(ctxt, struct, trig)
        down = build_ctx(mirror(ctxt), mirror(struct), mirror(trig))
        residuals.append(module.compute(up).value + module.compute(down).value)
    worst = float(np.max(np.abs(residuals)))
    assert worst < tol, f"{name} is not antisymmetric: worst residual {worst:.4f}"


def test_indicators_do_not_saturate_on_noise():
    """Pure noise must not routinely produce maximum-conviction readings."""
    for module in (liquidity, absorption, voids, kinetics, flow):
        vals = [abs(module.compute(noise_ctx(seed=s)).value) for s in range(1, 13)]
        saturated = sum(1 for v in vals if v > 0.95)
        assert saturated <= 2, f"{module.__name__} saturated {saturated}/12 on noise"


# --------------------------------------------------------------------------
# VFI
# --------------------------------------------------------------------------

def test_vfi_detects_embedded_void_direction():
    ctxt, struct, trig = synth.coherent_ladder(seed=11)
    bull = build_ctx(ctxt, synth.with_void(struct, direction=1), trig)
    bear = build_ctx(ctxt, synth.with_void(struct, direction=-1), trig)
    assert voids.compute(bull).value > voids.compute(bear).value


def test_void_fill_erosion_reduces_mass():
    ctx = noise_ctx(seed=6)
    found = voids.detect_voids(ctx)
    if found:
        for v in found:
            assert 0.0 <= v.fill <= 1.0
            assert v.residual_mass <= v.size_sigma + 1e-9


def test_find_shelf_returns_void_on_correct_side():
    ctx = noise_ctx(seed=6)
    price = ctx.last_price
    up = voids.find_shelf(ctx, 1)
    if up is not None:
        assert up.direction == 1
        assert up.bottom <= price + 1e-9


# --------------------------------------------------------------------------
# ARC
# --------------------------------------------------------------------------

def test_arc_reads_rejection_direction():
    ctxt, struct, trig = synth.coherent_ladder(seed=11)
    bull = build_ctx(ctxt, synth.with_absorption(struct, bullish=True), trig)
    bear = build_ctx(ctxt, synth.with_absorption(struct, bullish=False), trig)
    assert absorption.compute(bull).value > absorption.compute(bear).value


def test_arc_degrades_without_volume():
    ctxt, struct, trig = synth.coherent_ladder(seed=11)
    novol = Candles(
        symbol=struct.symbol, timeframe=struct.timeframe, ts=struct.ts,
        open=struct.open, high=struct.high, low=struct.low, close=struct.close,
        volume=np.zeros(len(struct)), source="synth",
    )
    reading = absorption.compute(build_ctx(ctxt, novol, trig))
    assert reading.detail.get("hasVolume") is False
    assert reading.confidence < absorption.compute(build_ctx(ctxt, struct, trig)).confidence


# --------------------------------------------------------------------------
# KDI
# --------------------------------------------------------------------------

def test_kdi_separates_trend_from_chop():
    trend_trig = synth.random_walk(
        n=1500, vol=0.0004, drift=0.0012, seed=5, timeframe="M5", step=300
    )
    trend_struct = synth.resample(trend_trig, 3, "M15")
    trend = build_ctx(synth.resample(trend_struct, 4, "H1"), trend_struct, trend_trig)

    chop_struct = synth.choppy(n=500)
    chop_trig = synth.choppy(n=1500, timeframe="M5", step=300)
    chop = build_ctx(synth.resample(chop_struct, 4, "H1"), chop_struct, chop_trig)

    k_trend = kinetics.compute(trend)
    k_chop = kinetics.compute(chop)
    assert k_trend.detail["efficiency"] > k_chop.detail["efficiency"] * 2
    assert k_trend.value > 0.3
    assert abs(k_chop.value) < 0.3


# --------------------------------------------------------------------------
# SAV
# --------------------------------------------------------------------------

def test_sav_fails_closed_when_feeds_disagree():
    """A quote far from session value means bad data, not a huge fade."""
    ctxt, struct, trig = synth.coherent_ladder(seed=11)
    bad_quote = synth.quote_for(trig)
    broken = MarketContext.build(
        symbol="TEST", display="TEST", asset_class="forex",
        context=ctxt, structure=struct, trigger=trig,
        quote=type(bad_quote)(
            symbol=bad_quote.symbol,
            bid=bad_quote.bid * 1.5, ask=bad_quote.ask * 1.5,
            ts=bad_quote.ts, source="wrong_venue",
        ),
        now=float(trig.ts[-1]) + 300.0,
    )
    reading = value.compute(broken)
    assert reading.value == 0.0
    assert reading.confidence == 0.0
    assert reading.detail["reason"] == "stretch_out_of_range"


def test_sav_falls_back_to_rolling_anchor_at_session_open():
    """SAV must stay defined in the first minutes of a session."""
    ctx = noise_ctx(seed=11)
    val = value.session_value(ctx)
    assert val is not None
    assert val["anchor"] in {"session", "rolling"}


def test_sav_rotation_is_a_fraction():
    ctx = noise_ctx(seed=9)
    val = value.session_value(ctx)
    assert val is not None
    assert 0.0 <= val["rotation"] <= 1.0


# --------------------------------------------------------------------------
# OFP
# --------------------------------------------------------------------------

def test_ofp_is_stationary_not_a_cumulative_level():
    """Z-scoring a cumulative sum against its own trailing mean reads ~0.8 on
    pure noise. The windowed-flow formulation must not."""
    vals = [abs(flow.compute(noise_ctx(seed=s)).value) for s in range(1, 17)]
    assert float(np.mean(vals)) < 0.55


def test_ofp_book_imbalance_signs():
    heavy_bid = {"bids": [[100.0, 90.0]], "asks": [[100.1, 10.0]]}
    heavy_ask = {"bids": [[100.0, 10.0]], "asks": [[100.1, 90.0]]}
    assert flow.book_imbalance(heavy_bid, 10) > 0.5
    assert flow.book_imbalance(heavy_ask, 10) < -0.5
    assert flow.book_imbalance(None, 10) is None
    assert flow.book_imbalance({"bids": [], "asks": []}, 10) is None


def test_ofp_uses_book_when_supplied():
    ctx = noise_ctx(seed=3)
    plain = flow.compute(ctx)
    with_book = flow.compute(ctx, book={"bids": [[1.0, 100.0]], "asks": [[1.1, 1.0]]})
    assert plain.detail["source"] == "bar"
    assert with_book.detail["source"] == "book+bar"
    assert with_book.value > plain.value


# --------------------------------------------------------------------------
# TCI / RQS
# --------------------------------------------------------------------------

def test_tci_shrinks_thin_buckets_toward_neutral():
    """A bucket with a handful of samples must not earn a large multiplier."""
    ctx = noise_ctx(seed=11)
    state = temporal.compute(ctx)
    assert 0.3 <= state.gain <= 1.9
    if state.samples < 30:
        assert abs(state.continuation - 0.5) < 0.25


def test_tci_never_returns_a_direction():
    state = temporal.compute(noise_ctx(seed=2))
    assert state.gain > 0.0     # a gain, never a signed signal


def test_rqs_classifies_trend_and_chop_differently():
    trend_trig = synth.random_walk(
        n=1500, vol=0.0004, drift=0.0014, seed=5, timeframe="M5", step=300
    )
    trend_struct = synth.resample(trend_trig, 3, "M15")
    trend = build_ctx(synth.resample(trend_struct, 4, "H1"), trend_struct, trend_trig)
    state = regime.compute(trend)
    assert state.regime in {Regime.TREND_DRIVE, Regime.COILED}
    assert state.imbalanced is True


def test_rqs_always_returns_a_quadrant():
    for seed in range(1, 9):
        state = regime.compute(noise_ctx(seed=seed))
        assert isinstance(state.regime, Regime)
        assert 0.0 <= state.confidence <= 1.0
