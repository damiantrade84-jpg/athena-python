"""Regression tests for Engine A V3 quant_scorer audit fixes (F1–F7)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine_a_v3.evaluator import evaluate_engine_a_v3
from engine_a_v3.levels import build_mean_reversion_levels, build_structural_levels
from engine_a_v3.profile import EngineAV3Profile, baseline_profile
from engine_a_v3.promotion import PromotionDecision
from engine_a_v3.contract import ValidationArtifact
from engine_a_v3.quant_scorer import (
    Component,
    _component_vol_mults,
    _location_component,
    _momentum_component,
    _trend_component,
    _trend_health_mult,
    _volume_component,
    score_pair,
)
from engine_a_v3.routing import route_specialist


def _rows(count: int, step: timedelta, *, falling: bool = False) -> list[dict]:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        close = 200.0 - index * 0.1 if falling else 100.0 + index * 0.1
        rows.append(
            {
                "time": (start + step * index).isoformat(),
                "open": close - 0.02,
                "high": close + 0.05,
                "low": close - 0.05,
                "close": close,
                "vol": 1_000 + index,
            }
        )
    return rows


def _candles() -> dict[str, list[dict]]:
    return {
        "D1": _rows(220, timedelta(days=1)),
        # 60 bars: matches the ENGINE_A_MIN_H4_BARS/H1_BARS floor (raised from 50).
        "H4": _rows(60, timedelta(hours=4)),
        "H1": _rows(60, timedelta(hours=1)),
    }


def _bearish_snap(*, adx: float = 28.0, close: float = 89.0) -> dict:
    return {
        "close": close,
        "ema21": 92.0,
        "ema50": 95.0,
        "ema200": 100.0,
        "ema200Slope10": -0.5,
        "atr": 2.0,
        "adx": adx,
        "rsi": 38.0,
        "macdHist": -0.4,
        "plusDI": 12.0,
        "minusDI": 22.0,
        "bbUpper": 96.0,
        "bbLower": 86.0,
    }


def _stretched_below_snap(*, adx: float = 15.0) -> dict:
    return {
        "close": 94.0,
        "ema21": 100.0,
        "ema50": 102.0,
        "ema200": 105.0,
        "atr": 2.0,
        "adx": adx,
        "bbUpper": 104.0,
        "bbLower": 96.0,
    }


def _mr_primary_candles() -> list[dict]:
    """H1 history ending stretched below the EMA20 mean (fade-long geometry)."""
    rows = _rows(50, timedelta(hours=1))
    mean_zone = 100.0
    for offset, candle in enumerate(rows[-8:]):
        close = mean_zone - 2.0 - offset * 0.4
        candle.update(
            {
                "open": close + 0.1,
                "high": close + 0.3,
                "low": close - 0.4,
                "close": close,
            }
        )
    return rows


def test_mr_yields_tradable_level(monkeypatch):
    """Stretched-below-BB low-ADX H1 must yield TRADE with valid MR levels."""
    import engine_a_v3.quant_scorer as qs

    candles = _candles()
    candles["H1"] = _mr_primary_candles()

    def _fake_snapshots(candle_map, asset_type, periods, snapshot_cache=None):
        return {
            "D1": _bearish_snap(),
            "H4": _bearish_snap(),
            "H1": _stretched_below_snap(),
        }

    monkeypatch.setattr(qs, "_snapshots", _fake_snapshots)

    base = baseline_profile("forex_majors", "intraday")
    profile = EngineAV3Profile.create(
        score_group=base.score_group,
        horizon=base.horizon,
        indicator_periods=dict(base.indicator_periods),
        weights=dict(base.weights),
        direction_deadband=base.direction_deadband,
        trade_threshold=0.40,
        exit_policy="SINGLE_TP1",
        status="UNVALIDATED",
    )

    route = route_specialist({"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"})
    quant = score_pair(route, "intraday", candles, profile=profile)

    assert quant.level_style == "mean_reversion"
    assert quant.direction == "LONG"
    assert quant.decision == "TRADE"

    levels = build_mean_reversion_levels(candles["H1"], direction=quant.direction)
    assert levels is not None
    assert levels.invalidation < levels.price < levels.targets[-1].price


def test_mr_threshold_tracks_config_not_literal_18(monkeypatch):
    from config import CONFIG

    _, style_19 = _location_component(_stretched_below_snap(adx=19.0), "forex", "forex_majors")
    assert style_19 == "mean_reversion"

    _, style_20 = _location_component(_stretched_below_snap(adx=20.0), "forex", "forex_majors")
    assert style_20 == "trend"

    monkeypatch.setitem(
        CONFIG,
        "ADX_TREND_MIN_CLASS",
        {**dict(CONFIG.get("ADX_TREND_MIN_CLASS") or {}), "forex_majors": 25},
    )
    _, style_22 = _location_component(_stretched_below_snap(adx=22.0), "forex", "forex_majors")
    assert style_22 == "mean_reversion"
    _, style_24 = _location_component(_stretched_below_snap(adx=24.0), "forex", "forex_majors")
    assert style_24 == "mean_reversion"
    _, style_25 = _location_component(_stretched_below_snap(adx=25.0), "forex", "forex_majors")
    assert style_25 == "trend"


def test_setup_upgrade_respects_quality_floor(monkeypatch):
    import engine_a_v3.evaluator as evaluator_module
    from engine_a_v3.quant_scorer import QuantScore
    from engine_a_v3.setups import SetupCandidate

    def _fake_score_pair(route, horizon, candles, *, context=None, profile=None, snapshot_cache=None):
        return QuantScore(
            direction="FLAT",
            confluence_score=0.1,
            max_score=3.0,
            score_norm=0.0333,
            conviction=0.0333,
            decision="WATCH",
            threshold=2.1,
            level_style="trend",
            factor_scores={},
            factor_diagnostics={},
            components={},
        )

    def _fake_detect_setup(route, horizon, candles, *, display=None, indicator_periods=None):
        return SetupCandidate("fx_trend_pullback", "TRADE", "LONG", (), (), "trend")

    monkeypatch.setattr(evaluator_module, "score_pair", _fake_score_pair)
    monkeypatch.setattr(evaluator_module, "detect_setup", _fake_detect_setup)

    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    signal = evaluate_engine_a_v3(pair, _candles(), horizon="intraday")
    assert signal.decision == "WATCH"
    assert "setup_upgrade_below_quant_quality_floor" in signal.rejectionReasons


def test_volume_ratio_contra_expansion_biases_signal():
    rows = _rows(20, timedelta(hours=1))
    prev_close = rows[-2]["close"]
    rows[-1].update(
        {
            "open": prev_close + 0.2,
            "high": prev_close + 0.3,
            "low": prev_close - 0.6,
            "close": prev_close - 0.5,
        }
    )
    base = _volume_component({}, rows, None)
    assert base.available is True
    assert base.signal > 0

    contra = _volume_component({}, rows, {"volume_ratio": 2.5})
    assert contra.signal < base.signal


def test_trend_component_skips_unavailable_tf():
    good = {
        "close": 110.0,
        "ema21": 105.0,
        "ema50": 100.0,
        "ema200": 95.0,
        "ema200Slope10": 1.0,
    }
    incomplete = {"close": 100.0}
    weights = {"D1": 0.28, "H4": 0.40, "H1": 0.32}

    all_present, _ = _trend_component(
        {"D1": good, "H4": good, "H1": good}, weights
    )
    one_missing, _ = _trend_component(
        {"D1": good, "H4": incomplete, "H1": good}, weights
    )

    assert all_present.signal == pytest.approx(one_missing.signal)
    assert all_present.quality == pytest.approx(one_missing.quality)


def test_momentum_blend_exposes_component_terms():
    snap = {
        "rsi": 62.0,
        "plusDI": 24.0,
        "minusDI": 14.0,
        "macdHist": 0.12,
        "macdHistPrev": 0.08,
        "adx": 28.0,
    }
    _comp, diag = _momentum_component(snap, "forex", "forex_majors")
    assert diag.get("rsiTerm") is not None
    assert diag.get("macdSlopeTerm") is not None


def test_unavailable_volume_does_not_inflate_confluence(monkeypatch):
    import engine_a_v3.quant_scorer as qs
    from engine_a_v3.profile import EngineAV3Profile, baseline_profile
    from engine_a_v3.routing import route_specialist

    aligned = Component(1.0, 1.0, available=True)

    monkeypatch.setattr(qs, "_snapshots", lambda *a, **k: {"H1": {}, "H4": {}, "D1": {}})
    monkeypatch.setattr(qs, "_trend_component", lambda *a, **k: (aligned, {}))
    monkeypatch.setattr(qs, "_momentum_component", lambda *a, **k: (aligned, {}))
    monkeypatch.setattr(qs, "_location_component", lambda *a, **k: (aligned, "trend"))

    base = baseline_profile("forex_majors", "intraday")
    profile = EngineAV3Profile.create(
        score_group=base.score_group,
        horizon=base.horizon,
        indicator_periods=dict(base.indicator_periods),
        weights=dict(base.weights),
        direction_deadband=0.01,
        trade_threshold=0.1,
        exit_policy="SINGLE_TP1",
        status="UNVALIDATED",
    )
    route = route_specialist({"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"})
    candles = _candles()

    monkeypatch.setattr(
        qs,
        "_volume_component",
        lambda *a, **k: Component(1.0, 1.0, available=True),
    )
    with_volume = score_pair(route, "intraday", candles, profile=profile)

    monkeypatch.setattr(
        qs,
        "_volume_component",
        lambda *a, **k: Component(0.0, 0.0, available=False),
    )
    without_volume = score_pair(route, "intraday", candles, profile=profile)

    assert without_volume.confluence_score < with_volume.confluence_score


def test_subsystems_enabled_carry_can_flip_direction(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_V3_SUBSYSTEMS",
        {"ENABLED": True, "WEIGHTS_BY_FAMILY": {}},
    )
    import engine_a_v3.quant_scorer as qs

    def _fake_snapshots(candle_map, asset_type, periods, snapshot_cache=None):
        return {
            "D1": {"close": 100, "ema21": 100, "ema50": 100, "ema200": 100, "adxSlope": 0},
            "H4": {"close": 100, "ema21": 100, "ema50": 100, "ema200": 100, "adxSlope": 0},
            "H1": {"close": 100, "ema21": 100, "ema50": 100, "ema200": 100, "adxSlope": 0},
        }

    monkeypatch.setattr(qs, "_snapshots", _fake_snapshots)
    monkeypatch.setattr(qs, "_trend_component", lambda *a, **k: (Component(0.0, 0.5), {}))
    monkeypatch.setattr(qs, "_momentum_component", lambda *a, **k: (Component(0.0, 0.5), {}))
    monkeypatch.setattr(qs, "_location_component", lambda *a, **k: (Component(0.0, 0.5), "trend"))
    monkeypatch.setattr(qs, "_volume_component", lambda *a, **k: Component(0.0, 0.5, available=True))

    base = baseline_profile("forex_majors", "intraday")
    profile = EngineAV3Profile.create(
        score_group=base.score_group,
        horizon=base.horizon,
        indicator_periods=dict(base.indicator_periods),
        weights=dict(base.weights),
        direction_deadband=0.01,
        trade_threshold=0.1,
        exit_policy="SINGLE_TP1",
        status="UNVALIDATED",
    )
    route = route_specialist({"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"})
    long_quant = score_pair(
        route,
        "intraday",
        _candles(),
        profile=profile,
        context={"carry": {"signal": 0.9, "quality": 0.9, "state": "available"}},
    )
    short_quant = score_pair(
        route,
        "intraday",
        _candles(),
        profile=profile,
        context={"carry": {"signal": -0.9, "quality": 0.9, "state": "available"}},
    )
    assert long_quant.direction == "LONG"
    assert short_quant.direction == "SHORT"


def test_component_vol_mults_differ_by_component():
    snap = {"adx_pct": 0.8, "atr_pct": 0.95}
    mults = _component_vol_mults(snap)
    assert mults["momentum"] != mults["location"]


def test_trend_health_penalizes_stale_alignment():
    rows = _rows(40, timedelta(hours=1))
    snap = {
        "close": rows[-1]["close"],
        "ema21": rows[-1]["close"] - 0.01,
        "ema50": rows[-1]["close"] - 0.05,
        "ema200": rows[-1]["close"] - 0.2,
        "ema200Slope10": 0.5,
        "adx": 30.0,
        "adxSlope": 0.2,
        "adxPrev": 29.5,
    }
    fresh_comp, _ = _trend_component({"H1": snap}, {"H1": 1.0}, entry_candles=rows[-10:])
    stale_comp, _ = _trend_component({"H1": snap}, {"H1": 1.0}, entry_candles=rows)
    assert stale_comp.quality < fresh_comp.quality


def test_structural_levels_use_adaptive_tp2_rr():
    rows = _rows(30, timedelta(hours=1))
    low_vol = build_structural_levels(rows, direction="LONG", atr_pct=0.2)
    high_vol = build_structural_levels(rows, direction="LONG", atr_pct=0.95)
    assert low_vol is not None and high_vol is not None
    assert high_vol.targets[1].rr > low_vol.targets[1].rr


def test_contract_to_dict_unknown_decision_does_not_raise():
    from engine_a_v3.contract import (
        CONTRACT_VERSION,
        DataFreshness,
        EngineASetupSignal,
    )

    signal = EngineASetupSignal(
        contractVersion=CONTRACT_VERSION,
        signalId="test",
        engine="ENGINE_A_V3",
        pair="EUR/USD",
        symbol="EURUSD",
        type="forex",
        scoreGroup="forex_majors",
        family="forex",
        subclass="majors",
        horizon="intraday",
        setupId="quant_trend",
        decision="UNKNOWN",
        qualified=False,
        direction=None,
        decisionTime=datetime.now(timezone.utc).isoformat(),
        lastConfirmedCandleTs=None,
        validUntil=datetime.now(timezone.utc).isoformat(),
        entryZone=None,
        invalidation=None,
        targets=(),
        price=None,
        sl=None,
        tp1=None,
        tp2=None,
        rr1=None,
        rr2=None,
        predicates=(),
        rejectionReasons=(),
        dataFreshness=DataFreshness(
            allowed=True,
            policy="confirmed_only",
            lastConfirmedCandleTs=None,
            reason="test",
        ),
        validationArtifact=None,
    )
    payload = signal.to_dict()
    assert payload["signalTier"] == "skip"


def test_trend_health_mult_prefers_entry_tf_snap_for_swing():
    snaps = {
        "H1": {"adx": 10.0, "adxSlope": -2.0, "adxPrev": 30.0},
        "H4": {"adx": 30.0, "adxSlope": 0.0, "adxPrev": 29.0},
    }
    h1_mult = _trend_health_mult(0.8, snaps, None, None, entry_tf="H1")
    h4_mult = _trend_health_mult(0.8, snaps, None, None, entry_tf="H4")
    assert h1_mult < h4_mult


def test_f1_macd_slope_term_monotonic_for_large_histograms():
    """F1: a rising MACD histogram must never reduce the slope term.

    The old absolute 0.05 divisor floor + |term|<0.05 snap made the term
    non-monotonic (hist 1.03 -> 0.8 but 1.06 -> 0.06).
    """
    terms = []
    for hist in (1.01, 1.03, 1.06, 1.5):
        snap = {
            "rsi": 55.0,
            "plusDI": 24.0,
            "minusDI": 14.0,
            "macdHist": hist,
            "macdHistPrev": 1.0,
            "adx": 28.0,
        }
        _comp, diag = _momentum_component(snap, "crypto", "crypto_btc")
        terms.append(diag["macdSlopeTerm"])
    assert all(a <= b for a, b in zip(terms, terms[1:])), terms


def test_f1_macd_slope_term_varies_at_forex_scale():
    """F1: forex-scale histograms (~1e-4) must produce slope-dependent terms,
    not the old constant +/-0.8 snap that discarded slope information."""
    cases = [
        (0.00010, 0.00005),  # doubling
        (0.00011, 0.00010),  # slight rise
        (0.00005, 0.00010),  # halving
    ]
    terms = []
    for hist, hist_prev in cases:
        snap = {
            "rsi": 55.0,
            "plusDI": 24.0,
            "minusDI": 14.0,
            "macdHist": hist,
            "macdHistPrev": hist_prev,
            "adx": 28.0,
        }
        _comp, diag = _momentum_component(snap, "forex", "forex_majors")
        terms.append(diag["macdSlopeTerm"])
    assert len({round(t, 4) for t in terms}) == len(terms), terms
    # A weakening positive histogram scores below strengthening ones.
    assert terms[2] < terms[0] and terms[2] < terms[1]


def test_f13_score_pair_rejects_unsupported_horizon():
    """F13: an unsupported horizon fails closed instead of silently scoring as swing."""
    route = route_specialist({"display": "BTC/USDT", "symbol": "BTCUSDT", "type": "crypto"})
    quant = score_pair(route, "scalp", {"D1": [], "H4": [], "H1": []})
    assert quant.direction == "FLAT"
    assert quant.factor_diagnostics.get("rejectionReason") == "unsupported_horizon"
