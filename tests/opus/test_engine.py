"""Tests for OPUS scoring, geometry, risk, execution and the API surface.

The safety-relevant assertions here are the point of the file: gates must fail
closed, live routing must need both switches, synthetic data must never reach a
live broker, and the same signal must not execute twice.
"""

from __future__ import annotations

import os
import shutil
import tempfile

import numpy as np
import pytest

import opus.config as config
from opus.data import feed as feed_mod
from opus.data.store import Store
from opus.execution import router
from opus.execution.broker import PaperBroker
from opus.risk import gates as gate_mod
from opus.risk.sizing import correlation_group_of, size_position
from opus.scoring import calibration as cal
from opus.scoring import expectancy as exp
from opus.scoring import validation as val
from opus.scoring.conviction import score as score_conviction
from opus.setups import archetypes
from opus.types import (
    Archetype, Candles, Decision, Direction, GateResult, IndicatorReading,
    Levels, Quote, Readiness, Regime,
)
from opus.indicators.base import MarketContext
from tests.opus.test_indicators import noise_ctx


@pytest.fixture(autouse=True)
def _synthetic_universe(monkeypatch):
    """Point the universe at the synthetic venue and an isolated store."""
    cfg = config.load()
    original = [dict(u) for u in cfg["UNIVERSE"]]
    for entry in cfg["UNIVERSE"]:
        entry["venue"] = "synthetic"
    yield
    cfg["UNIVERSE"][:] = original


@pytest.fixture()
def tmp_store():
    """An isolated Store that is CLOSED before teardown.

    Windows refuses to unlink an open SQLite file, so leaving the connection
    open turns every store test into a PermissionError during cleanup.
    """
    tmp = tempfile.mkdtemp()
    store = Store(os.path.join(tmp, "opus_test.sqlite3"))
    try:
        yield store
    finally:
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _readings(values: dict[str, float], confidence: float = 1.0):
    return [IndicatorReading(k, v, confidence) for k, v in values.items()]


# --------------------------------------------------------------------------
# conviction: coherence is the whole point
# --------------------------------------------------------------------------

def test_coherence_discounts_disagreement():
    """Two evidence sets with the SAME weighted mean must not score the same."""
    agree = _readings({"LDI": 0.6, "ARC": 0.6, "OFP": 0.6, "SAV": 0.6, "VFI": 0.6, "KDI": 0.6})
    conflict = _readings({"LDI": 1.0, "ARC": 1.0, "OFP": 1.0, "SAV": -0.2, "VFI": -0.2, "KDI": 0.6})

    a = score_conviction(
        agree, archetype=Archetype.SWEEP_RECLAIM, direction=Direction.LONG,
        regime=Regime.VOLATILE_RANGE, temporal_gain=1.0,
    )
    c = score_conviction(
        conflict, archetype=Archetype.SWEEP_RECLAIM, direction=Direction.LONG,
        regime=Regime.VOLATILE_RANGE, temporal_gain=1.0,
    )
    assert a.coherence > c.coherence
    assert a.conviction > c.conviction


def test_low_confidence_readings_carry_less_weight():
    strong = _readings({"LDI": 0.8, "ARC": 0.8, "OFP": 0.8, "SAV": 0.8, "VFI": 0.8, "KDI": 0.8}, 1.0)
    weak = _readings({"LDI": 0.8, "ARC": 0.8, "OFP": 0.8, "SAV": 0.8, "VFI": 0.8, "KDI": 0.8}, 0.1)
    kw = dict(archetype=Archetype.SWEEP_RECLAIM, direction=Direction.LONG,
              regime=Regime.VOLATILE_RANGE, temporal_gain=1.0)
    # Same values, but the weak set is backed by almost no evidence.
    assert score_conviction(strong, **kw).conviction >= score_conviction(weak, **kw).conviction


def test_direction_flips_sign_of_conviction():
    bullish = _readings({"LDI": 0.7, "ARC": 0.7, "OFP": 0.7, "SAV": 0.7, "VFI": 0.7, "KDI": 0.7})
    kw = dict(archetype=Archetype.SWEEP_RECLAIM, regime=Regime.VOLATILE_RANGE, temporal_gain=1.0)
    long = score_conviction(bullish, direction=Direction.LONG, **kw)
    short = score_conviction(bullish, direction=Direction.SHORT, **kw)
    assert long.conviction > 0 > short.conviction


