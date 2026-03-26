import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from meta_learner import (
    apply_meta_policy,
    compute_engine_quality,
    get_dynamic_engine_weights,
    get_engine_context,
    meta_report,
)


def test_meta_weights_stay_neutral_without_samples():
    signal = {
        "engine": "engine_a",
        "type": "crypto",
        "style": "intraday",
        "direction": "LONG",
        "combinedConviction": 0.62,
    }
    context = get_engine_context(signal, records=[])
    report = get_dynamic_engine_weights(context, records=[])

    assert round(sum(report["weights"].values()), 6) == 1.0
    assert report["weights"] == report["baseWeights"]
    assert report["suspensionFlags"]["engine_a"] is False
    assert report["engineQualities"]["engine_a"]["adaptive"] is False


def test_meta_weights_tilt_toward_better_recent_engine_with_step_cap():
    signal = {
        "engine": "engine_b",
        "type": "forex",
        "style": "intraday",
        "direction": "LONG",
        "confluenceScore": 4.0,
        "maxScore": 5.0,
    }
    records = []
    for _ in range(12):
        records.append({"engine": "engine_b", "won": True, "expectancy_r": 0.45, "slippage_bps": 1.0})
    for _ in range(12):
        records.append({"engine": "engine_a", "won": False, "expectancy_r": -0.25, "slippage_bps": 8.0})

    context = get_engine_context(signal, records=records)
    report = get_dynamic_engine_weights(context, records=records)

    assert report["weights"]["engine_b"] > report["baseWeights"]["engine_b"]
    assert report["weights"]["engine_a"] < report["baseWeights"]["engine_a"]
    assert abs(report["weights"]["engine_b"] - report["baseWeights"]["engine_b"]) <= 0.08


def test_meta_quality_can_suspend_clearly_degraded_bucket():
    signal = {
        "engine": "engine_a",
        "type": "crypto",
        "style": "intraday",
        "direction": "LONG",
        "combinedConviction": 0.51,
    }
    records = []
    for _ in range(14):
        records.append({"engine": "engine_a", "won": False, "expectancy_r": -0.35, "slippage_bps": 18.0})

    context = get_engine_context(signal, records=records)
    # Inject a weak SSI snapshot to emulate a degraded live bucket.
    context["recent_performance_by_engine"]["engine_a"]["ssi"] = 28.0
    quality = compute_engine_quality("engine_a", context, records=records)

    assert quality["adaptive"] is True
    assert quality["suspended"] is True
    assert quality["threshold_delta"] > 0


def test_apply_meta_policy_adds_controller_fields():
    signal = {
        "engine": "engine_c",
        "type": "crypto",
        "style": "intraday",
        "direction": "SHORT",
        "conviction": 0.58,
    }
    report = meta_report(signal, records=[])
    updated = apply_meta_policy(signal, report)

    assert "metaDynamicWeights" in updated
    assert "metaThresholdDelta" in updated
    assert "metaSuspended" in updated
