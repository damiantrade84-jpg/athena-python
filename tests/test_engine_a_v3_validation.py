from __future__ import annotations

import json

import pytest

from engine_a_v3.promotion import PromotionRegistry, promote_candidate
from engine_a_v3.routing import route_specialist
import engine_a_v3.validation as validation
from engine_a_v3.validation import (
    _promotion_passes,
    block_bootstrap_lower95,
    build_validation_windows,
    provenance_sha256,
)


def test_validation_uses_four_purged_folds_and_untouched_20_percent_holdout():
    folds, holdout = build_validation_windows(1_000, purge_bars=5)

    assert len(folds) == 4
    assert holdout.train_end == 795
    assert holdout.test_start == 800
    assert holdout.test_end == 1_000
    assert holdout.test_end - holdout.test_start == 200
    assert all(window.test_start - window.train_end == 5 for window in folds)
    assert all(left.test_end <= right.train_end for left, right in zip(folds, folds[1:]))


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
    return {
        "schemaVersion": 2,
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
            "sha256": "a" * 64,
            "implementationSha256": "b" * 64,
            "confirmedOnly": True,
            "untouchedHoldoutPct": 20,
            "walkForwardFolds": 4,
            "purgeBars": 5,
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
            "expectancyR": 0.09,
            "profitFactor": 1.25,
            "sqn": 1.7,
            "maxDrawdownR": 8.0,
            "bootstrapLower95ExpectancyR": 0.01,
        },
        "pairAdapter": False,
    }


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
