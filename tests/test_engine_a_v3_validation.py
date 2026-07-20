from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from engine_a_v3.promotion import PromotionRegistry, promote_candidate
from engine_a_v3.routing import route_specialist
from engine_a_v3.routing import KNOWN_SCORE_GROUPS
from engine_a_v3.workflow import load_manifest
from engine_a_v3.profile import EngineAV3Profile, baseline_profile, scorer_sha256
import engine_a_v3.validation as validation
from engine_a_v3.validation import (
    _promotion_passes,
    block_bootstrap_lower95,
    build_validation_windows,
    provenance_sha256,
)


def _backtest_bars(timeframe: str, count: int = 100) -> list[dict]:
    step = {"D1": timedelta(days=1), "H4": timedelta(hours=4), "H1": timedelta(hours=1)}[
        timeframe
    ]
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "time": (start + index * step).isoformat(),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "vol": 1_000.0,
        }
        for index in range(count)
    ]


# The two backtest_pair validation-mode routing tests were removed with the
# legacy backtester (archive/backtest_legacy/): validation_mode was a legacy
# request field. The v3 rebuild's API rejects unsupported styles at the route
# layer (tests/backtest_v3/test_api_routes.py).


def test_v3_promotion_validation_uses_resolved_entry_timeframe(monkeypatch):
    observed_primary_lengths: list[int] = []
    candles = {
        "D1": _backtest_bars("D1", count=150),
        "H4": _backtest_bars("H4", count=150),
        "H1": _backtest_bars("H1", count=600),
    }

    def _windows(total_bars, **_kwargs):
        observed_primary_lengths.append(total_bars)
        return (), validation.ValidationWindow("holdout", 0, 0, 0)

    monkeypatch.setattr(validation, "build_validation_windows", _windows)

    folds, holdout = validation._validation_results(
        {
            "display": "USD/MXN",
            "symbol": "USDMXN",
            "type": "forex",
            "score_group": "forex_exotics",
        },
        candles,
        horizon="intraday",
        spread_bps=1.0,
        commission_bps=1.0,
        slippage_bps=1.0,
        swap_bps_per_day=0.0,
    )

    assert observed_primary_lengths == [150]
    assert folds == []
    assert holdout == []


def test_validation_uses_four_purged_folds_and_untouched_20_percent_holdout():
    folds, holdout = build_validation_windows(1_000, purge_bars=25)

    assert len(folds) == 4
    assert holdout.train_end == 775
    assert holdout.test_start == 800
    assert holdout.test_end == 1_000
    assert holdout.test_end - holdout.test_start == 200
    assert all(window.test_start - window.train_end == 25 for window in folds)
    assert all(left.test_end <= right.train_end for left, right in zip(folds, folds[1:]))


def test_manifest_declares_all_52_lanes_and_pins_provider():
    manifest = load_manifest("configs/engine_a_v3_validation.yaml")
    assert set(manifest["groups"]) == set(KNOWN_SCORE_GROUPS)
    for group in manifest["groups"].values():
        assert group["horizons"] == ["intraday", "swing"]
        if group.get("validationEnabled") is True:
            assert group["requiredProvider"] in {"bybit", "mt5"}
            assert group.get("costsVerified") is True
        else:
            assert group["requiredProvider"] == "unverified"


def test_provenance_hash_is_order_stable_and_data_sensitive():
    candles = {
        "H1": [
            {"time": "2026-01-01T00:00:00Z", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "vol": 10}
        ],
        "D1": [
            {"time": "2026-01-01T00:00:00Z", "open": 1, "high": 3, "low": 0.5, "close": 2, "vol": 20}
        ],
    }
    reordered = {"D1": candles["D1"], "H1": candles["H1"]}
    changed = {"D1": candles["D1"], "H1": [{**candles["H1"][0], "close": 1.6}]}

    assert provenance_sha256(candles) == provenance_sha256(reordered)
    assert provenance_sha256(candles) != provenance_sha256(changed)


