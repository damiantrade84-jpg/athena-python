"""Deterministic checks for research validation helpers (no athena import)."""

from research_validation import (
    ALLOWED_VALIDATION_MODES,
    backtest_bar_validation_state,
    build_validation_report,
    normalize_validation_mode,
    temporal_validation_mode,
)


def test_normalize_unknown_falls_back_to_standard():
    m, w = normalize_validation_mode("not_a_mode")
    assert m == "standard"
    assert w is not None


def test_temporal_live_parity_maps_to_standard():
    assert temporal_validation_mode("live_parity") == "standard"


def test_embargoed_skips_purge_band_only():
    min_bars, total = 10, 100
    base_oos = min_bars + int((total - min_bars) * 0.7)  # 73
    pg = 5
    for i in range(min_bars, total):
        st = backtest_bar_validation_state(
            i,
            min_bars=min_bars,
            total_bars=total,
            temporal_mode="embargoed",
            purge_gap=pg,
            folds=3,
        )
        purge_start = max(min_bars, base_oos - pg)
        if purge_start <= i < base_oos:
            assert st["skip"] is True
        else:
            assert st["skip"] is False
        assert st["oos_label"] == (i >= base_oos)


def test_walk_forward_fold_monotonic():
    min_bars, total, folds = 100, 400, 3
    folds_seen = set()
    for i in range(min_bars, total):
        st = backtest_bar_validation_state(
            i,
            min_bars=min_bars,
            total_bars=total,
            temporal_mode="walk_forward",
            purge_gap=10,
            folds=folds,
        )
        assert st["wf_fold"] is not None
        assert 0 <= st["wf_fold"] < folds
        folds_seen.add(st["wf_fold"])
    assert folds_seen == {0, 1, 2}


def test_build_validation_report_classifications():
    trades = [
        {"resultR": 1.0, "oos": False, "regime": "TRENDING"},
        {"resultR": -1.0, "oos": False, "regime": "TRENDING"},
        {"resultR": 0.5, "oos": True, "regime": "RANGING"},
    ]
    r = build_validation_report(
        trades,
        validation_mode="embargoed",
        temporal_mode="embargoed",
        purge_gap=20,
        folds=3,
        wf_split={"is_trades": 2, "oos_trades": 1},
    )
    assert r["isLeakageDefendedTemporal"] is True
    assert r["runClassification"] == "LEAKAGE_DEFENDED_VALIDATION"
    assert "TRENDING" in r["regimeSegmentation"]

    r2 = build_validation_report(
        trades,
        validation_mode="live_parity",
        temporal_mode="standard",
        purge_gap=0,
        folds=3,
    )
    assert r2["isLiveParityExecutionStress"] is True
    assert "ORDINARY" in r2["runClassification"] or "LIVE_PARITY" in r2["runClassification"]


def test_allowed_modes_documented():
    assert "standard" in ALLOWED_VALIDATION_MODES
    assert "walk_forward_cv" in ALLOWED_VALIDATION_MODES
