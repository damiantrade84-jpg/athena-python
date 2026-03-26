import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from calibration import calibration_report, fit_calibrator, predict_calibrated_prob


def test_fit_calibrator_builds_bucketed_empirical_rates():
    records = []
    for _ in range(10):
        records.append(
            {
                "engine": "engine_a",
                "asset_class": "crypto",
                "style": "intraday",
                "raw_score": 0.25,
                "won": False,
            }
        )
    for _ in range(10):
        records.append(
            {
                "engine": "engine_a",
                "asset_class": "crypto",
                "style": "intraday",
                "raw_score": 0.85,
                "won": True,
            }
        )

    calibrator = fit_calibrator(
        records,
        engine="engine_a",
        asset_class="crypto",
        style="intraday",
    )
    low_pred = predict_calibrated_prob(
        0.25,
        engine="engine_a",
        asset_class="crypto",
        style="intraday",
        calibrator=calibrator,
    )
    high_pred = predict_calibrated_prob(
        0.85,
        engine="engine_a",
        asset_class="crypto",
        style="intraday",
        calibrator=calibrator,
    )

    assert calibrator["available"] is True
    assert calibrator["usable_records"] == 20
    assert low_pred["calibrated_prob"] < high_pred["calibrated_prob"]
    assert high_pred["bucket_samples"] == 10


def test_predict_calibrated_prob_falls_back_to_broader_scope():
    records = []
    for idx in range(24):
        records.append(
            {
                "engine": "engine_a",
                "asset_class": "forex",
                "style": "swing",
                "raw_score": 0.75 if idx < 18 else 0.35,
                "won": idx < 18,
            }
        )
    for idx in range(2):
        records.append(
            {
                "engine": "engine_a",
                "asset_class": "forex",
                "style": "scalp",
                "raw_score": 0.8,
                "won": idx == 0,
            }
        )

    predicted = predict_calibrated_prob(
        0.8,
        engine="engine_a",
        asset_class="forex",
        style="scalp",
        records=records,
    )
    report = calibration_report(
        engine="engine_a",
        asset_class="forex",
        style="scalp",
        records=records,
    )

    assert predicted["available"] is True
    assert predicted["scope_used"] == {
        "engine": "engine_a",
        "asset_class": "forex",
        "style": None,
    }
    assert predicted["fallback_reason"] is not None
    assert report["scope_used"] == {
        "engine": "engine_a",
        "asset_class": "forex",
        "style": None,
    }
