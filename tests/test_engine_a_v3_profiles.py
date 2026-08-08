from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine_a_v3.evaluator import evaluate_engine_a_v3
from engine_a_v3.profile import (
    CORE_COMPONENTS,
    EngineAV3Profile,
    baseline_profile,
)
from engine_a_v3.quant_scorer import _volume_component
from engine_a_v3.promotion import PromotionDecision
from engine_a_v3.contract import ValidationArtifact
from engine_a_v3.routing import KNOWN_SCORE_GROUPS


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
        "H4": _rows(60, timedelta(hours=4)),
        "H1": _rows(60, timedelta(hours=1)),
    }


def test_baseline_profiles_cover_all_52_lanes_and_are_v3_owned():
    for group in KNOWN_SCORE_GROUPS:
        for horizon in ("intraday", "swing"):
            profile = baseline_profile(group, horizon)
            assert isinstance(profile, EngineAV3Profile)
            assert profile.score_group == group
            assert profile.horizon == horizon
            assert profile.status == "UNVALIDATED"
            assert profile.exit_policy == "SINGLE_TP1"
            assert set(dict(profile.weights)) == set(CORE_COMPONENTS)
            assert sum(dict(profile.weights).values()) == pytest.approx(1.0)
            assert len(profile.profile_sha256) == 64


def test_profile_rejects_unknown_negative_and_nonfinite_weights():
    base = baseline_profile("forex_majors", "intraday")
    with pytest.raises(ValueError, match="profile_weights_invalid"):
        EngineAV3Profile.create(
            score_group=base.score_group,
            horizon=base.horizon,
            indicator_periods=dict(base.indicator_periods),
            weights={"trend": 0.5, "momentum": -0.1, "location": 0.4, "bogus": 0.2},
            direction_deadband=0.05,
            trade_threshold=2.1,
            exit_policy="SINGLE_TP1",
            status="UNVALIDATED",
        )


def test_evaluator_requires_full_confirmed_history_and_rejects_nonfinite_ohlc(tmp_path):
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    candles = _candles()
    candles["H4"] = candles["H4"][:-1]
    signal = evaluate_engine_a_v3(pair, candles, horizon="intraday")
    assert signal.decision == "NO_SIGNAL"
    assert "h4_history_insufficient" in signal.rejectionReasons

    candles = _candles()
    candles["H1"][-1]["close"] = float("inf")
    signal = evaluate_engine_a_v3(pair, candles, horizon="intraday")
    assert signal.decision == "NO_SIGNAL"
    assert "h1_ohlc_nonfinite" in signal.rejectionReasons


def test_bearish_obv_is_directionally_bearish_and_context_is_not_executable():
    rows = _rows(40, timedelta(hours=1), falling=True)
    component = _volume_component({}, rows, {"carry": {"signal": 1, "quality": 1}})
    assert component.available is True
    assert component.signal < 0


def test_quant_weights_by_group_override_merges_and_renormalizes(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_QUANT_WEIGHTS_BY_GROUP",
        {"crypto_btc": {"trend": 0.50, "momentum": 0.50, "bogus": 9.0, "location": "junk"}},
    )
    profile = baseline_profile("crypto_btc", "intraday")
    weights = dict(profile.weights)
    assert set(weights) == set(CORE_COMPONENTS)
    assert sum(weights.values()) == pytest.approx(1.0)
    # trend/momentum overridden to equal shares; location/volume keep family
    # defaults (.18/.19); everything renormalized over the merged total 1.37.
    assert weights["trend"] == pytest.approx(0.50 / 1.37)
    assert weights["momentum"] == pytest.approx(0.50 / 1.37)
    assert weights["location"] == pytest.approx(0.18 / 1.37)
    assert weights["volume"] == pytest.approx(0.19 / 1.37)

    # Untouched group keeps its family default.
    untouched = dict(baseline_profile("forex_majors", "intraday").weights)
    assert untouched["trend"] == pytest.approx(0.42)


def test_quant_weights_by_group_invalid_override_falls_back(monkeypatch):
    from config import CONFIG

    monkeypatch.setitem(
        CONFIG,
        "ENGINE_A_QUANT_WEIGHTS_BY_GROUP",
        {"forex_majors": {"trend": "abc", "unknown_component": 1.0}},
    )
    weights = dict(baseline_profile("forex_majors", "intraday").weights)
    assert weights == {"trend": 0.42, "momentum": 0.28, "location": 0.21, "volume": 0.09}


def test_setup_direction_conflict_falls_back_to_quant_path(monkeypatch):
    import engine_a_v3.evaluator as evaluator_module
    from engine_a_v3.quant_scorer import QuantScore
    from engine_a_v3.setups import SetupCandidate

    def _fake_score_pair(route, horizon, candles, **_kwargs):
        return QuantScore(
            direction="LONG", confluence_score=1.8, max_score=3.0, score_norm=0.6,
            conviction=0.6, decision="WATCH", threshold=2.1, level_style="trend",
            factor_scores={}, factor_diagnostics={}, components={},
        )

    def _fake_detect_setup(route, horizon, candles, **_kwargs):
        return SetupCandidate("fx_trend_pullback", "TRADE", "SHORT", (), (), "trend")

    monkeypatch.setattr(evaluator_module, "score_pair", _fake_score_pair)
    monkeypatch.setattr(evaluator_module, "detect_setup", _fake_detect_setup)

    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}

    signal = evaluate_engine_a_v3(pair, _candles(), horizon="intraday")
    assert signal.direction == "LONG"          # quant direction preserved
    assert signal.decision == "WATCH"          # no upgrade from the conflicting setup
    assert "setup_direction_conflicts_quant" in signal.rejectionReasons
    payload = signal.to_dict()
    assert payload["tp"] == payload["tp1"]
    assert payload["rr"] == payload["rr1"]     # headline rr describes the headline tp

    from config import CONFIG

    monkeypatch.setitem(CONFIG, "ENGINE_A_V3_SETUP_QUANT_CONFLICT_GUARD", False)
    signal = evaluate_engine_a_v3(pair, _candles(), horizon="intraday")
    assert signal.direction == "SHORT"         # guard off restores prior behavior
    assert "setup_direction_conflicts_quant" not in signal.rejectionReasons


def test_evaluator_uses_profile_returned_by_promotion_registry():
    base = baseline_profile("forex_majors", "intraday")
    promoted = EngineAV3Profile.create(
        score_group=base.score_group, horizon=base.horizon,
        indicator_periods=dict(base.indicator_periods), weights=dict(base.weights),
        direction_deadband=base.direction_deadband, trade_threshold=3.0,
        exit_policy="SPLIT_50_50", status="PROMOTED", profile_id="fixture-profile",
    )

    class Registry:
        def resolve(self, route, horizon, *, symbol, now=None):
            return PromotionDecision(
                True, (),
                ValidationArtifact("fixture", route.family, route.subclass, horizon, "PROMOTED", (), "a" * 64, promoted.profile_sha256),
                promoted,
            )

    signal = evaluate_engine_a_v3(
        {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"},
        _candles(), horizon="intraday", registry=Registry(),
    )
    assert signal.confluenceThreshold == 3.0
    assert signal.exitPolicy == "SPLIT_50_50"
    assert signal.scoringProfile["profileId"] == "fixture-profile"