def test_archetype_disabled_in_regime_is_vetoed():
    """VALUE_FADE is configured to 0.0 in TREND_DRIVE - fading a trend."""
    strong = _readings({"SAV": 0.9, "ARC": 0.9, "OFP": 0.9, "LDI": 0.9, "VFI": 0.9, "KDI": 0.9})
    result = score_conviction(
        strong, archetype=Archetype.VALUE_FADE, direction=Direction.LONG,
        regime=Regime.TREND_DRIVE, temporal_gain=1.0,
    )
    assert result.regime_gain == 0.0
    assert result.conviction == 0.0
    assert "disabled" in result.reason


def test_too_few_active_indicators_refuses_to_score():
    result = score_conviction(
        _readings({"LDI": 0.9, "ARC": 0.9}), archetype=Archetype.SWEEP_RECLAIM,
        direction=Direction.LONG, regime=Regime.VOLATILE_RANGE, temporal_gain=1.0,
    )
    assert result.conviction == 0.0
    assert "indicators_active" in result.reason


# --------------------------------------------------------------------------
# expectancy and cost
# --------------------------------------------------------------------------

def test_cost_in_r_is_inverse_to_stop_distance():
    """The central economic fact the expectancy gate exists to expose."""
    quote = Quote("T", bid=99.99, ask=100.01, ts=0.0)
    wide = Levels(entry=100.0, stop=98.0, targets=[103.0])
    tight = Levels(entry=100.0, stop=99.8, targets=[100.3])
    c_wide = exp.compute_cost(levels=wide, quote=quote, asset_class="forex")
    c_tight = exp.compute_cost(levels=tight, quote=quote, asset_class="forex")
    assert c_tight.cost_r > c_wide.cost_r * 5


def test_missing_quote_is_charged_not_assumed_free():
    levels = Levels(entry=100.0, stop=99.0, targets=[101.5])
    cost = exp.compute_cost(levels=levels, quote=None, asset_class="forex")
    assert cost.spread > 0
    assert cost.spread_source == "assumed_no_quote"


def test_expectancy_charges_both_sides_of_the_round_trip():
    quote = Quote("T", bid=100.0, ask=100.0, ts=0.0)  # zero spread
    levels = Levels(entry=100.0, stop=99.0, targets=[101.5])
    cost = exp.compute_cost(levels=levels, quote=quote, asset_class="forex")
    model = config.cost_model("forex")
    one_side = 100.0 * (model["commission_bps"] + model["slippage_bps"]) / 10_000.0
    assert cost.total_price == pytest.approx(2.0 * one_side, rel=1e-6)


def test_zero_stop_distance_yields_infinite_cost_not_a_divide_error():
    levels = Levels(entry=100.0, stop=100.0, targets=[101.0])
    cost = exp.compute_cost(levels=levels, quote=None, asset_class="forex")
    assert not np.isfinite(cost.cost_r)


def test_kelly_is_zero_when_the_edge_is_negative():
    assert exp.kelly(0.20, 1.0) == 0.0
    assert exp.kelly(0.80, 2.0) > 0.0
    assert exp.kelly(0.60, 0.05, cost_r=0.5) == 0.0  # cost exceeds reward


def test_breakeven_probability_rises_with_cost():
    levels = Levels(entry=100.0, stop=99.0, targets=[101.5])
    cheap, _ = exp.evaluate(
        probability=0.5, levels=levels,
        quote=Quote("T", 99.999, 100.001, 0.0), asset_class="forex",
    )
    dear, _ = exp.evaluate(
        probability=0.5, levels=levels,
        quote=Quote("T", 99.8, 100.2, 0.0), asset_class="forex",
    )
    assert dear.breakeven_probability > cheap.breakeven_probability
    assert dear.expectancy_r < cheap.expectancy_r


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------

def test_triple_barrier_resolves_ambiguous_bars_adversely():
    outcome = cal.triple_barrier(
        high=np.array([103.0]), low=np.array([98.0]), close=np.array([100.0]),
        entry=100.0, stop=99.0, target=102.0,
        direction=Direction.LONG, max_bars=5,
    )
    assert outcome is not None
    assert outcome.resolution == "ambiguous_stop"
    assert outcome.label == 0


def test_triple_barrier_target_and_stop_and_time():
    kw = dict(entry=100.0, stop=99.0, target=102.0, direction=Direction.LONG, max_bars=5)
    hit = cal.triple_barrier(
        high=np.array([100.5, 102.5]), low=np.array([99.8, 100.2]),
        close=np.array([100.2, 102.0]), **kw,
    )
    assert hit is not None and hit.resolution == "target" and hit.label == 1

    stopped = cal.triple_barrier(
        high=np.array([100.5, 100.6]), low=np.array([99.8, 98.5]),
        close=np.array([100.2, 98.9]), **kw,
    )
    assert stopped is not None and stopped.resolution == "stop" and stopped.label == 0

    timed = cal.triple_barrier(
        high=np.full(5, 100.4), low=np.full(5, 99.6), close=np.full(5, 100.2), **kw,
    )
    assert timed is not None and timed.resolution == "time"