def test_promotion_requires_every_quality_gate():
    passing = {
        "oosTrades": 80,
        "foldExpectancyR": [0.1, 0.1, -0.01, 0.1],
        "holdoutExpectancyR": 0.1,
        "holdoutTrades": 15,
        "expectancyR": 0.08,
        "profitFactor": 1.2,
        "sqn": 1.5,
        "maxDrawdownR": 12.0,
        "bootstrapLower95ExpectancyR": 0.001,
    }

    assert _promotion_passes(passing, pair_adapter=False)
    assert not _promotion_passes({**passing, "oosTrades": 59}, pair_adapter=False)
    assert _promotion_passes({**passing, "oosTrades": 40}, pair_adapter=True)
    assert not _promotion_passes(
        {**passing, "bootstrapLower95ExpectancyR": 0.0},
        pair_adapter=True,
    )


def test_block_bootstrap_lower_bound_is_deterministic():
    results = [0.2, 0.3, -0.1, 0.4, 0.1] * 20

    first = block_bootstrap_lower95(results, samples=200)
    second = block_bootstrap_lower95(results, samples=200)

    assert first == second
    assert first is not None


def _candidate(status: str = "PROMOTED") -> dict:
    baseline = baseline_profile("forex_majors", "intraday")
    promoted_profile = EngineAV3Profile.create(
        score_group=baseline.score_group,
        horizon=baseline.horizon,
        indicator_periods=dict(baseline.indicator_periods),
        weights=dict(baseline.weights),
        direction_deadband=baseline.direction_deadband,
        trade_threshold=baseline.trade_threshold,
        exit_policy=baseline.exit_policy,
        status="PROMOTED",
        profile_id="forex-majors-intraday-profile",
        created_at="2026-06-13T00:00:00+00:00",
        valid_until="2027-06-13T00:00:00+00:00",
    )
    return {
        "schemaVersion": 3,
        "artifactId": "forex-majors-intraday-20260613",
        "family": "forex",
        "subclass": "majors",
        "horizon": "intraday",
        "status": status,
        "createdAt": "2026-06-13T00:00:00+00:00",
        "validUntil": "2027-06-13T00:00:00+00:00",
        "validationScope": {
            "type": "family",
            "expectedSymbols": ["EURUSD", "GBPUSD"],
            "validatedSymbols": ["EURUSD", "GBPUSD"],
        },
        "provenance": {
            "datasetId": "fixture-v2",
            "provider": "mt5",
            "sha256": "a" * 64,
            "implementationSha256": scorer_sha256(),
            "confirmedOnly": True,
            "untouchedHoldoutPct": 20,
            "walkForwardFolds": 4,
            "purgeBars": 25,
            "maxHoldBars": 24,
            "costs": {
                "spreadBps": 1.0,
                "commissionBps": 0.5,
                "slippageBps": 1.0,
                "swapBpsPerDay": 0.1,
                "adverseSameBarOrdering": True,
            },
        },
        "metrics": {
            "oosTrades": 80,
            "foldExpectancyR": [0.12, 0.09, -0.01, 0.11],
            "holdoutExpectancyR": 0.10,
            "holdoutTrades": 15,
            "expectancyR": 0.09,
            "profitFactor": 1.25,
            "sqn": 1.7,
            "maxDrawdownR": 8.0,
            "bootstrapLower95ExpectancyR": 0.01,
        },
        "pairAdapter": False,
        "profile": promoted_profile.to_dict(),
    }


def test_schema_three_artifact_binds_current_scorer_and_exact_profile():
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    route = route_specialist(pair)
    candidate = _candidate()
    decision = validation.validate_promotion_artifact(candidate, route, "intraday")
    assert decision.qualified is True
    assert decision.profile is not None
    assert decision.profile.profile_sha256 == candidate["profile"]["profileSha256"]

    candidate["profile"]["scorerSha256"] = "f" * 64
    decision = validation.validate_promotion_artifact(candidate, route, "intraday")
    assert decision.qualified is False
    assert "promotion_scorer_hash_mismatch" in decision.reasons

    candidate = _candidate()
    candidate["provenance"]["provider"] = "binance_rest"
    decision = validation.validate_promotion_artifact(candidate, route, "intraday")
    assert decision.qualified is False
    assert "promotion_provider_invalid" in decision.reasons


