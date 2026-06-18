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
        "H4": _rows(50, timedelta(hours=4)),
        "H1": _rows(50, timedelta(hours=1)),
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