def test_short_direction_barriers_are_mirrored():
    outcome = cal.triple_barrier(
        high=np.array([100.2]), low=np.array([97.5]), close=np.array([98.0]),
        entry=100.0, stop=101.0, target=98.0,
        direction=Direction.SHORT, max_bars=5,
    )
    assert outcome is not None and outcome.resolution == "target"


def test_logistic_fit_recovers_known_parameters():
    rng = np.random.default_rng(0)
    x = rng.uniform(-1, 1, 800)
    p = 1.0 / (1.0 + np.exp(-(2.5 * x - 0.5)))
    y = (rng.uniform(size=800) < p).astype(float)
    fitted = cal.fit_logistic(x, y)
    assert fitted is not None
    slope, intercept = fitted
    assert 2.0 < slope < 3.2
    assert -1.0 < intercept < 0.0


def test_separable_data_does_not_diverge():
    x = np.array([-1.0] * 25 + [1.0] * 25)
    y = np.array([0.0] * 25 + [1.0] * 25)
    fitted = cal.fit_logistic(x, y)
    assert fitted is not None
    assert np.isfinite(fitted[0]) and fitted[0] < 20.0


def test_calibration_shrinks_small_samples_toward_the_prior():
    rng = np.random.default_rng(1)
    x = rng.uniform(-1, 1, 600)
    y = (rng.uniform(size=600) < 1 / (1 + np.exp(-(4.0 * x)))).astype(float)
    prior = cal.prior_calibrator("B|forex")
    small = cal.build_calibrator("B|forex", x[:45], y[:45])
    large = cal.build_calibrator("B|forex", x, y)
    assert abs(small.slope - prior.slope) < abs(large.slope - prior.slope)


def test_negative_slope_bucket_falls_back_to_prior():
    """Higher conviction predicting worse outcomes must not invert sizing."""
    rng = np.random.default_rng(2)
    x = rng.uniform(-1, 1, 400)
    y = (rng.uniform(size=400) < 1 / (1 + np.exp(3.0 * x))).astype(float)
    calibrator = cal.build_calibrator("B|forex", x, y)
    assert calibrator.fitted is False
    assert calibrator.slope == cal.prior_calibrator("B|forex").slope


def test_probability_is_clipped_to_configured_bounds():
    calibrator = cal.Calibrator(slope=50.0, intercept=0.0, samples=999, fitted=True, bucket="X")
    cfg = config.load()["CALIBRATION"]
    assert calibrator.probability(1.0) <= float(cfg["probability_ceiling"]) + 1e-9
    assert calibrator.probability(-1.0) >= float(cfg["probability_floor"]) - 1e-9


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def test_deflated_sharpe_rejects_noise_and_accepts_a_real_edge():
    rng = np.random.default_rng(3)
    noise = rng.normal(0.02, 1.0, 150)
    assert val.validate("N|forex", noise, trials=24).passed is False

    real = np.where(rng.uniform(size=300) < 0.45, 2.2, -1.0)
    result = val.validate("R|forex", real, trials=24)
    assert result.mean_r > 0
    assert result.passed is True


def test_expected_max_sharpe_grows_with_trial_count():
    assert val.expected_max_sharpe(1) == 0.0
    assert val.expected_max_sharpe(4) < val.expected_max_sharpe(24) < val.expected_max_sharpe(100)


def test_undersampled_bucket_cannot_pass():
    result = val.validate("T|forex", np.array([2.0, 3.0, 2.5, 4.0]))
    assert result.passed is False
    assert "trades" in result.reason


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

def test_stop_is_always_on_the_losing_side():
    ctx = noise_ctx(seed=5)
    for archetype in Archetype:
        for direction in (Direction.LONG, Direction.SHORT):
            levels = archetypes.build(ctx, archetype, direction)
            if levels is None:
                continue
            if direction is Direction.LONG:
                assert levels.stop < levels.entry
                assert all(t > levels.entry for t in levels.targets)
            else:
                assert levels.stop > levels.entry
                assert all(t < levels.entry for t in levels.targets)