def test_family_candidate_requires_complete_declared_cohort(tmp_path):
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    route = route_specialist(pair)
    candidate = _candidate()
    candidate["validationScope"]["validatedSymbols"] = ["EURUSD"]
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    with pytest.raises(ValueError, match="promotion_cohort_incomplete"):
        promote_candidate(
            candidate_path,
            registry=PromotionRegistry(tmp_path / "registry"),
            pair=pair,
            expected_artifact_id=candidate["artifactId"],
            now=None,
        )

    assert route.family == "forex"
    assert not (tmp_path / "registry").exists()


def test_rejected_candidate_cannot_be_promoted(tmp_path):
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    candidate = _candidate(status="REJECTED")
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    with pytest.raises(ValueError, match="promotion_status_not_promoted"):
        promote_candidate(
            candidate_path,
            registry=PromotionRegistry(tmp_path / "registry"),
            pair=pair,
            expected_artifact_id=candidate["artifactId"],
        )


def test_invalid_or_stale_artifact_falls_back_to_unvalidated_only_in_demo(tmp_path):
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    route = route_specialist(pair)
    candidate = _candidate()
    candidate["validUntil"] = "2020-01-01T00:00:00+00:00"
    path = tmp_path / route.family / route.subclass / "intraday" / "promotion.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(candidate), encoding="utf-8")

    demo = PromotionRegistry(tmp_path, allow_demo_unvalidated=True).resolve(
        route, "intraday", symbol="EURUSD"
    )
    assert demo.qualified is True
    assert demo.profile.status == "UNVALIDATED"
    assert demo.profile.exit_policy == "SINGLE_TP1"
    assert "promotion_artifact_stale" in demo.reasons

    production = PromotionRegistry(tmp_path, allow_demo_unvalidated=False).resolve(
        route, "intraday", symbol="EURUSD"
    )
    assert production.qualified is False


def test_candidate_promotion_requires_exact_artifact_confirmation(tmp_path):
    pair = {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"}
    candidate = _candidate()
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")

    with pytest.raises(ValueError, match="promotion_confirmation_mismatch"):
        promote_candidate(
            candidate_path,
            registry=PromotionRegistry(tmp_path / "registry"),
            pair=pair,
            expected_artifact_id="wrong-artifact",
        )


def test_cohort_validation_pools_folds_and_promotes_atomically(tmp_path, monkeypatch):
    profitable = [0.3] * 8 + [-0.1] * 2
    results = iter(
        [
            ([profitable] * 4, profitable),
            ([profitable] * 4, profitable),
        ]
    )
    monkeypatch.setattr(validation, "_validation_results", lambda *args, **kwargs: next(results))
    candles = {
        "H1": [{"time": "2026-01-01", "open": 1, "high": 2, "low": 0.5, "close": 1.5}],
        "H4": [{"time": "2026-01-01", "open": 1, "high": 2, "low": 0.5, "close": 1.5}],
        "D1": [{"time": "2026-01-01", "open": 1, "high": 2, "low": 0.5, "close": 1.5}],
    }
    pairs = [
        {"display": "EUR/USD", "symbol": "EURUSD", "type": "forex"},
        {"display": "GBP/USD", "symbol": "GBPUSD", "type": "forex"},
    ]
    artifact = validation.validate_specialist_cohort(
        [{"pair": pair, "candles": candles} for pair in pairs],
        horizon="intraday",
        dataset_id="cohort-fixture",
        expected_symbols=["EURUSD", "GBPUSD"],
        spread_bps=1.0,
        commission_bps=0.5,
        slippage_bps=1.0,
        swap_bps_per_day=0.1,
        provider="mt5",
    )

    assert artifact["status"] == "PROMOTED"
    assert artifact["metrics"]["oosTrades"] == 80
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(artifact), encoding="utf-8")
    destination = promote_candidate(
        candidate_path,
        registry=PromotionRegistry(tmp_path / "registry"),
        pair=pairs[0],
        expected_artifact_id=artifact["artifactId"],
    )
    assert destination.exists()
    assert json.loads(destination.read_text(encoding="utf-8"))["artifactId"] == artifact["artifactId"]