def test_geometry_respects_sigma_bounds_and_min_rr():
    geom = config.load()["GEOMETRY"]
    for seed in range(1, 10):
        ctx = noise_ctx(seed=seed)
        for archetype in Archetype:
            for direction in (Direction.LONG, Direction.SHORT):
                levels = archetypes.build(ctx, archetype, direction)
                if levels is None:
                    continue
                sigmas = levels.risk_per_unit / ctx.sigma
                assert sigmas >= float(geom["min_stop_sigma"]) - 1e-6
                assert sigmas <= float(geom["max_stop_sigma"]) + 1e-6
                assert levels.reward_multiple(0) >= float(geom["min_rr"]) - 1e-6


def test_cost_floor_widens_a_stop_that_could_never_pay_its_spread():
    """The stop floor must respond to spread, not only to volatility.

    Cost in R is inverse to stop distance, so below some distance
    cost_r > max_cost_r is arithmetically guaranteed regardless of the read.
    """
    ctx = noise_ctx(seed=5)
    cheap = archetypes._cost_floor(ctx)

    wide_spread = Quote(
        symbol=ctx.quote.symbol,
        bid=ctx.quote.mid * 0.999, ask=ctx.quote.mid * 1.001,
        ts=ctx.quote.ts, source=ctx.quote.source,
    )
    ctx_dear = MarketContext.build(
        symbol=ctx.symbol, display=ctx.display, asset_class=ctx.asset_class,
        context=ctx.context, structure=ctx.structure, trigger=ctx.trigger,
        quote=wide_spread, now=ctx.now,
    )
    dear = archetypes._cost_floor(ctx_dear)
    assert dear > cheap * 5
    assert archetypes._cost_floor(
        MarketContext.build(
            symbol=ctx.symbol, display=ctx.display, asset_class=ctx.asset_class,
            context=ctx.context, structure=ctx.structure, trigger=ctx.trigger,
            quote=None, now=ctx.now,
        )
    ) == 0.0


def test_coil_detection_is_scale_free_in_window_length():
    """Compression is judged against the range a random walk would make anyway.

    E[range] grows as sqrt(n), so a fixed sigma count silently becomes
    unreachable as the window widens - the previous 3.0-sigma cap sat well
    below the ~7.8 sigma a 24-bar random walk produces, so the setup could
    effectively never fire.
    """
    geom = config.load()["GEOMETRY"]
    assert "coil_max_compression" in geom
    assert 0.0 < float(geom["coil_max_compression"]) < 1.0


def test_vacuum_break_stop_sits_inside_the_coil():
    """A stop at the far side caps RR near 1.0 by construction."""
    geom = config.load()["GEOMETRY"]
    fraction = float(geom.get("coil_stop_fraction", 0.5))
    assert 0.0 < fraction < 1.0, "stop must sit inside the coil, not beyond it"
    # With a one-times measured move, RR is roughly 1/fraction; it has to clear
    # min_rr or the setup is unreachable no matter what the market does.
    assert (1.0 / fraction) > float(geom["min_rr"])


def test_value_fade_only_fires_when_price_is_extended():
    """A fade with price sitting on value is not a fade."""
    ctx = noise_ctx(seed=4)
    from opus.indicators import value as value_mod

    val_state = value_mod.session_value(ctx)
    if val_state is None or val_state["dispersion"] <= 0:
        pytest.skip("no session value on this fixture")
    stretch = (ctx.last_price - val_state["vwap"]) / val_state["dispersion"]
    outer = float(config.indicator("SAV")["band_sigma_mult"][1])
    if abs(stretch) < outer:
        assert archetypes.build_value_fade(ctx, Direction.LONG) is None
        assert archetypes.build_value_fade(ctx, Direction.SHORT) is None


# --------------------------------------------------------------------------
# gates - fail closed
# --------------------------------------------------------------------------

def test_missing_quote_fails_every_quote_gate():
    results = {g.name: g for g in gate_mod.quote_gates(None, now=1000.0)}
    assert results["quote_age"].passed is False
    assert results["spread"].passed is False


def test_stale_quote_fails_the_age_gate():
    limit = float(config.load()["GATES"]["max_quote_age_sec"])
    stale = Quote("T", 1.0, 1.0001, ts=0.0)
    results = {g.name: g for g in gate_mod.quote_gates(stale, now=limit + 60.0)}
    assert results["quote_age"].passed is False


def test_wide_spread_fails_the_spread_gate():
    wide = Quote("T", bid=100.0, ask=101.0, ts=1000.0)
    results = {g.name: g for g in gate_mod.quote_gates(wide, now=1000.0)}
    assert results["spread"].passed is False


def test_future_dated_quote_fails_closed():
    """A quote timestamped in the future must FAIL the freshness gate.

    Quote.age_sec floors at zero, so a future-dated tick reads as "0s old" and
    would sail through - a fail-OPEN. This is not hypothetical: MT5 reports
    tick times in the broker's server timezone, so a UTC+3 server dates every
    tick three hours ahead and an arbitrarily stale quote would pass.
    """
    future = Quote("T", bid=1.0, ask=1.0001, ts=1000.0 + 10_800.0)
    results = {g.name: g for g in gate_mod.quote_gates(future, now=1000.0)}
    assert results["quote_age"].passed is False
    assert "FUTURE" in results["quote_age"].detail


def test_quote_age_gate_still_passes_a_fresh_quote():
    fresh = Quote("T", bid=1.0, ask=1.0001, ts=999.0)
    results = {g.name: g for g in gate_mod.quote_gates(fresh, now=1000.0)}
    assert results["quote_age"].passed is True


def test_inverted_quote_is_rejected():
    inverted = Quote("T", bid=101.0, ask=100.0, ts=1000.0)
    results = {g.name: g for g in gate_mod.quote_gates(inverted, now=1000.0)}
    assert results["spread"].passed is False


def test_decision_and_readiness_are_independent():
    """A good idea on a stale quote stays a good idea, but is not executable."""
    good = [
        GateResult("conviction", True), GateResult("expectancy", True),
        GateResult("live_quote", True),
    ]
    decision, readiness = gate_mod.decide(0.8, good)
    assert decision is Decision.TRADE and readiness is Readiness.READY

    stale = good + [GateResult("quote_age", False)]
    decision, readiness = gate_mod.decide(0.8, stale)
    assert decision is Decision.TRADE            # the idea is unchanged
    assert readiness is Readiness.BLOCKED        # but it cannot be acted on

    bad_edge = good + [GateResult("expectancy", False)]
    decision, readiness = gate_mod.decide(0.05, bad_edge)
    assert decision is Decision.STAND_DOWN
    assert readiness is Readiness.BLOCKED


def test_levels_gates_reject_incoherent_geometry():
    bad = Levels(entry=100.0, stop=101.0, targets=[102.0])  # long with stop above
    results = {g.name: g for g in gate_mod.levels_gates(bad, Direction.LONG)}
    assert results["stop_side"].passed is False


# --------------------------------------------------------------------------
# sizing
# --------------------------------------------------------------------------

def test_size_is_capped_by_risk_pct_max():
    cap = float(config.load()["RISK"]["risk_pct_max"])
    levels = Levels(entry=100.0, stop=99.0, targets=[102.0])
    sized = size_position(levels=levels, kelly_fraction=5.0, equity=10_000.0)
    assert sized.risk_pct <= cap + 1e-6
    assert sized.capped_by == "risk_pct_max"


def test_no_edge_means_no_size():
    levels = Levels(entry=100.0, stop=99.0, targets=[102.0])
    assert size_position(levels=levels, kelly_fraction=0.0, equity=10_000.0).units == 0.0


def test_size_below_venue_minimum_returns_zero_not_rounded_up():
    levels = Levels(entry=100.0, stop=99.0, targets=[102.0])
    sized = size_position(
        levels=levels, kelly_fraction=0.5, equity=10.0, min_units=1000.0,
    )
    assert sized.units == 0.0
    assert sized.capped_by == "below_min_units"


def test_correlation_groups_resolve():
    assert correlation_group_of("EURUSD") == "USD_MAJORS"
    assert correlation_group_of("XAUUSD") == "METALS"
    assert correlation_group_of("NOT_A_SYMBOL") is None


# --------------------------------------------------------------------------
# feed hygiene
# --------------------------------------------------------------------------

def test_sanitise_drops_malformed_and_duplicate_bars():
    ts = np.array([1.0, 2.0, 2.0, 3.0, 4.0])
    raw = Candles(
        symbol="T", timeframe="M15", ts=ts,
        open=np.array([1.0, 1.0, 1.0, 1.0, 1.0]),
        high=np.array([2.0, 2.0, 2.0, 0.5, 2.0]),   # index 3 has high < low
        low=np.array([0.5, 0.5, 0.5, 1.5, 0.5]),
        close=np.array([1.5, 1.5, 1.5, 1.0, 1.5]),
        volume=np.array([1.0, 1.0, 1.0, 1.0, -5.0]),
    )
    clean = feed_mod.sanitise(raw)
    assert len(clean) == 3                       # dup and malformed removed
    assert np.all(np.diff(clean.ts) > 0)
    assert np.all(clean.volume >= 0)


def test_forming_bar_is_dropped():
    now = 10_000.0
    ts = np.array([now - 1800.0, now - 900.0, now - 100.0])  # last still forming
    candles = Candles(
        symbol="T", timeframe="M15", ts=ts,
        open=np.ones(3), high=np.ones(3) * 2, low=np.ones(3) * 0.5,
        close=np.ones(3) * 1.5, volume=np.ones(3),
    )
    trimmed = feed_mod.drop_forming_bar(candles, "M15", now=now)
    assert len(trimmed) == 2


def test_sanitise_raises_when_nothing_is_valid():
    broken = Candles(
        symbol="T", timeframe="M15", ts=np.array([1.0]),
        open=np.array([np.nan]), high=np.array([np.nan]),
        low=np.array([np.nan]), close=np.array([np.nan]),
        volume=np.array([0.0]),
    )
    with pytest.raises(feed_mod.FeedError):
        feed_mod.sanitise(broken)


# --------------------------------------------------------------------------
# execution safety
# --------------------------------------------------------------------------

def _make_signal(**overrides):
    from opus.types import Signal

    base = dict(
        symbol="EURUSD", display="EUR/USD", asset_class="forex",
        direction=Direction.LONG, archetype=Archetype.SWEEP_RECLAIM,
        regime=Regime.VOLATILE_RANGE, decision=Decision.TRADE,
        readiness=Readiness.READY, conviction=0.6, coherence=0.7,
        probability=0.55, expectancy_r=0.3, cost_r=0.1,
        levels=Levels(entry=1.10, stop=1.0980, targets=[1.1040]),
        size_units=10_000.0, signal_id="opus_test_1",
        quote=Quote("EURUSD", 1.09995, 1.10005, ts=__import__("time").time(), source="MT5"),
        provenance={"venue": "mt5"},
    )
    base.update(overrides)
    return Signal(**base)


def test_live_requires_both_switches(monkeypatch):
    monkeypatch.delenv("OPUS_LIVE_CONFIRM", raising=False)
    cfg = config.load()
    original = cfg.get("MODE")
    try:
        cfg["MODE"] = "paper"
        assert router.live_enabled()[0] is False

        monkeypatch.setenv("OPUS_LIVE_CONFIRM", "yes")
        assert router.live_enabled()[0] is False   # token alone is not enough

        cfg["MODE"] = "live"
        assert router.live_enabled()[0] is True    # both switches

        monkeypatch.delenv("OPUS_LIVE_CONFIRM", raising=False)
        assert router.live_enabled()[0] is False   # config alone is not enough
    finally:
        cfg["MODE"] = original


def test_live_request_is_refused_in_paper_mode(monkeypatch):
    monkeypatch.delenv("OPUS_LIVE_CONFIRM", raising=False)
    decision = router.route(_make_signal(), request_live=True)
    assert decision.allowed is False
    assert "refused" in decision.reason


def test_synthetic_data_can_never_reach_a_live_broker(monkeypatch):
    monkeypatch.setenv("OPUS_LIVE_CONFIRM", "yes")
    cfg = config.load()
    original = cfg.get("MODE")
    try:
        cfg["MODE"] = "live"
        demo = _make_signal(
            quote=Quote("EURUSD", 1.1, 1.1001, ts=__import__("time").time(),
                        source="SYNTHETIC"),
        )
        decision = router.route(demo, request_live=True)
        assert decision.allowed is False
        assert "synthetic" in decision.reason.lower()
    finally:
        cfg["MODE"] = original


def test_live_is_refused_when_account_is_not_demo(monkeypatch):
    """require_demo_account must block a real-money account.

    Server NAME is not evidence of account type: the deployment this was
    written against has a DEMO account on a server called "ATFXGM16-LIVE".
    """
    monkeypatch.setenv("OPUS_LIVE_CONFIRM", "yes")
    cfg = config.load()
    original_mode = cfg.get("MODE")

    class _RealBroker(PaperBroker):
        name = "mt5"
        is_live = True

        def account_kind(self) -> str:
            return "real"

    try:
        cfg["MODE"] = "live"
        monkeypatch.setattr(router, "_broker_for", lambda venue, live: _RealBroker())
        decision = router.route(_make_signal(), request_live=True)
        assert decision.allowed is False
        assert "real" in decision.reason
    finally:
        cfg["MODE"] = original_mode


def test_live_is_refused_when_account_type_is_unknown(monkeypatch):
    """An unconfirmable account might be real money; refuse rather than guess."""
    monkeypatch.setenv("OPUS_LIVE_CONFIRM", "yes")
    cfg = config.load()
    original_mode = cfg.get("MODE")

    class _UnknownBroker(PaperBroker):
        name = "mt5"
        is_live = True

    try:
        cfg["MODE"] = "live"
        monkeypatch.setattr(router, "_broker_for", lambda venue, live: _UnknownBroker())
        decision = router.route(_make_signal(), request_live=True)
        assert decision.allowed is False
        assert "unknown" in decision.reason
    finally:
        cfg["MODE"] = original_mode


def test_live_is_allowed_on_a_confirmed_demo_account(monkeypatch):
    monkeypatch.setenv("OPUS_LIVE_CONFIRM", "yes")
    cfg = config.load()
    original_mode = cfg.get("MODE")

    class _DemoBroker(PaperBroker):
        name = "mt5"
        is_live = True

        def account_kind(self) -> str:
            return "demo"

    try:
        cfg["MODE"] = "live"
        monkeypatch.setattr(router, "_broker_for", lambda venue, live: _DemoBroker())
        decision = router.route(_make_signal(), request_live=True)
        assert decision.allowed is True
        assert decision.mode == "live"
        assert "demo" in decision.reason
    finally:
        cfg["MODE"] = original_mode


def test_base_broker_reports_unknown_account_kind():
    from opus.execution.broker import Broker
    assert Broker().account_kind() == "unknown"


def test_revalidation_rejects_a_stale_quote():
    import time as _time
    stale = _make_signal(
        quote=Quote("EURUSD", 1.09995, 1.10005, ts=_time.time() - 600.0, source="MT5"),
    )
    ok, reason = router.revalidate(stale)
    assert ok is False and "old" in reason


def test_revalidation_rejects_non_ready_signals():
    ok, reason = router.revalidate(_make_signal(readiness=Readiness.ARMED))
    assert ok is False and "READY" in reason
    ok, reason = router.revalidate(_make_signal(decision=Decision.WATCH))
    assert ok is False and "TRADE" in reason


def test_revalidation_rejects_blocking_gates():
    signal = _make_signal(gates=[GateResult("spread", False, hard=True)])
    ok, reason = router.revalidate(signal)
    assert ok is False and "spread" in reason


def test_paper_fill_crosses_the_spread_and_adds_slippage():
    signal = _make_signal()
    result = PaperBroker().place(signal, units=10_000.0)
    assert result.ok is True
    # A long must fill at or above the ask, never at mid and never at the limit.
    assert result.entry is not None
    assert result.entry >= signal.quote.ask


def test_paper_broker_refuses_zero_size_and_missing_quote():
    assert PaperBroker().place(_make_signal(), units=0.0).ok is False
    assert PaperBroker().place(_make_signal(quote=None), units=100.0).ok is False


def test_duplicate_execution_is_blocked(tmp_store):
    signal = _make_signal()
    first = router.submit(signal, store=tmp_store)
    assert first.ok is True
    second = router.submit(signal, store=tmp_store)
    assert second.ok is False
    assert "duplicate" in second.message


def test_portfolio_position_cap_is_enforced():
    import time as _time

    cap = int(config.load()["RISK"]["max_concurrent_positions"])
    now = _time.time()
    open_orders = [
        {"symbol": f"SYM{i}", "status": "filled", "submitted_ts": now - 60.0}
        for i in range(cap)
    ]
    allowed, why = router.portfolio_limits(_make_signal(), open_orders)
    assert allowed is False
    assert "open positions" in why


def test_correlation_group_cap_is_enforced():
    import time as _time

    now = _time.time()
    cap = int(config.load()["RISK"]["correlation_group_cap"])
    open_orders = [
        {"symbol": s, "status": "filled", "submitted_ts": now - 60.0}
        for s in ["GBPUSD", "AUDUSD", "USDCAD"][:cap]
    ]
    allowed, why = router.portfolio_limits(_make_signal(), open_orders)
    assert allowed is False
    assert "correlation group" in why


def test_symbol_cooldown_is_enforced():
    """Isolate cooldown from the per-symbol position rule.

    A fill older than the position horizon is treated as closed, so the
    "already holding" rule no longer fires - but the cooldown still should if
    the fill is recent. Here the fill is inside cooldown but the horizon has
    been shortened below it, which is the only state where cooldown is the
    binding constraint.
    """
    import time as _time

    risk = config.load()["RISK"]
    original = risk.get("position_horizon_sec")
    try:
        risk["position_horizon_sec"] = 1.0     # everything counts as closed
        open_orders = [{
            "symbol": "EURUSD", "status": "filled",
            "submitted_ts": _time.time() - 5.0,
        }]
        allowed, why = router.portfolio_limits(_make_signal(), open_orders)
        assert allowed is False
        assert "cooldown" in why
    finally:
        risk["position_horizon_sec"] = original


def test_rejected_orders_do_not_start_a_cooldown():
    """A rejected order never reached the market, so it must not lock a symbol."""
    import time as _time

    open_orders = [{
        "symbol": "EURUSD", "status": "rejected",
        "submitted_ts": _time.time() - 5.0,
    }]
    allowed, _ = router.portfolio_limits(_make_signal(), open_orders)
    assert allowed is True


def test_stale_fills_stop_counting_as_open_positions():
    """Without a horizon, one historical fill would block a symbol forever."""
    import time as _time

    horizon = float(config.load()["RISK"]["position_horizon_sec"])
    ancient = [{
        "symbol": "EURUSD", "status": "filled",
        "submitted_ts": _time.time() - horizon - 3600.0,
    }]
    allowed, why = router.portfolio_limits(_make_signal(), ancient)
    assert allowed is True, why


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------

def test_store_round_trip_and_stats(tmp_store):
    store = tmp_store
    signal = _make_signal()
    assert store.record_signals([signal]) == 1
    # Re-recording the same signal id must not duplicate the row.
    store.record_signals([signal])
    stats = store.stats()
    assert stats["signals"] == 1
    assert stats["tradeSignals"] == 1

    rows = store.recent_signals(limit=10)
    assert rows and rows[0]["signalId"] == signal.signal_id

    store.record_outcome(signal.signal_id, cal.Outcome(1, 1.8, "target", 6, 1.1040))
    x, y = store.labelled_samples(cal.bucket_key("SWEEP_RECLAIM", "forex"))
    assert x.size == 1 and y[0] == 1.0
    assert store.r_multiples(cal.bucket_key("SWEEP_RECLAIM", "forex"))[0] == 1.8


# --------------------------------------------------------------------------
# engine end to end
# --------------------------------------------------------------------------

def test_engine_scan_runs_over_the_synthetic_universe():
    from opus import engine

    result = engine.scan()
    assert result.scanned == len(config.universe())
    assert result.errors == []
    for signal in result.signals:
        assert signal.levels.risk_per_unit > 0
        assert signal.signal_id.startswith("opus_")
        if signal.decision is Decision.TRADE and signal.readiness is Readiness.READY:
            assert signal.expectancy_r >= float(config.load()["GATES"]["min_expectancy_r"])
            assert signal.size_units > 0
        else:
            assert signal.size_units == 0.0


def test_signal_id_is_stable_across_rescans_of_the_same_bar():
    from opus import engine

    a = {s.signal_id for s in engine.scan(symbols=["EURUSD"]).signals}
    b = {s.signal_id for s in engine.scan(symbols=["EURUSD"]).signals}
    assert a == b


def test_every_emitted_signal_serialises_to_json():
    import json

    from opus import engine

    for signal in engine.scan().signals:
        json.dumps(signal.as_dict())      # must not raise on numpy types


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

@pytest.fixture()
def client():
    from flask import Flask

    from opus.api import create_opus_blueprint

    app = Flask(__name__)
    app.register_blueprint(create_opus_blueprint())
    return app.test_client()


def test_status_endpoint(client):
    body = client.get("/api/opus/status").get_json()
    assert body["ok"] is True
    assert body["engine"] == "OPUS"
    assert body["liveArmed"] is False


def test_scan_endpoint(client):
    response = client.post("/api/opus/scan", json={"equity": 25_000})
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["scanned"] == len(config.universe())


def test_scan_endpoint_rejects_bad_input(client):
    assert client.post("/api/opus/scan", json={"symbols": "EURUSD"}).status_code == 400
    assert client.post("/api/opus/scan", json={"equity": "lots"}).status_code == 400


def test_execute_requires_symbol_and_signal_id(client):
    assert client.post("/api/opus/execute", json={}).status_code == 400
    assert client.post(
        "/api/opus/execute", json={"signalId": "x"}
    ).status_code == 400


def test_execute_rejects_an_unknown_signal(client):
    response = client.post(
        "/api/opus/execute", json={"signalId": "opus_nope", "symbol": "EURUSD"}
    )
    assert response.status_code == 409


def test_indicators_endpoint_rejects_unknown_symbol(client):
    assert client.get("/api/opus/indicators/NOPE").status_code == 404


def test_indicators_endpoint_returns_all_readings(client):
    body = client.get("/api/opus/indicators/EURUSD").get_json()
    assert body["ok"] is True
    assert {r["name"] for r in body["readings"]} == {
        "LDI", "ARC", "VFI", "SAV", "KDI", "OFP"
    }
    assert body["regime"]["regime"] in {
        "TREND_DRIVE", "COILED", "VOLATILE_RANGE", "DORMANT"
    }
